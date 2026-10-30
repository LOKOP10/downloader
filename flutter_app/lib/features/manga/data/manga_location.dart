import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';
import 'package:vdl/features/download/data/download_location.dart';

/// 漫画压缩包保存位置：**与普通视频下载目录分开**，可以单独设置。
///
/// 默认落在「下载目录/漫画」，用户改过后以设置为准；校验与规范化直接复用
/// DownloadLocation 那一套（试写 + 权限提示），避免两处逻辑跑偏。
abstract final class MangaLocation {
  static const _key = 'manga.dir';

  /// 默认目录：下载目录下的「漫画」子目录。
  static Future<String> defaultDir() async {
    final base = await DownloadLocation.current();
    return base + Platform.pathSeparator + '漫画';
  }

  /// 当前生效目录（用户设置优先）。
  static Future<String> current() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(_key);
      if (saved != null && saved.trim().isNotEmpty) return saved.trim();
    } catch (error) {
      // 读不到就用默认，绝不把异常丢给调用方
    }
    return defaultDir();
  }

  static Future<void> save(String path) async {
    final prefs = await SharedPreferences.getInstance();
    final trimmed = DownloadLocation.normalizePath(path);
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

  /// 目录是否被用户单独设置过（设置页用来区分「默认」与「自定义」）。
  static Future<bool> isCustom() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(_key);
      return saved != null && saved.trim().isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  /// 确保目录存在并返回路径。
  static Future<String> ensure() async {
    final path = await current();
    await Directory(path).create(recursive: true);
    return path;
  }
}
