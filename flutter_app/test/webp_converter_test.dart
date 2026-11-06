import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/tools/webp_converter.dart';

/// WebP 转 MP4 的 Dart 侧逻辑：解码探测、参数校验、以及与原生通道的调用序列。
///
/// 原生编码（MediaCodec）没法在单元测试里真跑，这里用 mock channel 验证
/// 「start → pushFrame×N → finish」的调用约定与帧数换算；真机行为未在此覆盖。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('vdl/webp2mp4');
  late List<MethodCall> calls;

  final fixture = File('test/fixtures/anim3.webp');

  setUp(() {
    calls = <MethodCall>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'finish') return true;
      return true;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('fixture 存在且是 3 帧动图', () {
    expect(fixture.existsSync(), true, reason: '缺测试素材 test/fixtures/anim3.webp');
    expect(fixture.lengthSync(), greaterThan(0));
  });

  test('probe 读出尺寸、帧数与总时长', () async {
    final info = await WebpToMp4.probe(fixture.path);
    expect(info.width, 160);
    expect(info.height, 120);
    expect(info.frameCount, 3);
    expect(info.isAnimated, true);
    expect(info.durationMs, 300);
    expect(info.fileName, 'anim3.webp');
  });

  test('probe 对不存在的文件报中文错误', () async {
    await expectLater(
      WebpToMp4.probe('/no/such/file.webp'),
      throwsA(isA<WebpConversionException>()),
    );
  });

  test('probe 对非 WebP 内容报中文错误', () async {
    final tmp = File('${Directory.systemTemp.path}/_tmp_not_webp_webp_test.webp');
    tmp.writeAsStringSync('this is not an image');
    try {
      await expectLater(
        WebpToMp4.probe(tmp.path),
        throwsA(isA<WebpConversionException>()),
      );
    } finally {
      if (tmp.existsSync()) tmp.deleteSync();
    }
  });

  test('convert 拒绝空输入、非 mp4 输出、输入输出同路径', () async {
    await expectLater(
      WebpToMp4.convert(sources: <String>[], outputPath: '/tmp/a.mp4'),
      throwsA(isA<WebpConversionException>()),
    );
    await expectLater(
      WebpToMp4.convert(sources: <String>[fixture.path], outputPath: '/tmp/a.mkv'),
      throwsA(isA<WebpConversionException>()),
    );
    await expectLater(
      WebpToMp4.convert(sources: <String>[fixture.path], outputPath: fixture.path),
      throwsA(isA<WebpConversionException>()),
    );
  });

  test('convert 调 start → 逐帧 pushFrame → finish，并按时长换算重复帧', () async {
    final out = File('${Directory.systemTemp.path}/_tmp_webp2mp4_out.mp4');
    if (out.existsSync()) out.deleteSync();
    final progress = <int>[];
    try {
      final result = await WebpToMp4.convert(
        sources: <String>[fixture.path],
        outputPath: out.path,
        fps: 20,
        onProgress: (done, total, message) => progress.add(done),
      );
      expect(result.width, 160);
      expect(result.height, 120);
      // 3 帧 x 100ms，20fps 下每帧 2 次重复
      expect(result.frames, 6);
      expect(result.durationMs, 300);

      final methods = calls.map((call) => call.method).toList();
      expect(methods.first, 'start');
      expect(methods.last, 'finish');
      expect(methods.where((m) => m == 'pushFrame').length, 3);

      final startArgs = calls.first.arguments as Map<Object?, Object?>;
      expect(startArgs['width'], 160);
      expect(startArgs['height'], 120);
      expect(startArgs['fps'], 20);

      for (final call in calls.where((c) => c.method == 'pushFrame')) {
        final args = call.arguments as Map<Object?, Object?>;
        final rgba = args['rgba'] as Uint8List;
        expect(rgba.length, 160 * 120 * 4);
        expect(args['durationMs'], 100);
      }
      expect(progress.isNotEmpty, true);
    } finally {
      if (out.existsSync()) out.deleteSync();
    }
  });

  test('convert 支持多张按顺序拼接（画布取最大尺寸）', () async {
    final out = File('${Directory.systemTemp.path}/_tmp_webp2mp4_multi.mp4');
    if (out.existsSync()) out.deleteSync();
    try {
      final result = await WebpToMp4.convert(
        sources: <String>[fixture.path, fixture.path],
        outputPath: out.path,
      );
      expect(result.frames, 12);
      expect(calls.where((call) => call.method == 'pushFrame').length, 6);
      expect(result.width.isEven, true);
      expect(result.height.isEven, true);
    } finally {
      if (out.existsSync()) out.deleteSync();
    }
  });

  test('isCancelled 返回 true 时中途退出且不调 finish', () async {
    final out = File('${Directory.systemTemp.path}/_tmp_webp2mp4_cancel.mp4');
    if (out.existsSync()) out.deleteSync();
    try {
      await expectLater(
        WebpToMp4.convert(
          sources: <String>[fixture.path],
          outputPath: out.path,
          isCancelled: () => true,
        ),
        throwsA(isA<WebpConversionException>()),
      );
      expect(calls.any((call) => call.method == 'finish'), false);
    } finally {
      if (out.existsSync()) out.deleteSync();
    }
  });
}
