import 'dart:io';

import 'package:flutter/services.dart';

/// DASH 音视频合并的 Dart 入口：转发给安卓原生 MediaMuxer（不用 ffmpeg）。
///
/// 只有「分离音视频」的档位需要它（目前是 B 站的 DASH 1080P / 4K）。
/// 原生侧把视频轨与音频轨写进同一个 mp4，合并期间可以 cancel。
class MediaMuxer {
  MediaMuxer._();

  static final MediaMuxer instance = MediaMuxer._();

  static const MethodChannel _channel = MethodChannel('vdl/muxer');

  bool get supported => Platform.isAndroid;

  Future<void> merge({
    required String videoPath,
    required String audioPath,
    required String outPath,
  }) async {
    if (!supported) {
      throw UnsupportedError('音视频合并只在安卓端可用');
    }
    final ok = await _channel.invokeMethod<bool>('merge', <String, String>{
      'videoPath': videoPath,
      'audioPath': audioPath,
      'outPath': outPath,
    });
    if (ok != true) throw StateError('合并失败：原生侧没有返回成功');
  }

  /// 让正在跑的合尽早退出（原生每写一帧检查一次）。
  Future<void> cancel() async {
    if (!supported) return;
    try {
      await _channel.invokeMethod<void>('cancel');
    } catch (_) {
      // 没有正在进行的合并，忽略即可
    }
  }
}
