import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/shared/net/browser_http.dart';

/// HLS（m3u8）下载：把分片按顺序抓下来、顺序拼进同一个文件。
///
/// 为什么需要它：Pornhub 从 2026-10 起不再给 mp4 直链 —— 播放页
/// `mediaDefinitions` 里那 4 档全是 HLS，`/video/get_media` 端点也退化成
/// 回整页 HTML。桌面端同一时刻同样拿不到 mp4（已对照验证），所以只能走 HLS。
///
/// 为什么不用 `AppHttp` / `HttpClient`：分片所在的 `hv-h.phncdn.com` 也在
/// Cloudflare 后面，`HttpClient` 取 m3u8 直接回 **410**（body 只有 1 字节），
/// 换成 `BrowserHttp`（裸 TLS + Chrome 头序）同一个 URL 就回 200（2026-10 实测）。
/// 所以整条 HLS 链路都走 [BrowserHttp]。
///
/// 产物是**拼好的 TS 流**（.ts）：分片本身就是 MPEG-TS，直接首尾相接就是一个
/// 合法流，ExoPlayer / VLC 都能播。项目里没有 ffmpeg，也不打算为一次封装引入它，
/// 所以**不做 ts→mp4 的 remux**，文件名老老实实用 .ts。
///
/// 已知边界：
///  · 只支持明文分片；遇到 `#EXT-X-KEY` 直接报错，不静默产出花屏文件；
///  · 分片带时效签名（Pornhub 是 1~2 小时），过期要重新解析；
///  · 进度按「码率 × 总时长」估算总字节，最后一片后校正到实际字节。
class HlsDownloader {
  const HlsDownloader();

  /// 分片并发数：再多容易撞站点限流，太少又跑不满带宽。
  static const _concurrency = 4;

