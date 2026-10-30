import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_preview_cache.dart';
import 'package:vdl/features/manga/data/jm_scramble.dart';
import 'package:vdl/features/manga/data/manga_book.dart';
import 'package:vdl/features/manga/data/manga_library.dart';
import 'package:vdl/features/manga/data/manga_location.dart';
import 'package:vdl/shared/site.dart';

/// 禁漫下载：逐章逐页下载 → 原生重排 → 打包成 zip → 进书架。
///
/// 并发策略照旧版 Kotlin（JmDownloader）：默认 8 路并发抓图、**按页序写包**。
///
/// ⚠️ 2026-10 修过的两个真缺陷：
///  1. **静默丢页**：某页下载失败时原来是 `results[index] = null` 然后打包时
///     `if (bytes == null) continue` —— 于是「解析 103 页、下完只有 59 页」，
///     且界面只显示成功（用户要翻到最后才发现）。现在每页**重试**（换 CDN 域名），
///     仍失败就**整本报错不落盘**，绝不再产出残缺压缩包。
///  2. **空章节静默消失**：某章一张图都没取到时原来只是记一笔，多章本子会凭空
///     少一章。现在直接报错说明是哪一章。
class JmDownloader {
  JmDownloader._();

  static final JmDownloader instance = JmDownloader._();

  static const MethodChannel _channel = MethodChannel('vdl/jmdescramble');

  /// 同时抓几页。禁漫图片站对 8 路以内基本不拦（旧版实测值）。
  static const int concurrency = 8;

  /// 取章节目录的并发（多章本子不必一章章等）。
  static const int planConcurrency = 4;

  /// 单页最多试几轮（每轮内部还会轮换 6 个图片域名）。
  static const int pageAttempts = 3;

  /// 预览时同时下载几页，别把 CDN 打爆。
  static const int previewConcurrency = 3;

  /// scramble_id 逐章可能不同，同一章反复取没意义 —— 缓存（预览与下载共用）。
  static final Map<String, String> _scrambleIds = <String, String>{};

  bool _running = false;
  bool _cancelled = false;

  bool get running => _running;

