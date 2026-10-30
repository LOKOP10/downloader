import 'dart:convert';

import 'package:vdl/features/parse/html_text.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

/// Pornhub（pornhub.com）：纯 HTTP，免登录。
///
/// 链路（与桌面端 `sites.py::parse_pornhub` 同一套）：
///   1) `GET /view_video.php?viewkey=<id>` → 页面里的 `flashvars_<id>`
///   2) `mediaDefinitions` 里 `format=="mp4"` 那项是 `get_media?s=<base64>`
///      签名端点（`hls` 那几项是 m3u8）
///   3) `GET get_media`（**必须带视频页 Referer + X-Requested-With**）
///      → 4 档 mp4 直链（240 / 480 / 720 / 1080），链接带 validfrom/validto 时效
///   4) 并发 Range 探针取每档精确字节数
///
/// ⚠️ 2026-10 起站点改了发流方式：播放页的 `mediaDefinitions` 只剩 4 档 HLS
/// （240/480/720/1080），唯一的 `format=="mp4"` 项是个失效的 `get_media` 端点
/// —— 它会回**整页 HTML** 而不是 JSON。桌面端同一时刻同样拿不到 mp4（已对照
/// 验证，不是这里写错），所以这里做成「mp4 优先、HLS 兜底」：拿到 mp4 就用
/// mp4，拿不到就把 m3u8 当档位交给 `HlsDownloader`（产物是 .ts）。
///
/// ⚠️ 两个坑（桌面端踩过，这里原样保留）：
///  · 该站按 IP 限流：短时间连续请求后页面「能打开但拿不到 flashvars」，
///    隔几秒重试即可恢复，所以要退避重试而不是一次失败就报错；
///  · CDN 直链（ev.phncdn.com 等）**不带视频页 Referer 一律 404**，
///    所以探针与下载都用作品页地址做 Referer（见 `MediaInfo.referer`）。
///
/// 该站不在 Cloudflare 的拦截名单里，`HttpClient` 能正常 200，所以沿用
/// `AppHttp`（cookie 罐对 `get_media` 是必要的：裸请求会回**空数组**）。
class PornhubParser {
  const PornhubParser();

  static const host = 'https://www.pornhub.com';

  static final _viewkeyRe =
      RegExp(r'viewkey=([A-Za-z0-9]+)', caseSensitive: false);
  static final _embedRe =
      RegExp(r'pornhub\.com/embed/([A-Za-z0-9]+)', caseSensitive: false);

  /// 整段就是一个 viewkey（13 位十六进制，可能带 ph 前缀），允许用户直接粘 ID。
  static final _bareKeyRe = RegExp(r'(ph)?([0-9a-f]{13})', caseSensitive: false);

  static final _digitsRe = RegExp(r'^\d+$');

  static const _retries = 3;
  static const _retryWait = Duration(milliseconds: 1500);

  /// 从链接 / 分享文案 / 纯 viewkey 里取视频 ID，识别失败返回空串。
  static String viewkey(String text) {
    final raw = text.trim();
    final byQuery = _viewkeyRe.firstMatch(raw);
    if (byQuery != null) return byQuery.group(1)!;
    final byEmbed = _embedRe.firstMatch(raw);
    if (byEmbed != null) return byEmbed.group(1)!;
    final bare = _bareKeyRe.firstMatch(raw);
    if (bare != null && bare.group(0) == raw) {
      return '${bare.group(1) ?? ''}${bare.group(2)!}';
    }
    return '';
  }

