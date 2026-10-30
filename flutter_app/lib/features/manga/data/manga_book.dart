import 'dart:convert';
import 'dart:io';

/// 书架里的一本漫画。
///
/// 一本书对应磁盘上的一个产物：一个 .zip（禁漫一次下载的结果）或一个图片目录
/// （用户自己丢进来的）。路径就是它的唯一标识，不做额外索引。
class MangaBook {
  MangaBook({
    required this.path,
    required this.title,
    required this.pages,
    this.lastPage = 0,
    this.lastRead,
    this.addedAt,
    this.sourceId = '',
  });

  /// 绝对路径（.zip 或目录）。
  final String path;
  final String title;

  /// 总页数（zip 内图片数 / 目录内图片数）。
  final int pages;

  /// 上次读到的页序号（从 0 开始）。
  int lastPage;

  /// 上次阅读时间（ISO 字符串，null = 没读过）。
  String? lastRead;

  /// 入库时间（ISO 字符串）。
  final String? addedAt;

  /// 来源作品 id（禁漫车号），本地导入为空。
  final String sourceId;

  bool get isZip => path.toLowerCase().endsWith('.zip');

  /// 阅读进度（0~1）。
  double get progress => pages <= 0 ? 0 : (lastPage / pages).clamp(0, 1);

  Map<String, dynamic> toJson() => {
        'path': path,
        'title': title,
        'pages': pages,
        'last_page': lastPage,
        'last_read': lastRead,
        'added_at': addedAt,
        'source_id': sourceId,
      };

  static MangaBook? fromJson(Map<String, dynamic> json) {
    final path = (json['path'] ?? '').toString();
    if (path.isEmpty) return null;
    return MangaBook(
      path: path,
      title: (json['title'] ?? '未命名').toString(),
      pages: _asInt(json['pages']),
      lastPage: _asInt(json['last_page']),
      lastRead: json['last_read']?.toString(),
      addedAt: json['added_at']?.toString(),
      sourceId: (json['source_id'] ?? '').toString(),
    );
  }

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }

  @override
  String toString() => 'MangaBook(' + title + ', ' + pages.toString() + 'p)';
}

/// 书架排序方式。
enum MangaSort {
  recent('最近阅读'),
  added('加入时间'),
  title('标题'),
  progress('进度');

  const MangaSort(this.label);

  final String label;
}

/// 书架数据文件的读写（单文件 JSON，原子替换 + .bak 备份）。
abstract final class MangaStore {
  static Future<List<MangaBook>> load(File file) async {
    if (!await file.exists()) return [];
    try {
      final text = await file.readAsString();
      if (text.trim().isEmpty) return [];
      final json = jsonDecode(text);
      final list = json is Map ? json['books'] : json;
      if (list is! List) return [];
      final books = <MangaBook>[];
      for (final raw in list) {
        if (raw is! Map) continue;
        final book = MangaBook.fromJson(raw.cast<String, dynamic>());
        if (book != null) books.add(book);
      }
      return books;
    } catch (error) {
      // 主文件坏了就用备份，别让用户的书架凭空消失
      final backup = File(file.path + '.bak');
      if (await backup.exists()) {
        try {
          final json = jsonDecode(await backup.readAsString());
          final list = json is Map ? json['books'] : json;
          if (list is List) {
            return [
              for (final raw in list)
                if (raw is Map)
                  if (MangaBook.fromJson(raw.cast<String, dynamic>())
                      case final book?)
                    book,
            ];
          }
        } catch (_) {}
      }
      return [];
    }
  }

  static Future<void> save(File file, List<MangaBook> books) async {
    await file.parent.create(recursive: true);
    final text = jsonEncode({
      'version': 1,
      'books': [for (final book in books) book.toJson()],
    });
    // 先写临时文件再改名：中途被杀不会留下半个 JSON
    final temp = File(file.path + '.tmp');
    await temp.writeAsString(text, flush: true);
    if (await file.exists()) {
      final backup = File(file.path + '.bak');
      try {
        await backup.writeAsString(await file.readAsString(), flush: true);
      } catch (_) {}
    }
    await temp.rename(file.path);
  }
}
