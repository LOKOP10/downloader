import 'dart:io';

/// 桌面端没有系统相册：登记动作一律空实现，文件落在哪就是哪。
abstract final class MediaSaver {
  static const String gallerySubDir = '视频下载器';

  static Future<void> scanToGallery(String path) async {}

  static bool isPublicMediaPath(String path) => false;

  static Future<int> scanDirectoryToGallery(String dirPath) async => 0;

  static bool isMediaFile(String path) {
    final lower = path.toLowerCase();
    for (final ext in <String>[
      '.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.ts', '.wmv',
      '.m4v', '.mpg', '.mpeg',
    ]) {
      if (lower.endsWith(ext)) return true;
    }
    return false;
  }

  /// 桌面端直接返回原路径（不复制到相册）。
  static Future<String> saveImageToGallery({
    required List<int> bytes,
    required String fileName,
    String? subDir,
  }) async {
    final dir = Directory.systemTemp;
    final file = File('${dir.path}${Platform.pathSeparator}$fileName');
    await file.writeAsBytes(bytes);
    return file.path;
  }
}
