import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/services.dart';

/// 取路径里的文件名。同时认 `/` 与 `\`：Android 用 `/`，Windows 风格路径用 `\`。
String fileNameOf(String path) {
  final slash = path.lastIndexOf('/');
  final backslash = path.lastIndexOf('\\');
  final index = slash > backslash ? slash : backslash;
  return index < 0 ? path : path.substring(index + 1);
}

/// WebP（含 animated）转 MP4 的失败原因，message 直接面向用户。
class WebpConversionException implements Exception {
  WebpConversionException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 转换结果。frames 是写入视频的帧数（与原动画帧数一一对应，不再做重复帧）。
class WebpConversionResult {
  const WebpConversionResult({
    required this.path,
    required this.frames,
    required this.durationMs,
    required this.width,
    required this.height,
  });

  final String path;
  final int frames;
  final int durationMs;
  final int width;
  final int height;
}

/// 单个 WebP 输入的头部信息。
class WebpSourceInfo {
  const WebpSourceInfo({
    required this.path,
    required this.width,
    required this.height,
    required this.frameCount,
    required this.durationMs,
  });

  final String path;
  final int width;
  final int height;
  final int frameCount;
  final int durationMs;

  bool get isAnimated => frameCount > 1;

  String get fileName => fileNameOf(path);
}

/// 逐帧解码 WebP 并交给原生 MediaCodec 编码成 mp4。
///
/// 解码走 dart:ui 的 ImageCodec（Skia 原生支持 animated WebP），编码走原生
/// MediaCodec + MediaMuxer，不依赖 ffmpeg。多张按传入顺序拼接，画布取最大
/// 尺寸并居中、白底填充，与桌面端行为一致。
class WebpToMp4 {
  WebpToMp4._();

  static const MethodChannel _channel = MethodChannel('vdl/webp2mp4');
  static const int defaultFps = 20;
  static const int maxCanvasPixels = 16000000;
  static const int maxFrames = 20000;
  static const int _maxInputBytes = 256 * 1024 * 1024;

  /// 只读头部：尺寸、帧数、总时长。选中文件后用来估算进度与画布。
  static Future<WebpSourceInfo> probe(String path) async {
    final file = File(path);
    if (!file.existsSync()) {
      throw WebpConversionException('文件不存在：$path');
    }
    if (file.lengthSync() > _maxInputBytes) {
      throw WebpConversionException('单个文件超过 256 MiB：${_name(path)}');
    }
    final bytes = await file.readAsBytes();
    ui.Codec? codec;
    try {
      codec = await ui.instantiateImageCodec(bytes);
      var durationMs = 0;
      final first = await codec.getNextFrame();
      final width = first.image.width;
      final height = first.image.height;
      durationMs += first.duration.inMilliseconds;
      first.image.dispose();
      if (width <= 0 || height <= 0) {
        throw WebpConversionException('图片尺寸无效：${_name(path)}');
      }
      if (width * height > maxCanvasPixels) {
        throw WebpConversionException('超过 1600 万像素限制：${_name(path)}');
      }
      for (var i = 1; i < codec.frameCount; i++) {
        final info = await codec.getNextFrame();
        durationMs += info.duration.inMilliseconds;
        info.image.dispose();
      }
      return WebpSourceInfo(
        path: path,
        width: width,
        height: height,
        frameCount: codec.frameCount,
        durationMs: durationMs,
      );
    } on WebpConversionException {
      rethrow;
    } catch (error) {
      throw WebpConversionException('不是可解码的 WebP：${_name(path)}（$error）');
    } finally {
      codec?.dispose();
    }
  }

