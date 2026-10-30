import 'dart:async';
import 'dart:io';

import 'package:vdl/shared/net/http.dart';

/// 取消令牌：任务页点「取消」时置位，正在跑的连接会尽快退出。
class CancelToken {
  bool _cancelled = false;

  bool get isCancelled => _cancelled;

  void cancel() => _cancelled = true;

  void throwIfCancelled() {
    if (_cancelled) throw const DownloadCancelled();
  }
}

class DownloadCancelled implements Exception {
  const DownloadCancelled();

  @override
  String toString() => '已取消';
}

/// 下载进度快照。
class DownloadProgress {
  const DownloadProgress({
    required this.received,
    required this.total,
    required this.speed,
  });

  final int received;
  final int total;
  final double speed;

  double get ratio => total <= 0 ? 0 : (received / total).clamp(0, 1).toDouble();

  String get receivedText => sizeText(received);

  String get totalText => total <= 0 ? '未知' : sizeText(total);

  String get speedText => sizeText(speed.round()) + '/s';

  static String sizeText(int bytes) {
    if (bytes < 1024) return '${bytes} B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
  }
}

/// 多连接分段下载器。
///
/// 先探测服务器是否支持 Range：支持就切 N 段并发写同一文件的不同偏移，
/// 不支持则退回单连接顺序写。CDN（抖音 / B站）基本都支持 206。
class Downloader {
  const Downloader();

  /// 小于 2MB 的文件单独切段没意义，直接单连接。
  static const _chunkThreshold = 2 * 1024 * 1024;

  Future<void> download({
    required String url,
    required File target,
    int connections = 4,
    Map<String, String>? headers,
    required void Function(DownloadProgress) onProgress,
    CancelToken? cancelToken,
  }) async {
    final total = await _probeTotal(url, headers);
    final parts = total >= _chunkThreshold ? connections.clamp(1, 8) : 1;

    await target.parent.create(recursive: true);
    final file = await target.open(mode: FileMode.write);
    if (total > 0) await file.truncate(total);

    // RandomAccessFile 的写在 Dart 里是异步的：多段并发 setPosition+write
    // 会撞上 "An async operation is currently pending"。用 Future 链把写串行化，
    // 网络仍是并行的（瓶颈在网络），只有落盘那一步排队。
    var writeLock = Future<void>.value();
    Future<void> writeAt(int position, List<int> chunk) {
      final task = writeLock.then((_) async {
        await file.setPosition(position);
        await file.writeFrom(chunk);
      });
      writeLock = task.catchError((Object _) {});
      return task;
    }

    var received = 0;
    var lastBytes = 0;
    var lastAt = DateTime.now();

    void report() {
      final now = DateTime.now();
      final ms = now.difference(lastAt).inMilliseconds;
      var speed = 0.0;
      if (ms >= 150) {
        speed = (received - lastBytes) * 1000 / ms;
        lastBytes = received;
        lastAt = now;
      }
      onProgress(DownloadProgress(
        received: received,
        total: total > 0 ? total : received,
        speed: speed,
      ));
    }

    try {
      if (parts <= 1) {
        await _fetchRange(
          url: url,
          headers: headers,
          writeAt: writeAt,
          start: 0,
          end: total > 0 ? total - 1 : null,
          onBytes: (n) {
            received += n;
            report();
          },
          cancelToken: cancelToken,
        );
      } else {
        final chunk = (total / parts).ceil();
        var offset = 0;
        final futures = <Future<void>>[];
        for (var i = 0; i < parts; i++) {
          final start = offset;
          final end = (start + chunk - 1).clamp(0, total - 1);
          offset = end + 1;
          if (start > end) break;
          futures.add(_fetchRange(
            url: url,
            headers: headers,
            writeAt: writeAt,
            start: start,
            end: end,
            onBytes: (n) {
              received += n;
              report();
            },
            cancelToken: cancelToken,
          ));
        }
        await Future.wait(futures);
      }
      report();
    } finally {
      await file.close();
    }
  }

  Future<int> _probeTotal(String url, Map<String, String>? headers) async {
    final client = AppHttp.newClient();
    try {
      final req = await client.getUrl(Uri.parse(url));
      (headers ?? AppHttp.headers()).forEach(req.headers.set);
      req.headers.set('Range', 'bytes=0-0');
      final resp = await req.close();
      await resp.drain<void>();
      if (resp.statusCode == 206) {
        final range = resp.headers.value('content-range') ?? '';
        final m = RegExp(r'/(\d+)$').firstMatch(range);
        if (m != null) return int.tryParse(m.group(1)!) ?? 0;
      }
      final len = resp.contentLength;
      return len > 0 ? len : 0;
    } catch (_) {
      return 0;
    } finally {
      client.close(force: true);
    }
  }

  Future<void> _fetchRange({
    required String url,
    required Map<String, String>? headers,
    required Future<void> Function(int position, List<int> chunk) writeAt,
    required int start,
    required int? end,
    required void Function(int) onBytes,
    CancelToken? cancelToken,
  }) async {
    final client = AppHttp.newClient();
    try {
      final req = await client.getUrl(Uri.parse(url));
      (headers ?? AppHttp.headers()).forEach(req.headers.set);
      if (end != null) req.headers.set('Range', 'bytes=${start}-${end}');
      final resp = await req.close().timeout(const Duration(seconds: 30));
      if (resp.statusCode >= 400) {
        throw HttpException('下载失败 HTTP ${resp.statusCode}');
      }
      var position = start;
      await for (final chunk in resp) {
        cancelToken?.throwIfCancelled();
        await writeAt(position, chunk);
        position += chunk.length;
        onBytes(chunk.length);
      }
    } finally {
      client.close(force: true);
    }
  }
}
