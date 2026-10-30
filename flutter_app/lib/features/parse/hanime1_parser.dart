import 'package:vdl/features/parse/html_text.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/shared/net/browser_http.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

/// hanime1.me：纯 HTTP，免登录。
///
/// 链路（与桌面端 `sites.py::parse_hanime1` 同一套）：
///   1) `GET /watch?v=<id>` → 页面 `<video>` 里直接挂着 3 个
///      `<source type="video/mp4" size="…">`（1080 / 720 / 480，HTML 顺序是乱的）
///   2) 并发 1 字节 Range 取每档精确字节数（直链带 `?secure=<签名>,<过期>`）
///
/// 已知边界（与桌面端一致，不猜）：
///  · 页面没有时长字段，duration 留 0，界面显示 `--`；
///  · 直链是签名 + 时效的，解析后搁太久再下会 403，续传依赖重新解析；
///  · 没有备用 CDN 地址，所以只给一条主地址；
///  · 站点在 Cloudflare 后面，`HttpClient` 直接 403，必须走 [BrowserHttp]。
class Hanime1Parser {
  const Hanime1Parser();

  static const host = 'https://hanime1.me';

  /// 只认 `/watch?v=<id>`。实测：路径式 `/watch/<id>` 与 `/v/<id>` 都是 404，
  /// 参数名换成 `?id=` 直接 403 —— 别按「直觉」多写几种形式，站点不认。
  static final _idRe =
      RegExp(r'/watch\?v=([A-Za-z0-9_\-]+)', caseSensitive: false);
  static final _bareIdRe = RegExp(r'^(\d{4,10})$');

  static const _titleSuffix = ' - Hanime1.me';
  static const _retries = 2;
  static const _retryWait = Duration(milliseconds: 1200);

  /// 从链接或纯数字 id 里取视频 ID，识别失败返回空串。
  static String videoId(String text) {
    final raw = text.trim();
    final byUrl = _idRe.firstMatch(raw);
    if (byUrl != null) return byUrl.group(1)!;
    final bare = _bareIdRe.firstMatch(raw);
    return bare?.group(1) ?? '';
  }

  Future<MediaInfo> parse(String rawUrl) async {
    final vid = videoId(rawUrl);
    if (vid.isEmpty) {
      throw ParseException('无法从链接中识别 hanime1 视频 ID'
          '（应形如 hanime1.me/watch?v=123456）');
    }
    final pageUrl = '$host/watch?v=$vid';

    String html = '';
    var lastError = '';
    for (var attempt = 0; attempt < _retries; attempt++) {
      try {
        final res = await BrowserHttp.get(pageUrl, referer: '$host/');
        if (res.statusCode == 404) {
          throw ParseException('hanime1 视频 $vid 不存在或已删除');
        }
        if (res.ok) {
          html = res.body;
          if (sources(html).isNotEmpty) break;
          lastError = '页面能打开但没有视频源（可能被风控拦了）';
        } else {
          lastError = '页面返回 HTTP ${res.statusCode}';
        }
      } catch (error) {
        if (error is ParseException) rethrow;
        lastError = '连接失败（$error）';
      }
      if (attempt < _retries - 1) {
        await Future<void>.delayed(_retryWait * (attempt + 1));
      }
    }

    final found = sources(html);
    if (found.isEmpty) {
      throw ParseException('hanime1 没返回可下载的视频源：$lastError。'
          '该站有 Cloudflare 风控，稍后重试；若一直失败请确认代理能打开 hanime1.me');
    }

    // size 属性就是档位高度（1080 / 720 / 480），直接拿来当画质标签，并按高度降序
    final seen = <String>{};
    final qualities = <MediaQuality>[];
    for (final item in found) {
      if (!seen.add(item.url)) continue;
      qualities.add(MediaQuality(
        label: item.height > 0 ? '${item.height}p' : '视频',
        url: item.url,
      ));
    }
    qualities.sort((a, b) => _height(b.label) - _height(a.label));
    if (qualities.isEmpty) throw ParseException('hanime1 未解析到可用的下载地址');

    final sizes = await _probeSizes([for (final q in qualities) q.url]);
    final withSize = [
      for (final q in qualities)
        MediaQuality(label: q.label, url: q.url, sizeBytes: sizes[q.url] ?? 0),
    ];

    final title = pageTitle(html);
    return MediaInfo(
      platform: SitePlatform.hanime1,
      title: title.isEmpty ? vid : title,
      author: uploader(html),
      coverUrl: cover(html),
      // 站点不提供时长，见类注释
      durationMs: 0,
      qualities: withSize,
      sourceId: vid,
      referer: '$host/',
    );
  }