  /// 转换入口。onProgress(done, total, message) 在调用方线程回调。
  static Future<WebpConversionResult> convert({
    required List<String> sources,
    required String outputPath,
    int fps = defaultFps,
    void Function(int done, int total, String message)? onProgress,
    bool Function()? isCancelled,
  }) async {
    if (sources.isEmpty) {
      throw WebpConversionException('请至少选择一个 WebP 文件');
    }
    if (!outputPath.toLowerCase().endsWith('.mp4')) {
      throw WebpConversionException('输出文件扩展名必须是 .mp4');
    }
    for (final source in sources) {
      if (source.toLowerCase() == outputPath.toLowerCase()) {
        throw WebpConversionException('输入和输出不能是同一个文件');
      }
    }

    void report(int done, int total, String message) {
      onProgress?.call(done, total, message);
    }

    void checkCancel() {
      if (isCancelled != null && isCancelled()) {
        throw WebpConversionException('已取消');
      }
    }

    report(0, 0, '正在读取文件信息…');
    final headers = <WebpSourceInfo>[];
    var totalFrames = 0;
    for (final source in sources) {
      checkCancel();
      final head = await probe(source);
      headers.add(head);
      totalFrames += head.frameCount;
      if (totalFrames > maxFrames) {
        throw WebpConversionException('总帧数超过 20000 帧限制');
      }
    }

    var canvasWidth = headers.first.width;
    var canvasHeight = headers.first.height;
    for (final head in headers) {
      if (head.width > canvasWidth) canvasWidth = head.width;
      if (head.height > canvasHeight) canvasHeight = head.height;
    }
    // H.264 要求偶数尺寸，向上补一像素（与桌面端一致）。
    if (canvasWidth.isOdd) canvasWidth += 1;
    if (canvasHeight.isOdd) canvasHeight += 1;
    if (canvasWidth * canvasHeight > maxCanvasPixels) {
      throw WebpConversionException('合并画布超过 1600 万像素限制');
    }

    final outFile = File(outputPath);
    await outFile.parent.create(recursive: true);
    if (await outFile.exists()) {
      await outFile.delete();
    }

    report(0, totalFrames, '正在启动编码器…');
    await _channel.invokeMethod<bool>('start', <String, dynamic>{
      'width': canvasWidth,
      'height': canvasHeight,
      'fps': fps,
      'outPath': outputPath,
    });

    var done = 0;
    var writtenFrames = 0;
    var emittedMs = 0;
    try {
      for (final head in headers) {
        final bytes = await File(head.path).readAsBytes();
        final codec = await ui.instantiateImageCodec(bytes);
        try {
          for (var i = 0; i < codec.frameCount; i++) {
            checkCancel();
            final info = await codec.getNextFrame();
            final frameMs = info.duration.inMilliseconds > 0
                ? info.duration.inMilliseconds
                : (1000 ~/ fps);
            final rgba = await _composeFrame(info.image, canvasWidth, canvasHeight);
            info.image.dispose();
            await _channel.invokeMethod<bool>('pushFrame', <String, dynamic>{
              'rgba': rgba,
              'durationMs': frameMs,
            });
            writtenFrames += 1;
            emittedMs += frameMs;
            done += 1;
            report(done, totalFrames, '正在编码第 $done / $totalFrames 帧…');
          }
        } finally {
          codec.dispose();
        }
      }
      report(totalFrames, totalFrames, '正在收尾…');
      final ok = await _channel.invokeMethod<bool>('finish') ?? false;
      if (!ok) {
        throw WebpConversionException('编码器没有产出视频轨，请重试');
      }
    } catch (error) {
      await _channel.invokeMethod<void>('cancel').catchError((_) {});
      await _deleteQuietly(outFile);
      if (error is WebpConversionException) rethrow;
      throw WebpConversionException('转换失败：$error');
    }

    return WebpConversionResult(
      path: outputPath,
      frames: writtenFrames,
      durationMs: emittedMs,
      width: canvasWidth,
      height: canvasHeight,
    );
  }

  /// 主动取消正在进行的转换（原生侧下一次推帧时抛错退出）。
  static Future<void> cancel() async {
    try {
      await _channel.invokeMethod<void>('cancel');
    } catch (_) {
      // 原生还没启动时忽略。
    }
  }

  /// 把单帧画到统一画布中央（白底），返回 RGBA 字节。
  static Future<Uint8List> _composeFrame(ui.Image image, int width, int height) async {
    final recorder = ui.PictureRecorder();
    final canvas = ui.Canvas(recorder);
    canvas.drawRect(
      ui.Rect.fromLTWH(0, 0, width.toDouble(), height.toDouble()),
      ui.Paint()..color = const ui.Color(0xFFFFFFFF),
    );
    final dx = ((width - image.width) / 2).floorToDouble();
    final dy = ((height - image.height) / 2).floorToDouble();
    canvas.drawImage(image, ui.Offset(dx, dy), ui.Paint());
    final picture = recorder.endRecording();
    ui.Image? composed;
    try {
      composed = await picture.toImage(width, height);
      final data = await composed.toByteData(format: ui.ImageByteFormat.rawRgba);
      if (data == null) {
        throw WebpConversionException('无法读取帧像素');
      }
      return data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes);
    } finally {
      composed?.dispose();
      picture.dispose();
    }
  }

  static Future<void> _deleteQuietly(File file) async {
    try {
      if (await file.exists()) await file.delete();
    } catch (_) {
      // 删不掉就留着，不覆盖原始错误。
    }
  }

  static String _name(String path) => fileNameOf(path);
}

