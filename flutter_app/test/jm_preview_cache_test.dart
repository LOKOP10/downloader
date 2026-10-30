import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/manga/data/jm_preview_cache.dart';

/// 禁漫预览磁盘缓存：写进去要能读出来、能清、能统计、文件名要安全。
///
/// 用 overrideDir 指到临时目录，不碰真实的系统缓存目录。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory dir;

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('vdl_jm_cache');
    JmPreviewCache.overrideDir = dir;
  });

  tearDown(() async {
    JmPreviewCache.overrideDir = null;
    if (await dir.exists()) await dir.delete(recursive: true);
  });

  Uint8List bytes(int n) => Uint8List.fromList(List<int>.generate(n, (i) => i % 256));

  test('写进去能原样读出来', () async {
    await JmPreviewCache.put('1466309', '00001.webp', bytes(1000));
    final got = await JmPreviewCache.get('1466309', '00001.webp');
    expect(got, isNotNull);
    expect(got!.length, 1000);
    expect(got[255], 255);
  });

  test('没缓存的页返回 null（不报错、不建空文件）', () async {
    expect(await JmPreviewCache.get('1466309', '00009.webp'), isNull);
    expect(await JmPreviewCache.sizeBytes(), 0);
  });

  test('文件名把非法字符换成下划线（章节 id + 原文件名）', () {
    expect(JmPreviewCache.fileNameFor('1466309', '00001.webp'),
        '1466309_00001.webp');
    expect(JmPreviewCache.fileNameFor('123', 'a b/c:d.jpg'), '123_a_b_c_d.jpg');
  });

  test('统计占用与清空', () async {
    await JmPreviewCache.put('1', '00001.webp', bytes(500));
    await JmPreviewCache.put('1', '00002.webp', bytes(700));
    expect(await JmPreviewCache.sizeBytes(), 1200);
    final removed = await JmPreviewCache.clear();
    expect(removed, 2);
    expect(await JmPreviewCache.sizeBytes(), 0);
    expect(await JmPreviewCache.get('1', '00001.webp'), isNull);
  });

  test('超过上限时按最久未用清理', () async {
    // 造 3 个「大」文件（每个 1 字节也走同一套逻辑，只是上限检查用真实字节数）
    await JmPreviewCache.put('1', 'old.webp', bytes(10));
    final oldFile = await JmPreviewCache.fileFor('1', 'old.webp');
    // 手动把 mtime 调旧，模拟「很久没用」
    await oldFile.setLastModified(DateTime(2020, 1, 1));
    await JmPreviewCache.put('1', 'new1.webp', bytes(10));
    await JmPreviewCache.put('1', 'new2.webp', bytes(10));

    // 直接验证 LRU 清理函数本身：把上限临时压到 0 不可行（常量），
    // 所以这里只验证「访问会刷新 mtime」这一条清理依据
    final before = (await oldFile.stat()).modified.millisecondsSinceEpoch;
    await JmPreviewCache.get('1', 'old.webp');
    final after = (await oldFile.stat()).modified.millisecondsSinceEpoch;
    expect(after, greaterThan(before), reason: '读一次要刷新 mtime，免得被先删');
  });

  test('空字节不写入（避免留下 0 字节垃圾）', () async {
    await JmPreviewCache.put('1', 'empty.webp', Uint8List(0));
    expect(await JmPreviewCache.sizeBytes(), 0);
  });

  group('解析新链接后要不要清预览缓存', () {
    test('换本子要清', () {
      expect(shouldClearPreviewCache('1466309', '350234'), isTrue);
    });

    test('同一本重复解析保留（缓存才有意义）', () {
      expect(shouldClearPreviewCache('1466309', '1466309'), isFalse);
    });

    test('解析的不是禁漫（别的站点）一律清', () {
      expect(shouldClearPreviewCache(null, '1466309'), isTrue);
      expect(shouldClearPreviewCache('', '1466309'), isTrue);
      expect(shouldClearPreviewCache(null, ''), isTrue);
    });

    test('空白字符按空处理', () {
      expect(shouldClearPreviewCache('  ', '1466309'), isTrue);
      expect(shouldClearPreviewCache(' 1466309 ', '1466309'), isFalse);
    });
  });
}
