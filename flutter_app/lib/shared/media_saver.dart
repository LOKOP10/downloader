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