  /// 抠出所有 `<source type="video/mp4">`，返回档位（`size` = 档位高度）+ 地址。
  ///
  /// 属性顺序不保证（src / size / type 谁先谁后都可能），所以逐个标签内部单独取
  /// 属性，不写成一整条正则。页面里的排列顺序是 720 → 480 → 1080，必须自己排。
  static List<HanimeSource> sources(String html) {
    final out = <HanimeSource>[];
    for (final match in RegExp(r'<source\b[^>]*>', caseSensitive: false)
        .allMatches(html)) {
      final tag = match.group(0)!;
      final src =
          RegExp(r'src="([^"]+)"', caseSensitive: false).firstMatch(tag);
      if (src == null) continue;
      final url = src.group(1)!.trim();
      if (!url.toLowerCase().contains('.mp4')) continue;
      final size =
          RegExp(r'size="(\d+)"', caseSensitive: false).firstMatch(tag);
      out.add(HanimeSource(
        url: url,
        height: size == null ? 0 : (int.tryParse(size.group(1)!) ?? 0),
      ));
    }
    return out;
  }

  /// 标题：详情块的 `h3#shareBtn-title`，兜底 og:title 去掉 ` - Hanime1.me` 后缀。
  static String pageTitle(String html) {
    final h3 = RegExp(r'<h3[^>]*id="shareBtn-title"[^>]*>(.*?)</h3>',
            caseSensitive: false, dotAll: true)
        .firstMatch(html);
    if (h3 != null) {
      final text = HtmlText.stripTags(h3.group(1)!);
      if (text.isNotEmpty) return text;
    }
    final og = RegExp(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"',
            caseSensitive: false)
        .firstMatch(html);
    if (og == null) return '';
    var title = HtmlText.unescape(og.group(1)!).trim();
    if (title.endsWith(_titleSuffix)) {
      title = title.substring(0, title.length - _titleSuffix.length).trim();
    }
    return title;
  }

  /// 上传者：`a#video-artist-name`，兜底头像 `img#video-user-avatar` 的 alt。
  static String uploader(String html) {
    final link = RegExp(r'<a[^>]*id="video-artist-name"[^>]*>(.*?)</a>',
            caseSensitive: false, dotAll: true)
        .firstMatch(html);
    if (link != null) {
      final text = HtmlText.stripTags(link.group(1)!);
      if (text.isNotEmpty) return text;
    }
    final avatar = RegExp(
                r'<img[^>]*id="video-user-avatar"[^>]*alt="([^"]*)"',
                caseSensitive: false)
            .firstMatch(html) ??
        RegExp(r'<img[^>]*alt="([^"]*)"[^>]*id="video-user-avatar"',
                caseSensitive: false)
            .firstMatch(html);
    return avatar == null ? '' : HtmlText.unescape(avatar.group(1)!).trim();
  }

  /// 封面：`<video poster>` 优先（带 secure 签名），兜底 og:image。
  static String cover(String html) {
    final poster = RegExp(r'<video[^>]+poster="([^"]+)"', caseSensitive: false)
        .firstMatch(html);
    if (poster != null && poster.group(1)!.startsWith('http')) {
      return poster.group(1)!;
    }
    final og = RegExp(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"',
            caseSensitive: false)
        .firstMatch(html);
    return og == null ? '' : og.group(1)!.trim();
  }

  static int _height(String label) {
    final match = RegExp(r'^(\d{3,4})p$').firstMatch(label);
    return match == null ? 0 : (int.tryParse(match.group(1)!) ?? 0);
  }

  /// 并发探针取每档精确字节数。
  ///
  /// 直链带 `?secure=<签名>,<过期>`，签名是强制的（去掉 query 或改一个字符都回
  /// 403），所以只能用页面刚给的那条地址。该 CDN 支持 Range，且不需要 Referer。
  Future<Map<String, int>> _probeSizes(List<String> urls) async {
    final out = <String, int>{};
    if (urls.isEmpty) return out;
    await Future.wait([
      for (final url in urls.toSet())
        () async {
          final size = await AppHttp.probeSize(
            url,
            ua: AppHttp.uaDesktop,
            timeout: const Duration(seconds: 15),
          );
          if (size > 0) out[url] = size;
        }(),
    ]);
    return out;
  }
}

/// 一个 `<source>` 档位：地址 + 档位高度（1080 / 720 / 480）。
class HanimeSource {
  const HanimeSource({required this.url, required this.height});

  final String url;
  final int height;
}