  Future<MediaInfo> parse(String rawUrl) async {
    final key = viewkey(rawUrl);
    if (key.isEmpty) {
      throw ParseException('无法从链接中识别 Pornhub 视频 ID'
          '（应形如 view_video.php?viewkey=xxxx）');
    }
    final pageUrl = '$host/view_video.php?viewkey=$key';

    String html = '';
    Map<String, dynamic> flashvars = const {};
    var lastError = '';
    for (var attempt = 0; attempt < _retries; attempt++) {
      try {
        html = await AppHttp.getText(
          pageUrl,
          platform: SitePlatform.pornhub,
          ua: AppHttp.uaDesktop,
          timeout: const Duration(seconds: 30),
        );
      } catch (error) {
        if (error.toString().contains('404')) {
          throw ParseException('Pornhub 视频 $key 不存在或已删除');
        }
        lastError = '连接失败（$error）';
        html = '';
      }
      if (html.isNotEmpty) {
        flashvars = flashvarsOf(html);
        if (flashvars.isNotEmpty) break;
        lastError = '页面能打开但没带视频数据（多为限流）';
      }
      if (attempt < _retries - 1) {
        await Future<void>.delayed(_retryWait * (attempt + 1));
      }
    }
    if (flashvars.isEmpty) {
      throw ParseException('Pornhub 没返回视频数据：$lastError。'
          '该站会限流，稍等几秒再试；若一直失败请确认代理能打开 pornhub.com');
    }

    final definitions = flashvars['mediaDefinitions'];
    final endpoints = <String>[];
    final hlsStreams = <_HlsStream>[];
    if (definitions is List) {
      for (final raw in definitions) {
        if (raw is! Map) continue;
        final format = (raw['format'] ?? '').toString().toLowerCase();
        final url = (raw['videoUrl'] ?? '').toString();
        if (url.isEmpty) continue;
        if (format == 'mp4') {
          endpoints.add(url);
        } else if (format == 'hls') {
          hlsStreams.add(_HlsStream(
            quality: (raw['quality'] ?? '').toString().trim(),
            url: url,
          ));
        }
      }
    }
    if (endpoints.isEmpty && hlsStreams.isEmpty) {
      throw ParseException('该 Pornhub 视频没有可下载的地址（可能已下架或受限）');
    }

    final rows = <Map<String, dynamic>>[];
    for (final endpoint in endpoints) {
      for (var attempt = 0; attempt < _retries; attempt++) {
        dynamic data;
        try {
          data = await AppHttp.getJson(
            endpoint,
            platform: SitePlatform.pornhub,
            ua: AppHttp.uaDesktop,
            timeout: const Duration(seconds: 30),
            extra: {
              'Referer': pageUrl,
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json, text/plain, */*',
            },
          );
        } catch (_) {
          // 这个端点挂了就换下一个；空数组同样多为限流，退避后重试
          data = null;
        }
        if (data is List && data.isNotEmpty) {
          for (final raw in data) {
            if (raw is Map && (raw['videoUrl'] ?? '').toString().isNotEmpty) {
              rows.add(raw.cast<String, dynamic>());
            }
          }
          break;
        }
        if (attempt < _retries - 1) {
          await Future<void>.delayed(_retryWait * (attempt + 1));
        }
      }
      if (rows.isNotEmpty) break;
    }

    final seen = <String>{};
    final qualities = <MediaQuality>[];
    for (final row in rows) {
      final url = row['videoUrl'].toString();
      if (!seen.add(url)) continue;
      final label = (row['quality'] ?? '').toString().trim();
      qualities.add(MediaQuality(
        label: _digitsRe.hasMatch(label)
            ? '${label}p'
            : (label.isEmpty ? '视频' : label),
        url: url,
      ));
    }

    // mp4 端点失效（2026-10 起）：退回 HLS。m3u8 交给下载层，产物是 .ts。
    if (qualities.isEmpty) {
      for (final stream in hlsStreams) {
        if (!seen.add(stream.url)) continue;
        qualities.add(MediaQuality(
          label: _digitsRe.hasMatch(stream.quality)
              ? '${stream.quality}p'
              : 'HLS',
          url: stream.url,
        ));
      }
    }
    qualities.sort((a, b) => _rank(b.label) - _rank(a.label));
    if (qualities.isEmpty) {
      throw ParseException('Pornhub 没有返回可下载档位（该站会限流，稍等几秒重试）');
    }

    // 只有 mp4 直链能探字节数；m3u8 是文本，探了也没意义
    final mp4Urls = <String>[
      for (final quality in qualities)
        if (!quality.url.contains('.m3u8')) quality.url,
    ];
    final sizes = mp4Urls.isEmpty
        ? const <String, int>{}
        : await _probeSizes(mp4Urls, pageUrl);
    final withSize = <MediaQuality>[
      for (final quality in qualities)
        MediaQuality(
          label: quality.label,
          url: quality.url,
          sizeBytes: sizes[quality.url] ?? 0,
        ),
    ];

    final title = (flashvars['video_title'] ?? '').toString().trim();
    final fallbackTitle = ogTitle(html);
    return MediaInfo(
      platform: SitePlatform.pornhub,
      title: title.isNotEmpty
          ? title
          : (fallbackTitle.isEmpty ? key : fallbackTitle),
      author: uploader(html),
      coverUrl: (flashvars['image_url'] ?? '').toString().trim(),
      durationMs: durationMs(html),
      qualities: withSize,
      sourceId: key,
      referer: pageUrl,
    );
  }

  /// 从页面里抠出 `flashvars_<id>` 这个 JS 对象。
  ///
  /// 它是个几千字符的大 JSON，**不能靠贪婪 / 懒惰正则括到结尾** —— 页面里还有
  /// 别的 `};` 会提前截断。这里用花括号配对扫描（跳过字符串内部的括号与转义），
  /// 拿到对象的确切边界再 `jsonDecode`。抽成公开方法便于离线测试。
  static Map<String, dynamic> flashvarsOf(String html) {
    final match = RegExp(r'flashvars_\d+\s*=\s*\{').firstMatch(html);
    if (match == null) return const {};
    final start = html.indexOf('{', match.start);
    if (start < 0) return const {};
    var depth = 0;
    var inString = false;
    var escaped = false;
    for (var i = start; i < html.length; i++) {
      final char = html[i];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (char == '\\') {
          escaped = true;
        } else if (char == '"') {
          inString = false;
        }
        continue;
      }
      if (char == '"') {
        inString = true;
      } else if (char == '{') {
        depth++;
      } else if (char == '}') {
        depth--;
        if (depth != 0) continue;
        try {
          final value = jsonDecode(html.substring(start, i + 1));
          return value is Map ? value.cast<String, dynamic>() : const {};
        } catch (_) {
          return const {};
        }
      }
    }
    return const {};
  }

  /// 时长（毫秒）：`video_duration` 是秒数，ld+json 里是 ISO 8601 时长。
  static int durationMs(String html) {
    final seconds =
        RegExp(r'"video_duration"\s*:\s*(\d+)').firstMatch(html);
    if (seconds != null) {
      return (int.tryParse(seconds.group(1)!) ?? 0) * 1000;
    }
    final iso = RegExp(
            r'"duration"\s*:\s*"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"')
        .firstMatch(html);
    if (iso != null) {
      final h = int.tryParse(iso.group(1) ?? '') ?? 0;
      final m = int.tryParse(iso.group(2) ?? '') ?? 0;
      final s = int.tryParse(iso.group(3) ?? '') ?? 0;
      return (h * 3600 + m * 60 + s) * 1000;
    }
    return 0;
  }

  /// og:title（页面里最稳的标题来源）。
  static String ogTitle(String html) {
    final match = RegExp(
            r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"',
            caseSensitive: false)
        .firstMatch(html);
    return match == null ? '' : HtmlText.unescape(match.group(1)!).trim();
  }

  /// 上传者昵称（尽力而为，拿不到就留空）。
  static String uploader(String html) {
    for (final pattern in [
      RegExp(r'"author"\s*:\s*"([^"]{1,80})"'),
      RegExp(r'class="videoUploaderLink"[^>]*>([^<]{1,80})<'),
    ]) {
      final match = pattern.firstMatch(html);
      if (match != null) {
        return HtmlText.unescape(match.group(1)!).trim();
      }
    }
    return '';
  }

  static int _rank(String label) {
    final match = RegExp(r'(\d{3,4})').firstMatch(label);
    return match == null ? 0 : (int.tryParse(match.group(1)!) ?? 0);
  }

  /// 并发探针取每档精确字节数。
  ///
  /// phncdn 的直链**不带视频页 Referer 会回 404**，探针必须带上；
  /// 它也不吃 HEAD，只能 1 字节 Range 从 `Content-Range` 读总长。
  Future<Map<String, int>> _probeSizes(List<String> urls, String pageUrl) async {
    final out = <String, int>{};
    if (urls.isEmpty) return out;
    await Future.wait([
      for (final url in urls.toSet())
        () async {
          final size = await AppHttp.probeSize(
            url,
            platform: SitePlatform.pornhub,
            ua: AppHttp.uaDesktop,
            extra: {'Referer': pageUrl},
            timeout: const Duration(seconds: 15),
          );
          if (size > 0) out[url] = size;
        }(),
    ]);
    return out;
  }
}

/// 一档 HLS 流（`quality` 是档位高度，`url` 是 master.m3u8）。
class _HlsStream {
  const _HlsStream({required this.quality, required this.url});

  final String quality;
  final String url;
}