  /// 下载 [url]（master 或 media playlist 都行）并写到 [target]。
  ///
  /// [headers] 沿用下载器那套（UA / Referer / Cookie）：`Referer` 会转成
  /// [BrowserHttp] 的 referer，其余头原样透传。
  Future<void> download({
    required String url,
    required File target,
    Map<String, String>? headers,
    required void Function(DownloadProgress) onProgress,
    CancelToken? cancelToken,
  }) async {
    final referer = headers?['Referer'] ?? '';
    final extra = <String, String>{};
    headers?.forEach((name, value) {
      final lower = name.toLowerCase();
      if (lower == 'referer' || lower == 'user-agent') return;
      extra[name] = value;
    });

    final playlist = await _resolvePlaylist(url, referer, extra, cancelToken);
    if (playlist.segments.isEmpty) {
      throw const HttpException('这个 m3u8 里没有分片');
    }

    await target.parent.create(recursive: true);
    final file = await target.open(mode: FileMode.write);
    try {
      final estimated = playlist.estimatedBytes;
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
          total: estimated > 0 ? estimated : received,
          speed: speed,
        ));
      }

      // 分片地址先全部展开（相对路径按 playlist 的地址解析）
      final urls = <String>[
        if (playlist.initUrl.isNotEmpty) playlist.initUrl,
        ...playlist.segments,
      ];

      // 滑动窗口并发抓、严格按顺序落盘：乱序完成也不会把分片写错位置
      final pending = <int, Future<List<int>>>{};
      var next = 0;
      for (var index = 0; index < urls.length; index++) {
        while (pending.length < _concurrency && next < urls.length) {
          pending[next] = _fetchBytes(urls[next], referer, extra);
          next++;
        }
        final bytes = await pending.remove(index)!;
        cancelToken?.throwIfCancelled();
        await file.writeFrom(bytes);
        received += bytes.length;
        report();
      }
      onProgress(DownloadProgress(
        received: received,
        total: received,
        speed: 0,
      ));
    } finally {
      await file.close();
    }
  }

  Future<_Playlist> _resolvePlaylist(
    String url,
    String referer,
    Map<String, String> extra,
    CancelToken? cancelToken,
  ) async {
    cancelToken?.throwIfCancelled();
    final text = await _fetchText(url, referer, extra);
    if (_isMaster(text)) {
      final variants = _parseVariants(text, url);
      if (variants.isEmpty) {
        throw const HttpException('m3u8 的 master 列表里没有可用档位');
      }
      final best = variants.reduce((a, b) => b.bandwidth > a.bandwidth ? b : a);
      final media = await _fetchText(best.url, referer, extra);
      return _parseMedia(media, best.url, bandwidth: best.bandwidth);
    }
    return _parseMedia(text, url);
  }

  Future<String> _fetchText(
    String url,
    String referer,
    Map<String, String> extra,
  ) async {
    final res = await BrowserHttp.get(
      url,
      referer: referer,
      extra: extra,
      cors: true,
      timeout: const Duration(seconds: 30),
    );
    if (!res.ok) {
      throw HttpException('取 m3u8 失败 HTTP ${res.statusCode}',
          uri: Uri.parse(url));
    }
    return res.body;
  }

  Future<List<int>> _fetchBytes(
    String url,
    String referer,
    Map<String, String> extra,
  ) async {
    final res = await BrowserHttp.getBytes(
      url,
      referer: referer,
      extra: extra,
      timeout: const Duration(seconds: 90),
    );
    if (!res.ok) {
      throw HttpException('分片下载失败 HTTP ${res.statusCode}',
          uri: Uri.parse(url));
    }
    return res.bytes;
  }

  static bool _isMaster(String text) => text.contains('#EXT-X-STREAM-INF');

  static List<_Variant> _parseVariants(String text, String baseUrl) {
    final lines = text
        .split(RegExp(r'\r?\n'))
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList();
    final out = <_Variant>[];
    for (var i = 0; i < lines.length; i++) {
      final line = lines[i];
      if (!line.startsWith('#EXT-X-STREAM-INF')) continue;
      final bandwidth = int.tryParse(
            RegExp(r'BANDWIDTH=(\d+)').firstMatch(line)?.group(1) ?? '',
          ) ??
          0;
      // 档位地址在下一行（可能中间还夹着别的 #EXT-X-* 标签）
      var j = i + 1;
      while (j < lines.length && lines[j].startsWith('#')) {
        j++;
      }
      if (j >= lines.length) break;
      out.add(_Variant(bandwidth: bandwidth, url: _abs(baseUrl, lines[j])));
    }
    return out;
  }

  static _Playlist _parseMedia(
    String text,
    String baseUrl, {
    int bandwidth = 0,
  }) {
    if (text.contains('#EXT-X-KEY') && !text.contains('METHOD=NONE')) {
      throw const HttpException('这个 HLS 流是加密的（#EXT-X-KEY），暂不支持');
    }
    final lines = text
        .split(RegExp(r'\r?\n'))
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList();
    final segments = <String>[];
    var durationMs = 0;
    var initUrl = '';
    for (final line in lines) {
      if (line.startsWith('#EXTINF:')) {
        final value = line.substring(8).split(',').first.trim();
        durationMs += ((double.tryParse(value) ?? 0) * 1000).round();
        continue;
      }
      if (line.startsWith('#EXT-X-MAP:')) {
        final match = RegExp(r'URI="([^"]+)"').firstMatch(line);
        if (match != null) initUrl = _abs(baseUrl, match.group(1)!);
        continue;
      }
      if (line.startsWith('#')) continue;
      segments.add(_abs(baseUrl, line));
    }
    return _Playlist(
      segments: segments,
      initUrl: initUrl,
      durationMs: durationMs,
      bandwidth: bandwidth,
    );
  }

  /// 相对地址按 playlist 自身地址解析（m3u8 里都是相对路径）。
  static String _abs(String base, String relative) {
    if (relative.startsWith('http://') || relative.startsWith('https://')) {
      return relative;
    }
    return Uri.parse(base).resolve(relative).toString();
  }
}

class _Variant {
  const _Variant({required this.bandwidth, required this.url});

  final int bandwidth;
  final String url;
}

class _Playlist {
  const _Playlist({
    required this.segments,
    required this.initUrl,
    required this.durationMs,
    required this.bandwidth,
  });

  final List<String> segments;
  final String initUrl;
  final int durationMs;
  final int bandwidth;

  /// 按「码率 × 总时长」估算总字节；算不出来就返回 0（界面显示未知）。
  int get estimatedBytes {
    if (bandwidth <= 0 || durationMs <= 0) return 0;
    return (bandwidth * durationMs) ~/ 8000;
  }
}
