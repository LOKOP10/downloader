import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:vdl/features/manga/data/manga_book.dart';
import 'package:vdl/features/manga/data/manga_location.dart';

/// 本地漫画书架：单例，负责「扫描漫画目录 → 列表 → 进度 → 移除」。
///
/// 书架的真相在磁盘上（漫画目录里的 .zip 与图片文件夹），JSON 只存阅读进度等
/// 附加信息，所以用户直接往目录里丢文件也能出现在书架里。
class MangaLibrary extends ChangeNotifier {
  MangaLibrary._();

  static final MangaLibrary instance = MangaLibrary._();

  final List<MangaBook> books = [];

  bool _loaded = false;

  bool get loaded => _loaded;

  List<MangaBook> sorted(MangaSort sort, String keyword) {
    final key = keyword.trim();
    final list = key.isEmpty
        ? List<MangaBook>.from(books)
        : books.where((b) => b.title.contains(key)).toList();
    switch (sort) {
      case MangaSort.recent:
        list.sort((a, b) {
          final left = a.lastRead ?? a.addedAt ?? '';
          final right = b.lastRead ?? b.addedAt ?? '';
          return right.compareTo(left);
        });
      case MangaSort.added:
        list.sort((a, b) => (b.addedAt ?? '').compareTo(a.addedAt ?? ''));
      case MangaSort.title:
        list.sort((a, b) => a.title.compareTo(b.title));
      case MangaSort.progress:
        list.sort((a, b) => b.progress.compareTo(a.progress));
    }
    return list;
  }

  Future<File> _storeFile() async {
    try {
      final dir = await getApplicationSupportDirectory();
      return File(dir.path + Platform.pathSeparator + 'manga_library.json');
    } catch (error) {
      // 单元测试 / 插件不可用时退回临时目录，书架退化成空列表而不是崩掉
      return File(
          Directory.systemTemp.path + Platform.pathSeparator + 'manga_library.json');
    }
  }

  /// 载入：先读 JSON，再扫目录补新文件、剔除已删除的。
  Future<void> load() async {
    final file = await _storeFile();
    final saved = await MangaStore.load(file);
    final scanned = await _scan();
    // 扫描不到任何文件但书架里有记录：多半是漫画目录暂时不可读（未授权/被移动），
    // 这时候清空书架就等于「用户的书凭空消失」，所以先按记录显示。
    if (scanned.isEmpty && saved.isNotEmpty) {
      books
        ..clear()
        ..addAll(saved);
      _loaded = true;
      notifyListeners();
      return;
    }
    final byPath = <String, MangaBook>{
      for (final book in saved) book.path: book,
    };

    final merged = <MangaBook>[];
    for (final book in scanned) {
      final old = byPath.remove(book.path);
      if (old != null) {
        // 保留进度与入库时间，页数以磁盘为准（用户可能替换过文件）
        old.lastPage = old.lastPage.clamp(0, book.pages <= 0 ? old.lastPage : book.pages - 1);
        merged.add(MangaBook(
          path: old.path,
          title: old.title,
          pages: book.pages > 0 ? book.pages : old.pages,
          lastPage: old.lastPage,
          lastRead: old.lastRead,
          addedAt: old.addedAt,
          sourceId: old.sourceId,
        ));
      } else {
        merged.add(book);
      }
    }
    books
      ..clear()
      ..addAll(merged);
    _loaded = true;
    await _save();
    notifyListeners();
  }

  /// 扫漫画目录：.zip 与「直接放图片的子目录」都算一本书。
  Future<List<MangaBook>> _scan() async {
    final dir = Directory(await MangaLocation.current());
    if (!await dir.exists()) return [];
    final found = <MangaBook>[];
    await for (final entry in dir.list(followLinks: false)) {
      final name = entry.path.split(Platform.pathSeparator).last;
      if (entry is File) {
        if (!name.toLowerCase().endsWith('.zip')) continue;
        found.add(MangaBook(
          path: entry.path,
          title: _titleOf(name),
          pages: 0,
          addedAt: DateTime.now().toIso8601String(),
        ));
      } else if (entry is Directory) {
        final images = await _countImages(entry);
        if (images <= 0) continue;
        found.add(MangaBook(
          path: entry.path,
          title: name,
          pages: images,
          addedAt: DateTime.now().toIso8601String(),
        ));
      }
    }
    return found;
  }

  Future<int> _countImages(Directory dir) async {
    var count = 0;
    try {
      await for (final entity in dir.list(followLinks: false)) {
        if (entity is! File) continue;
        if (_isImage(entity.path)) count++;
      }
    } catch (_) {}
    return count;
  }

  static bool _isImage(String path) {
    final lower = path.toLowerCase();
    return lower.endsWith('.jpg') ||
        lower.endsWith('.jpeg') ||
        lower.endsWith('.png') ||
        lower.endsWith('.webp') ||
        lower.endsWith('.gif') ||
        lower.endsWith('.bmp');
  }

