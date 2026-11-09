import 'dart:io';

import '../ffmpeg.dart';

/// 桌面端的音视频合并：调用外部 ffmpeg，不再走原生桥。
class MediaMuxer {
  static final MediaMuxer instance = MediaMuxer._();
  MediaMuxer._();

  Process? _proc;
  bool _cancelled = false;

  bool get supported =>
      Platform.isWindows || Platform.isLinux || Platform.isMacOS;

  Future<void> merge({
    required String videoPath,
    required String audioPath,
    required String outPath,
  }) async {
    if (!supported) {
      throw UnsupportedError('当前平台不支持音视频合并');
    }
    final ffmpeg = await resolveFfmpeg();
    if (ffmpeg == null) {
      throw StateError('没有找到 ffmpeg：请在设置里指定 ffmpeg 路径');
    }
    final out = File(outPath);
    if (await out.exists()) await out.delete();
    _cancelled = false;

    _proc = await Process.start(ffmpeg, <String>[
      '-y',
      '-loglevel', 'error',
      '-i', videoPath,
      '-i', audioPath,
      '-c', 'copy',
      '-map', '0:v:0',
      '-map', '1:a:0',
      outPath,
    ]);
    final err = StringBuffer();
    final errDone = _proc!.stderr
        .transform(const SystemEncoding().decoder)
        .listen(err.write)
        .asFuture<void>();
    await _proc!.stdout.drain<void>();
    await errDone;
    final code = await _proc!.exitCode;
    _proc = null;

    if (_cancelled) throw StateError('已取消');
    if (code != 0) {
      throw StateError('合并失败（ffmpeg 退出码 $code）：${err.toString().trim()}');
    }
  }

  Future<void> cancel() async {
    _cancelled = true;
    _proc?.kill();
    _proc = null;
  }
}
