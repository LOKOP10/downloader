import 'dart:async';
import 'dart:collection';
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
    required this.uid,
    required this.path,
    required this.width,
    required this.height,
    required this.frameCount,
    required this.durationMs,
    this.hasAlpha = false,
    this.durationEstimated = false,
  });

  /// 同一次会话内唯一：同一个文件可以被重复选择，列表 key 不能只用 path。
  final String uid;
  final String path;
  final int width;
  final int height;
  final int frameCount;
  final int durationMs;

  /// 首帧是否含半透明像素。全不透明且尺寸等于画布时可以跳过合成直接回读，
  /// 省掉一次 GPU 渲染往返（这是每帧的主要开销之一）。
  final bool hasAlpha;

  /// durationMs 是否为估算值（首帧时长 × 帧数）。列表里标「约」提醒。
  final bool durationEstimated;

  bool get isAnimated => frameCount > 1;

  String get fileName => fileNameOf(path);
}

/// 解码出来的一帧：像素 + 该帧时长。
class _DecodedFrame {
  _DecodedFrame(this.image, this.durationMs);

  final ui.Image image;
  final int durationMs;
}

/// 带背压的帧泵：后台按顺序解码，主流程按需取帧。
///
/// 这样「解码下一帧」与「合成 + 跨通道编码当前帧」能重叠起来 —— 原来是一帧一帧
/// 串行：解一帧 → 合成 → 传一帧 → 再解下一帧，解码线程大部分时间在空转。
/// 同一个 codec 的 getNextFrame 仍然是严格顺序调用（并发调同一个 codec 会乱序）。
class _FramePump {
  _FramePump(this._source) {
    _subscription = _decode().listen(
      _onFrame,
      onError: _onError,
      onDone: _onDone,
      cancelOnError: true,
    );
  }

  /// 缓冲上限。每帧约 3MB（744×1000 RGBA），4 帧约 12MB，够重叠又不炸内存。
  static const int _maxBuffered = 4;

  final WebpSourceInfo _source;
  late final StreamSubscription<_DecodedFrame> _subscription;
  final Queue<_DecodedFrame> _queue = Queue<_DecodedFrame>();
  Object? _error;
  StackTrace? _stackTrace;
  bool _done = false;

  Stream<_DecodedFrame> _decode() async* {
    final bytes = await File(_source.path).readAsBytes();
    final codec = await ui.instantiateImageCodec(bytes);
    try {
      for (var i = 0; i < codec.frameCount; i++) {
        final frame = await codec.getNextFrame();
        yield _DecodedFrame(frame.image, frame.duration.inMilliseconds);
      }
    } finally {
      codec.dispose();
    }
  }

  void _onFrame(_DecodedFrame frame) {
    _queue.add(frame);
    if (_queue.length >= _maxBuffered && !_subscription.isPaused) {
      _subscription.pause();
    }
  }

  void _onError(Object error, StackTrace stackTrace) {
    _error = error;
    _stackTrace = stackTrace;
  }

  void _onDone() {
    _done = true;
  }

  /// 取下一帧；没有更多帧时返回 null。
  Future<_DecodedFrame?> take() async {
    while (_queue.isEmpty && !_done && _error == null) {
      await Future<void>.delayed(Duration.zero);
    }
    if (_error != null) {
      Error.throwWithStackTrace(_error!, _stackTrace ?? StackTrace.current);
    }
    if (_queue.isEmpty) return null;
    final frame = _queue.removeFirst();
    if (_subscription.isPaused) {
      _subscription.resume();
    }
    return frame;
  }

  /// 停掉解码并释放队列里还没取走的帧。
  Future<void> dispose() async {
    await _subscription.cancel();
    for (final frame in _queue) {
      frame.image.dispose();
    }
    _queue.clear();
  }
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