  /// 下载整本（含所有章节）。[onProgress] 参数是 (已完成页, 总页, 状态文字)。
  Future<MangaBook> downloadAlbum(
    JmAlbum album, {
    void Function(int done, int total, String status)? onProgress,
  }) async {
    if (_running) throw JmException('已经有一本正在下载，请等它完成');
    _running = true;
    _cancelled = false;
    final title = album.name.isEmpty ? 'JM' + album.id : album.name;
    // 登记到任务页：用户能在「任务」里看到这本的进度，也能在那里取消
    final job = TaskStore.instance.startExternal(
      title: title,
      platform: SitePlatform.jm,
      onCancel: () => _cancelled = true,
    );
    try {
      final plan = await allPages(album, onProgress: onProgress);
      if (plan.isEmpty) {
        throw JmException('没有取到任何页（可能章节被限制或未取到图片）');
      }
      final pages = plan;

      // 8 路并发抓图，结果按原序号回填 —— 写包顺序与页面顺序严格一致
      final results = List<Uint8List?>.filled(pages.length, null);
      final failed = <int>[];
      var cursor = 0;
      var done = 0;

      Future<void> worker() async {
        while (true) {
          final index = cursor;
          cursor++;
          if (index >= pages.length) return;
          if (_cancelled) return;
          final page = pages[index];
          try {
            results[index] = await _fetchPage(page, index);
          } catch (error) {
            failed.add(index);
            debugPrint('第 ' + (index + 1).toString() + ' 页失败（已重试）：' + error.toString());
          }
          done++;
          onProgress?.call(done, pages.length,
              '已完成 ' + done.toString() + '/' + pages.length.toString() + ' 页');
          TaskStore.instance.reportExternal(job, done: done, total: pages.length);
        }
      }

      onProgress?.call(0, pages.length, '开始下载（' + concurrency.toString() + ' 路并发）');
      await Future.wait([
        for (var i = 0; i < concurrency; i++) worker(),
      ]);

      if (_cancelled) {
        TaskStore.instance.finishExternal(job, error: '已取消');
        throw JmException('已取消');
      }

      // ⚠️ 有任何一页没下来就不打包：残缺的压缩包比失败更糟（用户要翻到最后才发现）
      if (failed.isNotEmpty) {
        throw JmException(missingPagesMessage(failed));
      }

      final archive = Archive();
      for (var i = 0; i < results.length; i++) {
        final bytes = results[i];
        if (bytes == null) continue;
        final name = (i + 1).toString().padLeft(5, '0') + '.jpg';
        archive.addFile(ArchiveFile(name, bytes.length, bytes));
      }
      if (archive.isEmpty) throw JmException('所有页都下载失败，没有可打包的内容');

      final zipBytes = ZipEncoder().encode(archive);
      if (zipBytes.isEmpty) {
        throw JmException('打包失败（压缩包为空）');
      }
      final dir = await MangaLocation.ensure();
      final file = File(dir + Platform.pathSeparator + safeFileName(title) + '.zip');
      await file.writeAsBytes(zipBytes, flush: true);

      // 顺手把第一页存成同名 .cover.jpg：书架不用解包就能显示封面
      try {
        for (var i = 0; i < results.length; i++) {
          final bytes = results[i];
          if (bytes == null) continue;
          await MangaLibrary.coverFileFor(file.path).writeAsBytes(bytes, flush: true);
          break;
        }
      } catch (error) {
        debugPrint('写封面失败（不影响下载）：' + error.toString());
      }

      final book = await MangaLibrary.instance.upsert(
        path: file.path,
        title: title,
        pages: archive.length,
        sourceId: album.id,
      );

      // 对账：接口页数（total_photos）与实际落盘张数比一次，不一致要说清楚
      final expected = album.totalPhotos;
      final actual = archive.length;
      if (expected > 0 && actual < expected) {
        onProgress?.call(actual, pages.length,
            '注意：实际只下到 ' + actual.toString() + ' 张，比接口的 ' +
                expected.toString() + ' 张少（接口页数可能不准，或本子中间缺页）');
      } else {
        onProgress?.call(actual, pages.length, '完成（' + actual.toString() + ' 页）');
      }
      TaskStore.instance.finishExternal(job);
      return book;
    } catch (error) {
      TaskStore.instance.finishExternal(job, error: error.toString());
      rethrow;
    } finally {
      _running = false;
    }
  }

  /// 取整本的页清单（含章节归属）：只发章节请求，不下载图片。
  ///
  /// 预览与下载共用这一份「目录」，保证两边页数一致。
  Future<List<JmPageRef>> allPages(
    JmAlbum album, {
    void Function(int done, int total, String status)? onProgress,
  }) async {
    final pages = <JmPageRef>[];
    final missing = <String>[];
    final names = List<List<String>?>.filled(album.chapters.length, null);

    // 章节目录并发拉（4 路），失败记录下来，最后统一报
    var index = 0;
    Future<void> work() async {
      while (true) {
        final i = index;
        index++;
        if (i >= album.chapters.length) return;
        final chapter = album.chapters[i];
        try {
          final list = await JmClient.instance.chapterImages(chapter.id);
          names[i] = list;
        } catch (error) {
          missing.add(chapter.label + '（' + error.toString() + '）');
        }
      }
    }
    onProgress?.call(0, 0, '正在获取目录…');
    await Future.wait([for (var i = 0; i < planConcurrency; i++) work()]);

    for (var i = 0; i < album.chapters.length; i++) {
      final chapter = album.chapters[i];
      final list = names[i];
      if (list == null || list.isEmpty) {
        if (!missing.any((m) => m.startsWith(chapter.label))) {
          missing.add(chapter.label + '（该章没有图片）');
        }
        continue;
      }
      final scrambleId = await _scrambleOf(chapter.id);
      for (final name in list) {
        pages.add(JmPageRef(
          chapterId: chapter.id,
          name: name,
          scrambleId: scrambleId,
        ));
      }
    }
    if (missing.isNotEmpty) {
      throw JmException('没有取到完整目录：' + missing.join('，'));
    }
    onProgress?.call(0, pages.length, '目录就绪，共 ' + pages.length.toString() + ' 页');
    return pages;
  }

