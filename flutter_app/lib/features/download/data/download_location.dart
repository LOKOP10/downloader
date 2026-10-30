import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 下载保存位置：可在设置里改，默认落在应用外部目录（免存储权限）。
class DownloadLocation {
  static const _key = 'download.dir';

  /// 默认目录：Android 取应用外部目录（Android/data/<包名>/files/Downloads），
  /// 其它平台取应用支持目录。
  static Future<String> defaultDir() async {
    try {
      Directory base;
      if (Platform.isAndroid) {
        base = await getExternalStorageDirectory() ??
            await getApplicationSupportDirectory();
      } else {
        base = await getApplicationSupportDirectory();
      }
      return '${base.path}${Platform.pathSeparator}Downloads';
    } catch (error) {
      // 插件不可用（单元测试环境）或平台不支持时给个确定路径，绝不把异常抛给调用方
      debugPrint('默认下载目录取不到，回退临时目录：$error');
      return '${Directory.systemTemp.path}${Platform.pathSeparator}vdl-downloads';
    }
  }

  /// 当前生效目录（用户设置优先，未设置则用默认）。
  static Future<String> current() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(_key);
      if (saved != null && saved.trim().isNotEmpty) return saved.trim();
    } catch (error) {
      debugPrint('读取下载目录失败：$error');
    }
    return defaultDir();
  }

  /// 保存目录（空值 = 恢复默认）。
  static Future<void> save(String path) async {
    final prefs = await SharedPreferences.getInstance();
    final trimmed = path.trim();
    if (trimmed.isEmpty) {
      await prefs.remove(_key);
    } else {
      await prefs.setString(_key, trimmed);
    }
  }

  static Future<void> reset() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }

  /// 规范化路径：补前导斜杠、合并重复斜杠、去掉结尾斜杠。
  ///
  /// 用户手输常见两种错法：漏了开头的 "/"（storage/emulated/0/...），
  /// 或多打了结尾的 "/"。这里统一修正，避免把畸形路径存进去。
  static String normalizePath(String raw) {
    var path = raw.trim();
    if (path.isEmpty) return path;
    if (!path.startsWith('/') &&
        (path.startsWith('storage/') || path.startsWith('sdcard/'))) {
      path = '/$path';
    }
    path = path.replaceAll(RegExp(r'/+'), '/');
    if (path.length > 1 && path.endsWith('/')) {
      path = path.substring(0, path.length - 1);
    }
    return path;
  }

  /// 系统公共存储下的一级「标准分类目录」（相册 / 文件管理器按它们归档）。
  ///
  /// Android 的相册只扫这些目录里的媒体文件；自己随手建一个 `Picture`
  /// （少个 s）在里面放视频，相册与系统搜索都找不到 —— 这是用户真机上踩到的。
  static const standardPublicDirs = <String, String>{
    'pictures': 'Pictures',
    'movies': 'Movies',
    'dcim': 'DCIM',
    'download': 'Download',
    'downloads': 'Download',
    'documents': 'Documents',
    'music': 'Music',
    'podcasts': 'Podcasts',
    'ringtones': 'Ringtones',
    'alarms': 'Alarms',
    'notifications': 'Notifications',
  };

  /// 用户把公共目录写成了标准目录的近似拼写时，给一句提示（**不改他的输入**）。
  ///
  /// 例：`/storage/emulated/0/Picture` → 提示应该是 `Pictures`。
  static String? suggestStandardDir(String rawPath) {
    final path = normalizePath(rawPath);
    const root = '/storage/emulated/0/';
    if (!path.startsWith(root)) return null;
    final rest = path.substring(root.length);
    if (rest.isEmpty) return null;
    final first = rest.split('/').first;
    if (first.isEmpty) return null;
    final lower = first.toLowerCase();
    // 先按原样查，再补一个单复数近似：Picture → Pictures、Document → Documents
    final standard = standardPublicDirs[lower] ?? standardPublicDirs['${lower}s'];
    if (standard == null) return null;
    if (standard == first) return null;
    return '系统里这个目录叫 $standard（注意大小写 / 单复数），你填的是 $first。'
        '相册和系统搜索只认标准目录名，建议改成 $root$standard 或 $root$standard/视频下载器。';
  }

  /// 是否已拿到「所有文件访问」权限（Android 11+ 写公共目录需要）。
  static Future<bool> hasAllFilesAccess() async {
    if (!Platform.isAndroid) return true;
    try {
      return await Permission.manageExternalStorage.isGranted;
    } catch (_) {
      return false;
    }
  }

  /// 申请「所有文件访问」：会跳到系统设置页，用户手动开启后返回。
  static Future<bool> requestAllFilesAccess() async {
    if (!Platform.isAndroid) return true;
    try {
      final status = await Permission.manageExternalStorage.request();
      if (status.isGranted) return true;
      if (status.isPermanentlyDenied) await openAppSettings();
      return status.isGranted;
    } catch (error) {
      debugPrint('申请所有文件访问失败：$error');
      return false;
    }
  }

  /// 校验目录是否可用：能建出来、能写文件。
  ///
  /// 返回 null 表示可用，否则返回给人看的原因。写测试文件后立即删掉，
  /// 不留垃圾。
  static Future<String?> validate(String rawPath) async {
    final trimmed = normalizePath(rawPath);
    if (trimmed.isEmpty) return '目录不能为空';
    if (!(Platform.isAndroid || Platform.isWindows || Platform.isLinux || Platform.isMacOS)) {
      return '当前平台不支持自定义目录';
    }
    if (Platform.isAndroid &&
        !(trimmed.startsWith('/storage/') || trimmed.startsWith('/sdcard/'))) {
      return 'Android 上请使用 /storage/emulated/0/... 这类路径';
    }
    final dir = Directory(trimmed);
    try {
      if (!await dir.exists()) {
        await dir.create(recursive: true);
      }
      final probe = File('${dir.path}${Platform.pathSeparator}.vdl_write_test');
      await probe.writeAsString('ok', flush: true);
      await probe.delete();
      return null;
    } on FileSystemException catch (error) {
      final reason = error.osError?.message ?? error.message;
      if (reason.contains('EACCES') ||
          reason.contains('EPERM') ||
          reason.contains('Permission denied') ||
          reason.contains('Operation not permitted')) {
        return '需要「所有文件访问」权限才能写这个目录（Android 11+ 分区存储限制），'
            '点下面的「去授权」开启后再保存；也可以点「用默认目录」存到应用目录';
      }
      return '无法写入：$reason';
    } catch (error) {
      return '无法写入：$error';
    }
  }
}