  static int _uidSeq = 0;

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
      final first = await codec.getNextFrame();
      final width = first.image.width;
      final height = first.image.height;
      final firstMs = first.duration.inMilliseconds;
      // 采样判断是否含透明像素：每 8 个像素查一次 alpha 通道，便宜且够用
      // （透明通常是成片的，采样不会漏）。
      var hasAlpha = false;
      final firstData =
          await first.image.toByteData(format: ui.ImageByteFormat.rawRgba);
      if (firstData != null) {
        final firstRgba = firstData.buffer.asUint8List(
          firstData.offsetInBytes,
          firstData.lengthInBytes,
        );
        for (var i = 3; i < firstRgba.length; i += 32) {
          if (firstRgba[i] != 255) {
            hasAlpha = true;
            break;
          }
        }
      }
      first.image.dispose();
      if (width <= 0 || height <= 0) {
        throw WebpConversionException('图片尺寸无效：${_name(path)}');
      }
      if (width * height > maxCanvasPixels) {
        throw WebpConversionException('超过 1600 万像素限制：${_name(path)}');
      }
      // 只解首帧就够：帧数由 codec.frameCount 直接给出，不必逐帧解码累加。
      // 原来逐帧解一遍，9 个 193 帧的动图要多解 1737 帧全尺寸画面，选文件时
      // 会一串串卡十几秒。总时长按「首帧时长 × 帧数」估算：定帧率动图完全准确，
      // 可变帧率会有偏差（列表里标「约」）；真正编码用的是逐帧真实 PTS，不受影响。
      final frameCount = codec.frameCount;
      final estimatedMs = firstMs > 0
          ? firstMs * frameCount
          : frameCount * (1000 ~/ defaultFps);
      return WebpSourceInfo(
        uid: (_uidSeq++).toString(),
        path: path,
        width: width,
        height: height,
        frameCount: frameCount,
        durationMs: estimatedMs,
        hasAlpha: hasAlpha,
        durationEstimated: frameCount > 1,
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
    required List<WebpSourceInfo> sources,
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
      if (source.path.toLowerCase() == outputPath.toLowerCase()) {
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

    // 直接复用调用方已 probe 过的头部信息，不再逐个重新解码。
    // 原来这里对每个文件又 probe 一次，而 probe 要逐帧 getNextFrame 才算得出时长：
    // 9 个 193 帧的动图等于多解码 1737 帧全尺寸画面，转换前会长时间卡在
    // 「正在读取文件信息…」，之后真正编码时还要从头再解一遍。
    var totalFrames = 0;
    for (final source in sources) {
      checkCancel();
      totalFrames += source.frameCount;
      if (totalFrames > maxFrames) {
        throw WebpConversionException('总帧数超过 20000 帧限制');
      }
    }

    var canvasWidth = sources.first.width;
    var canvasHeight = sources.first.height;
    for (final head in sources) {
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
    // 分阶段计时写进进度文案，慢的时候能一眼看出瓶颈在解码、合成还是编码。
    var decodeUs = 0;
    var composeUs = 0;
    var pushUs = 0;
    final stopwatch = Stopwatch();
    _FramePump? pump;
    // 预取的下一张：中途出错时要一起停掉，否则它的解码还在后台跑。
    _FramePump? spare;
    try {
      var current = _FramePump(sources.first);
      pump = current;
      for (var f = 0; f < sources.length; f++) {
        final head = sources[f];
        // 尺寸已等于画布且不含透明像素时走快速路径：直接回读解码结果，省掉
        // PictureRecorder → toImage → toByteData 的整条 GPU 渲染往返。
        final direct = head.width == canvasWidth &&
            head.height == canvasHeight &&
            !head.hasAlpha;
        // 下一个文件立刻开始解码，与当前文件的合成/编码重叠，也就是多张并行。
        final upcoming =
            f + 1 < sources.length ? _FramePump(sources[f + 1]) : null;
        spare = upcoming;
        while (true) {
          checkCancel();
          stopwatch
            ..reset()
            ..start();
          final frame = await current.take();
          stopwatch.stop();
          decodeUs += stopwatch.elapsedMicroseconds;
          if (frame == null) break;
          final frameMs =
              frame.durationMs > 0 ? frame.durationMs : (1000 ~/ fps);
          stopwatch
            ..reset()
            ..start();
          final rgba = direct
              ? await _imageToRgba(frame.image)
              : await _composeFrame(frame.image, canvasWidth, canvasHeight);
          frame.image.dispose();
          stopwatch.stop();
          composeUs += stopwatch.elapsedMicroseconds;
          stopwatch
            ..reset()
            ..start();
          await _channel.invokeMethod<bool>('pushFrame', <String, dynamic>{
            'rgba': rgba,
            'durationMs': frameMs,
          });
          stopwatch.stop();
          pushUs += stopwatch.elapsedMicroseconds;
          writtenFrames += 1;
          emittedMs += frameMs;
          done += 1;
          report(
            done,
            totalFrames,
            '正在编码第 $done / $totalFrames 帧'
            '${_timingHint(decodeUs, composeUs, pushUs, done)}',
          );
        }
        await current.dispose();
        pump = null;
        if (upcoming == null) break;
        current = upcoming;
        pump = current;
      }
      report(totalFrames, totalFrames, '正在收尾…');
      final ok = await _channel.invokeMethod<bool>('finish') ?? false;
      if (!ok) {
        throw WebpConversionException('编码器没有产出视频轨，请重试');
      }
    } catch (error) {
      await pump?.dispose();
      await spare?.dispose();
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

  /// 尺寸已等于画布且不含透明像素时的快速路径：直接回读解码结果。
  static Future<Uint8List> _imageToRgba(ui.Image image) async {
    final data = await image.toByteData(format: ui.ImageByteFormat.rawRgba);
    if (data == null) {
      throw WebpConversionException('无法读取帧像素');
    }
    return data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes);
  }

  /// 每 10 帧刷新一次分阶段平均耗时，写进进度文案方便定位瓶颈。
  static String _timingHint(
    int decodeUs,
    int composeUs,
    int pushUs,
    int done,
  ) {
    if (done == 0 || done % 10 != 0) return '';
    final decode = (decodeUs / done / 1000).round();
    final compose = (composeUs / done / 1000).round();
    final push = (pushUs / done / 1000).round();
    return '（解码 ${decode}ms · 合成 ${compose}ms · 编码 ${push}ms）';
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