  static String _titleOf(String fileName) {
    final dot = fileName.lastIndexOf('.');
    return dot > 0 ? fileName.substring(0, dot) : fileName;
  }

  /// 新增/更新一本书（下载完成后调用）。
  Future<MangaBook> upsert({
    required String path,
    required String title,
    required int pages,
    String sourceId = '',
  }) async {
    // 重新下载后会覆盖同名 zip，旧的解压缓存必须丢掉
    invalidatePages(path);
    for (final existing in books) {
      if (existing.path != path) continue;
      existing.lastPage = 0;
      return existing;
    }
    final book = MangaBook(
      path: path,
      title: title,
      pages: pages,
      addedAt: DateTime.now().toIso8601String(),
      sourceId: sourceId,
    );
    books.insert(0, book);
    await _save();
    notifyListeners();
    return book;
  }

  /// 记录阅读进度。
  ///
  /// ⚠️ 必须**原地改字段、按 path 找书**。原来是 `books[index] = MangaBook(...)`
  /// 换了个新实例 —— 阅读器手里还攥着旧对象，从第二次翻页起
  /// `books.indexOf(book)` 就是 -1，进度再也写不回列表，于是「每次打开都停在第 2 页」
  /// （最后一次成功写入的是第一次翻页的页号）。`pages` 是 final，只有页数真变了
  /// 才需要替换实例，那时阅读器已经关了，不影响。
  Future<void> markRead(MangaBook book, int page, int pages) async {
    final readAt = DateTime.now().toIso8601String();
    applyProgress(books, book.path, page, readAt);
    book.lastPage = page;
    book.lastRead = readAt;
    if (pages > 0 && book.pages != pages) {
      final index = books.indexWhere((b) => b.path == book.path);
      if (index >= 0) {
        books[index] = MangaBook(
          path: book.path,
          title: book.title,
          pages: pages,
          lastPage: page,
          lastRead: readAt,
          addedAt: book.addedAt,
          sourceId: book.sourceId,
        );
      }
    }
    await _save();
    notifyListeners();
  }

  /// 从书架移除。[deleteFile] 为 true 时同时删除磁盘上的产物。
  Future<String> remove(MangaBook book, {required bool deleteFile}) async {
    books.removeWhere((b) => b.path == book.path);
    invalidatePages(book.path);
    await _save();
    notifyListeners();
    if (!deleteFile) return '已从书架移除（文件保留）';
    try {
      final type = FileSystemEntity.typeSync(book.path);
      if (type == FileSystemEntityType.file) {
        await File(book.path).delete();
        return '已移除，并删除文件';
      }
      if (type == FileSystemEntityType.directory) {
        // 目录是用户自己的东西，只移出书架、不递归删
        return '已从书架移除（目录未删除，避免误删你的文件）';
      }
      return '已从书架移除（文件已不在了）';
    } catch (error) {
      return '已从书架移除，但删文件失败：' + error.toString();
    }
  }

  Future<void> _save() async {
    try {
      final file = await _storeFile();
      await MangaStore.save(file, books);
    } catch (error) {
      debugPrint('书架保存失败：' + error.toString());
    }
  }

  /// 封面图缓存（path → 字节）。书架格子会反复 build，别每次都去解包。
  static final Map<String, Uint8List> _covers = {};

  /// 封面文件：与 zip 同名的 .cover.jpg（下载时就写好，读它最快）。
  static File coverFileFor(String path) {
    final dot = path.toLowerCase().endsWith('.zip')
        ? path.substring(0, path.length - 4)
        : path;
    return File(dot + '.cover.jpg');
  }

  /// 取一本书的封面字节；没有就返回 null（界面显示占位图标）。
  ///
  /// 顺序：同名 .cover.jpg → zip 里第一张图（顺便落地成 .cover.jpg，下次直接读）。
  Future<Uint8List?> cover(MangaBook book) async {
    final cached = _covers[book.path];
    if (cached != null) return cached;
    try {
      final coverFile = coverFileFor(book.path);
      if (await coverFile.exists()) {
        final bytes = await coverFile.readAsBytes();
        if (bytes.isNotEmpty) {
          _covers[book.path] = bytes;
          return bytes;
        }
      }
      if (book.isZip && await File(book.path).exists()) {
        final pages = await MangaZip.readPages(book.path);
        if (pages.isNotEmpty) {
          _covers[book.path] = pages.first.bytes;
          // 落地一份，下次开书架不用再解包
          try {
            await coverFile.writeAsBytes(pages.first.bytes, flush: true);
          } catch (_) {}
          return pages.first.bytes;
        }
      }
    } catch (error) {
      debugPrint('读取封面失败：' + error.toString());
    }
    return null;
  }