  /// 取某一页的图片字节（已重排），供预览使用。
  ///
  /// 两级缓存：内存 LRU 在预览页里，这里先查**磁盘**（重排后的成品），
  /// 命中就彻底不下载、不解码 —— 退出 App 再进也能秒开。
  Future<Uint8List> pageBytes(JmPageRef page, {int index = 0}) async {
    final cached = await JmPreviewCache.get(page.chapterId, page.name);
    if (cached != null) return cached;
    final bytes = await _fetchPage(page, index);
    await JmPreviewCache.put(page.chapterId, page.name, bytes);
    return bytes;
  }

  /// 取一页：内部最多试 [pageAttempts] 轮，每轮轮换图片 CDN 域名起点。
  Future<Uint8List> _fetchPage(JmPageRef page, int index) async {
    Object? lastError;
    for (var attempt = 0; attempt < pageAttempts; attempt++) {
      if (_cancelled) throw JmException('已取消');
      try {
        final raw = await JmClient.instance.fetchImage(
          page.chapterId,
          page.name,
          startIndex: index + attempt * 2,
        );
        return await _descramble(raw, page);
      } catch (error) {
        lastError = error;
        // 退避一下再试：连续失败多半是被 CDN 限流，等一会儿能恢复
        await Future<void>.delayed(Duration(milliseconds: 250 * (attempt + 1)));
      }
    }
    throw JmException('图片下载失败（' + lastError.toString() + '）');
  }

  Future<String> _scrambleOf(String chapterId) async {
    final cached = _scrambleIds[chapterId];
    if (cached != null) return cached;
    final value = await JmClient.instance.scrambleId(chapterId);
    _scrambleIds[chapterId] = value;
    return value;
  }

  /// 调原生重排；num <= 0（老图没切片）或原生不可用时原样返回。
  Future<Uint8List> _descramble(Uint8List raw, JmPageRef page) async {
    final num = JmScramble.segmentationNum(
      page.scrambleId,
      page.chapterId,
      page.name,
    );
    if (num <= 0) return raw;
    if (!Platform.isAndroid) return raw;
    File? tempFile;
    try {
      final dir = Directory(
          Directory.systemTemp.path + Platform.pathSeparator + '_jm_raw');
      await dir.create(recursive: true);
      tempFile = File(dir.path +
          Platform.pathSeparator +
          DateTime.now().microsecondsSinceEpoch.toString() +
          '_' +
          page.name.replaceAll(RegExp(r'[^0-9a-zA-Z]'), '') +
          '.bin');
      await tempFile.writeAsBytes(raw, flush: true);
      final result = await _channel.invokeMethod<Uint8List>('descramble', {
        'path': tempFile.path,
        'num': num,
      });
      return result ?? raw;
    } catch (error) {
      debugPrint('图片重排失败，按原图保存：' + error.toString());
      return raw;
    } finally {
      try {
        if (tempFile != null && await tempFile.exists()) await tempFile.delete();
      } catch (_) {}
    }
  }
}

/// 一页的任务描述：章节 id + 文件名 + 该章的 scramble_id。
///
/// 公开给预览使用（预览与下载共用同一份页清单，避免两边页数对不上）。
class JmPageRef {
  const JmPageRef({
    required this.chapterId,
    required this.name,
    required this.scrambleId,
  });

  final String chapterId;
  final String name;
  final String scrambleId;
}

/// 有页没下下来时的提示文案（纯函数，便于离线测试）。
///
/// 只列前 20 个页号：一次性刷出 44 个数字反而看不清重点。
String missingPagesMessage(List<int> failed, {int maxShown = 20}) {
  final pages = [...failed]..sort();
  final shown = pages.take(maxShown).map((i) => (i + 1).toString()).join('、');
  final tail = pages.length > maxShown ? ' 等' : '';
  return '有 ' + pages.length.toString() + ' 页没下载成功（第 ' + shown + tail +
      ' 页），已中止，未生成残缺压缩包。请重试一次';
}

/// 文件名清洗：去掉路径分隔符等非法字符。
String safeFileName(String raw) {
  var name = raw.trim();
  name = name.replaceAll(RegExp(r'[\\/:*?"<>|\r\n\t]'), ' ');
  name = name.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (name.length > 80) name = name.substring(0, 80).trim();
  if (name.isEmpty) name = 'jm_' + DateTime.now().millisecondsSinceEpoch.toString();
  return name;
}
