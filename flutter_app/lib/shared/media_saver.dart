import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:vdl/features/download/data/download_location.dart';

/// 把一张图存进**系统相册**。
///
/// Android 上不能只写文件：图库只认登记进 MediaStore 的图片，直接写
/// `/storage/emulated/0/Pictures` 即使成功，相册与系统搜索也扫不到
/// （看起来就像「保存失败」）。所以这里落临时文件 → 交原生
/// `vdl/media` 通道插入 MediaStore（`Pictures/视频下载器/文件名`）→ 删临时文件。
///
/// 非 Android（桌面实测）直接写到「视频 / 图片保存位置」，不搞通道。
abstract final class MediaSaver {
  static const MethodChannel _channel = MethodChannel('vdl/media');

  /// 相册里的落点（相对系统公共目录）。
  static const String gallerySubDir = 'Pictures/视频下载器';

  /// 下载完成后把成品交给系统媒体库收录（QQ / 微信的「最近图片」只查 MediaStore）。
  ///
  /// 只对**公共目录**里的文件有意义：应用私有目录（Android/data/...）别的 App 本来就读不到。
  static Future<void> scanToGallery(String path) async {
    if (!Platform.isAndroid) return;
    if (!isPublicMediaPath(path)) return;
    try {
      await _channel.invokeMethod<int>('scanFiles', {
        'paths': <String>[path],
      });
    } catch (_) {
      // 扫描失败不影响下载结果，静默忽略
    }
  }

  /// 是否在系统公共存储里（排除应用私有目录）。
  static bool isPublicMediaPath(String path) {
    if (!path.startsWith('/storage/emulated/')) return false;
    if (path.contains('/Android/data/') || path.contains('/Android/obb/')) {
      return false;
    }
    return true;
  }

  /// 把整个目录里的媒体文件重新登记进媒体库。
  ///
  /// 用途：以前下好的文件（写盘时没登记）在 QQ / 微信的「最近图片」里看不到，
  /// 不用重新下载，跑一次这个就行。只扫公共目录、只扫一层（下载产物是平铺的）。
  static Future<int> scanDirectoryToGallery(String dirPath) async {
    if (!Platform.isAndroid) return 0;
    if (!isPublicMediaPath(dirPath)) return 0;
    final dir = Directory(dirPath);
    if (!await dir.exists()) return 0;
    final paths = <String>[];
    try {
      await for (final entity in dir.list(followLinks: false)) {
        if (entity is File && isMediaFile(entity.path)) paths.add(entity.path);
      }
    } catch (_) {
      return 0;
    }
    if (paths.isEmpty) return 0;
    var total = 0;
    // 分批送，别一次把几百个路径塞给 MediaScanner
    for (var i = 0; i < paths.length; i += 100) {
      final end = (i + 100) < paths.length ? i + 100 : paths.length;
      try {
        total += await _channel.invokeMethod<int>('scanFiles', {
              'paths': paths.sublist(i, end),
            }) ??
            0;
      } catch (_) {
        break;
      }
    }
    return total;
  }

  /// 是否是需要登记进媒体库的媒体文件（视频 / 图片）。
  ///
  /// 隐藏文件（以 `.` 开头）与书架封面（`.cover.jpg`）一律排除：
  /// 封面是我们自己的缓存，进相册只会变成一堆莫名其妙的图。
  static bool isMediaFile(String path) {
    final lower = path.toLowerCase();
    final name = lower.split(RegExp(r'[\\/]')).last;
    if (name.startsWith('.')) return false;
    if (name.contains('.cover.')) return false;
    const media = [
      '.mp4', '.mkv', '.webm', '.mov', '.m4v', '.ts', '.flv',
      '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
    ];
    return media.any(lower.endsWith);
  }

  /// 保存一张图，返回给人看的位置。
  ///
  /// Android：`Pictures/视频下载器/<文件名>`；其它平台：绝对路径。
  static Future<String> saveImageToGallery({
    required Uint8List bytes,
    required String displayName,
  }) async {
    if (bytes.isEmpty) throw Exception('这一页没有内容');
    if (!Platform.isAndroid) {
      final dir = await DownloadLocation.current();
      await Directory(dir).create(recursive: true);
      final file = File(dir + Platform.pathSeparator + displayName);
      await file.writeAsBytes(bytes, flush: true);
      return file.path;
    }
    final tmpDir = Directory(
        Directory.systemTemp.path + Platform.pathSeparator + '_vdl_gallery');
    await tmpDir.create(recursive: true);
    final tmp = File(tmpDir.path + Platform.pathSeparator + displayName);
    await tmp.writeAsBytes(bytes, flush: true);
    try {
      final saved = await _channel.invokeMethod<String>('saveImage', {
        'path': tmp.path,
        'name': displayName,
        'relativeDir': gallerySubDir,
      });
      if (saved == null || saved.trim().isEmpty) {
        return gallerySubDir + '/' + displayName;
      }
      return saved;
    } on PlatformException catch (error) {
      throw Exception(error.message ?? error.code);
    } finally {
      try {
        if (await tmp.exists()) await tmp.delete();
      } catch (_) {}
    }
  }
}