  /// 已解压的页面缓存（path → 页列表）。
  ///
  /// 一本 103 页的 zip 解出来约 20MB，以前**每次打开都重新读盘 + 整本解压**，
  /// 所以退出再进要等一两秒。这里留最近 [pageCacheMax] 本，来回打开不再解包。
  static final Map<String, List<MangaPageData>> _pageCache = {};
  static const int pageCacheMax = 2;

  /// 这本书的页面是否还缓存在内存里（书架「清理缓存」入口用它给反馈）。
  static bool hasPageCache(String path) => _pageCache.containsKey(path);

  /// 丢掉某本（或全部）的页面缓存：删书、重下、换文件后必须调。
  ///
  /// 连封面内存缓存一起丢：重下/换文件后封面可能变了，留着旧图会串。
  static void invalidatePages(String? path) {
    if (path == null) {
      _pageCache.clear();
      _covers.clear();
    } else {
      _pageCache.remove(path);
      _covers.remove(path);
    }
  }

  /// 供阅读器调用：打开一本，返回按自然序排好的图片字节。
  Future<List<MangaPageData>> openPages(MangaBook book) async {
    final cached = _pageCache[book.path];
    if (cached != null && cached.isNotEmpty) {
      // 命中就提到最新（Map 的插入序就是 LRU 序）
      _pageCache.remove(book.path);
      _pageCache[book.path] = cached;
      return cached;
    }
    final pages = await _openPagesUncached(book);
    if (pages.isNotEmpty) {
      _pageCache[book.path] = pages;
      while (_pageCache.length > pageCacheMax) {
        _pageCache.remove(_pageCache.keys.first);
      }
    }
    return pages;
  }

  Future<List<MangaPageData>> _openPagesUncached(MangaBook book) async {
    if (book.isZip) {
      return MangaZip.readPages(book.path);
    }
    final dir = Directory(book.path);
    if (!await dir.exists()) return [];
    final files = <File>[];
    await for (final entity in dir.list(followLinks: false)) {
      if (entity is File && _isImage(entity.path)) files.add(entity);
    }
    files.sort((a, b) => naturalCompare(a.path, b.path));
    return [
      for (final file in files)
        MangaPageData(name: file.uri.pathSegments.last, bytes: await file.readAsBytes()),
    ];
  }
}

/// 在书架列表里按 path 找到那本，**原地**更新进度（纯函数，便于离线测试）。
///
/// 用 path 而不是对象引用：阅读器持有的可能是加载时的实例，而 `load()` / `upsert()`
/// 期间列表里的实例可能被换过。返回 true 表示找到了。
bool applyProgress(List<MangaBook> books, String path, int page, String readAt) {
  for (final book in books) {
    if (book.path != path) continue;
    book.lastPage = page;
    book.lastRead = readAt;
    return true;
  }
  return false;
}

/// 一页的数据（名字 + 字节），阅读器直接喂给 Image.memory。
class MangaPageData {
  const MangaPageData({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

/// zip 读取与自然排序（书架与阅读器共用）。
abstract final class MangaZip {
  static Future<List<MangaPageData>> readPages(String path) async {
    final bytes = await File(path).readAsBytes();
    final archive = ZipDecoder().decodeBytes(bytes);
    final entries = <ArchiveFile>[];
    for (final file in archive) {
      final name = file.name.toLowerCase();
      if (!file.isFile) continue;
      if (name.endsWith('.jpg') ||
          name.endsWith('.jpeg') ||
          name.endsWith('.png') ||
          name.endsWith('.webp') ||
          name.endsWith('.gif')) {
        entries.add(file);
      }
    }
    entries.sort((a, b) => naturalCompare(a.name, b.name));
    return [
      for (final entry in entries)
        MangaPageData(
          name: entry.name,
          bytes: Uint8List.fromList(entry.content as List<int>),
        ),
    ];
  }
}

/// 自然排序：00002 排在 00010 前面，1.jpg 排在 10.jpg 前面。
int naturalCompare(String left, String right) {
  final a = _naturalParts(left);
  final b = _naturalParts(right);
  final length = a.length < b.length ? a.length : b.length;
  for (var i = 0; i < length; i++) {
    final one = a[i];
    final two = b[i];
    final oneNum = int.tryParse(one);
    final twoNum = int.tryParse(two);
    int result;
    if (oneNum != null && twoNum != null) {
      result = oneNum.compareTo(twoNum);
    } else {
      result = one.compareTo(two);
    }
    if (result != 0) return result;
  }
  return a.length.compareTo(b.length);
}

List<String> _naturalParts(String text) {
  final parts = <String>[];
  final buffer = StringBuffer();
  var lastDigit = false;
  for (final unit in text.codeUnits) {
    final isDigit = unit >= 48 && unit <= 57;
    if (buffer.isNotEmpty && isDigit != lastDigit) {
      parts.add(buffer.toString());
      buffer.clear();
    }
    buffer.writeCharCode(unit);
    lastDigit = isDigit;
  }
  if (buffer.isNotEmpty) parts.add(buffer.toString());
  return parts;
}
