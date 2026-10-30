import 'dart:io';
import 'dart:typed_data';

import 'package:path_provider/path_provider.dart';

/// 禁漫在线预览的**磁盘缓存**：存「已经切片重排过」的页字节。
///
/// 为什么需要：预览一页 = 一次图片请求 + 一次原生重排（解码整张位图再搬像素）。
/// 同一个本子反复打开、来回翻页，不缓存就等于每次都重新下 + 重新解码。
/// 页面字节进内存的是 LRU（24 页），这里再落一层磁盘，**退出 App 再进也能秒开**。
///
/// 边界：
///  · 只缓存重排后的成品（原图是乱序切片，存下来也不能直接看）；
///  · 目录在系统缓存目录下（`<cache>/jm_preview`），用户清 App 缓存会被一起清掉 —— 这是预期；
///  · 容量上限 [maxBytes]，超了按「最久没用的先删」清理，不需要用户操心。
abstract final class JmPreviewCache {
  /// 磁盘缓存上限：一本 103 页约 20MB，200MB 够放十来本常看的。
  static const int maxBytes = 200 * 1024 * 1024;

  /// 测试注入用：非空时不再去问 path_provider。
  static Directory? overrideDir;

  static Future<Directory> directory() async {
    final base = overrideDir ?? await _systemDir();
    if (!await base.exists()) await base.create(recursive: true);
    return base;
  }

  static Future<Directory> _systemDir() async {
    try {
      final tmp = await getTemporaryDirectory();
      return Directory(tmp.path + Platform.pathSeparator + 'jm_preview');
    } catch (_) {
      // 插件不可用（单元测试）时退回系统临时目录，绝不把异常抛给调用方
      return Directory(
          Directory.systemTemp.path + Platform.pathSeparator + 'jm_preview');
    }
  }

  /// 缓存文件名：`<章节id>_<原文件名>`，非法字符换成下划线。
  static String fileNameFor(String chapterId, String name) {
    final raw = chapterId + '_' + name;
    return raw.replaceAll(RegExp(r'[^0-9a-zA-Z._-]'), '_');
  }

  static Future<File> fileFor(String chapterId, String name) async {
    final dir = await directory();
    return File(dir.path + Platform.pathSeparator + fileNameFor(chapterId, name));
  }

  /// 取缓存；没有就返回 null。
  static Future<Uint8List?> get(String chapterId, String name) async {
    try {
      final file = await fileFor(chapterId, name);
      if (!await file.exists()) return null;
      final bytes = await file.readAsBytes();
      if (bytes.isEmpty) return null;
      // 顺手更新访问时间：LRU 清理按 mtime 挑最旧的，常看的不该被先删
      try {
        await file.setLastModified(DateTime.now());
      } catch (_) {}
      return bytes;
    } catch (_) {
      return null;
    }
  }

  /// 写入缓存（失败就算了，不影响预览）。
  static Future<void> put(
      String chapterId, String name, Uint8List bytes) async {
    if (bytes.isEmpty) return;
    try {
      final file = await fileFor(chapterId, name);
      await file.writeAsBytes(bytes, flush: true);
      await _trim();
    } catch (_) {}
  }

  /// 当前占用字节数（设置页显示用）。
  static Future<int> sizeBytes() async {
    try {
      final dir = await directory();
      var total = 0;
      await for (final entity in dir.list()) {
        if (entity is File) {
          try {
            total += await entity.length();
          } catch (_) {}
        }
      }
      return total;
    } catch (_) {
      return 0;
    }
  }

  /// 清空预览缓存，返回删掉的文件数。
  static Future<int> clear() async {
    var removed = 0;
    try {
      final dir = await directory();
      await for (final entity in dir.list()) {
        if (entity is! File) continue;
        try {
          await entity.delete();
          removed++;
        } catch (_) {}
      }
    } catch (_) {}
    return removed;
  }

  /// 超过 [maxBytes] 就按 mtime 从旧到新删，直到不超。
  static Future<void> _trim() async {
    try {
      final dir = await directory();
      final entries = <MapEntry<File, FileStat>>[];
      var total = 0;
      await for (final entity in dir.list()) {
        if (entity is! File) continue;
        try {
          final stat = await entity.stat();
          total += stat.size;
          entries.add(MapEntry(entity, stat));
        } catch (_) {}
      }
      if (total <= maxBytes) return;
      entries.sort((a, b) => a.value.modified.compareTo(b.value.modified));
      for (final entry in entries) {
        if (total <= maxBytes) break;
        try {
          await entry.key.delete();
          total -= entry.value.size;
        } catch (_) {}
      }
    } catch (_) {}
  }
}

/// 解析了新的链接之后，要不要清掉在线预览缓存。
///
/// 规则（用户要求）：**换本子、或解析的根本不是禁漫（别的站点）就清**；
/// 同一本重复解析保留缓存（否则刚加的磁盘缓存等于白加）。
bool shouldClearPreviewCache(String? albumId, String? lastAlbumId) {
  final current = (albumId ?? '').trim();
  final last = (lastAlbumId ?? '').trim();
  if (current.isEmpty) return true;
  return current != last;
}
