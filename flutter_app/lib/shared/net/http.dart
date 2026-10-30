import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../site.dart';

/// 统一网络出口：按站点补 User-Agent / Referer / Cookie。
///
/// UA 取值与 Kotlin 端 Http.kt 一致 —— 抖音那条链路对 UA 敏感，
/// 换 UA 会被判成不同的设备通道。
class AppHttp {
  static const uaDesktop =
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
  static const uaMobile =
      'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36';
  static const uaIphone =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1';

  static String refererOf(SitePlatform platform) => switch (platform) {
        SitePlatform.douyin => 'https://www.douyin.com/',
        SitePlatform.xhs => 'https://www.xiaohongshu.com/',
        SitePlatform.bili => 'https://www.bilibili.com/',
        SitePlatform.x => 'https://x.com/',
        SitePlatform.iwara => 'https://www.iwara.tv/',
        SitePlatform.pornhub => 'https://www.pornhub.com/',
        SitePlatform.hanime1 => 'https://hanime1.me/',
        SitePlatform.jm => 'https://18comic.vip/',
      };

  /// 各站点 Cookie（登录后能拿到更完整的数据；为空按游客访问）。
  static final Map<SitePlatform, String> cookies = {};

  /// 进程级代理（形如 `127.0.0.1:7897`），**只给桌面实测脚本用**。
  ///
  /// 真机上留空：安卓的代理由系统 VPN / 全局代理接管，这里再设一遍反而会
  /// 在「代理没开」时把整条链路弄挂。
  static String? proxy;

  /// 建一个内部 HttpClient（统一带连接超时；设了 [proxy] 就走代理）。
  static HttpClient newClient({
    Duration connectTimeout = const Duration(seconds: 15),
  }) {
    final client = HttpClient()..connectionTimeout = connectTimeout;
    final host = proxy;
    if (host != null && host.isNotEmpty) {
      client.findProxy = (_) => 'PROXY $host';
    }
    return client;
  }

  /// 进程内 cookie 罐：按 host 存。抖音必须先拿 ttwid 再请求同一页，
  /// 否则永远只回不含视频地址的空壳。
  static final Map<String, Map<String, String>> _jar = {};

  static void _absorb(String url, HttpClientResponse resp) {
    final values = resp.headers['set-cookie'];
    if (values == null || values.isEmpty) return;
    final jar = _jar.putIfAbsent(Uri.parse(url).host, () => <String, String>{});
    for (final raw in values) {
      final first = raw.split(';').first;
      final eq = first.indexOf('=');
      if (eq <= 0) continue;
      jar[first.substring(0, eq).trim()] = first.substring(eq + 1).trim();
    }
  }

  static String _jarCookie(String url) {
    final jar = _jar[Uri.parse(url).host];
    if (jar == null || jar.isEmpty) return '';
    return jar.entries.map((e) => '${e.key}=${e.value}').join('; ');
  }

  static Map<String, String> _merge(String url, Map<String, String> base) {
    final jar = _jarCookie(url);
    if (jar.isEmpty) return base;
    final merged = Map<String, String>.from(base);
    final existing = merged['Cookie'] ?? '';
    merged['Cookie'] = existing.isEmpty ? jar : '$existing; $jar';
    return merged;
  }

  static Map<String, String> headers({
    SitePlatform? platform,
    String? ua,
    Map<String, String>? extra,
  }) {
    final h = <String, String>{
      'User-Agent': ua ?? uaDesktop,
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    };
    if (platform != null) {
      h['Referer'] = refererOf(platform);
      final cookie = cookies[platform];
      if (cookie != null && cookie.isNotEmpty) h['Cookie'] = cookie;
    }
    if (extra != null) h.addAll(extra);
    return h;
  }

  static Future<String> getText(
    String url, {
    SitePlatform? platform,
    String? ua,
    Map<String, String>? extra,
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final client = newClient();
    try {
      final req = await client.getUrl(Uri.parse(url));
      _merge(url, headers(platform: platform, ua: ua, extra: extra))
          .forEach(req.headers.set);
      final resp = await req.close().timeout(timeout);
      _absorb(url, resp);
      final body = await resp.transform(utf8.decoder).join();
      if (resp.statusCode >= 400) {
        throw HttpException('HTTP ${resp.statusCode}', uri: Uri.parse(url));
      }
      return body;
    } finally {
      client.close(force: true);
    }
  }

  static Future<dynamic> getJson(
    String url, {
    SitePlatform? platform,
    String? ua,
    Map<String, String>? extra,
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final text = await getText(url,
        platform: platform, ua: ua, extra: extra, timeout: timeout);
    return jsonDecode(text);
  }

  /// 探测远程文件总字节数（Range: bytes=0-0 读 Content-Range）。
  ///
  /// 抖音同一 video_id 换 ratio 会返回不同清晰度，用总长能判断「这档是否真的存在」：
  /// 大小与更高档相同说明服务端回落了，不能当作独立档位。失败返回 0。
  static Future<int> probeSize(
    String url, {
    SitePlatform? platform,
    String? ua,
    Map<String, String>? extra,
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final client = newClient(connectTimeout: const Duration(seconds: 12));
    try {
      final req = await client.getUrl(Uri.parse(url));
      _merge(url, headers(platform: platform, ua: ua, extra: extra))
          .forEach(req.headers.set);
      req.headers.set('Range', 'bytes=0-0');
      final resp = await req.close().timeout(timeout);
      await resp.drain<void>();
      final range = resp.headers.value('content-range') ?? '';
      final slash = range.lastIndexOf('/');
      if (slash >= 0 && slash < range.length - 1) {
        final total = int.tryParse(range.substring(slash + 1));
        if (total != null && total > 0) return total;
      }
      final len = resp.contentLength;
      return len > 0 ? len : 0;
    } catch (_) {
      return 0;
    } finally {
      client.close(force: true);
    }
  }

  /// 跟随跳转拿最终 URL（短链解析用）；最多 5 跳。
  static Future<String> resolveFinalUrl(
    String url, {
    SitePlatform? platform,
    String? ua,
  }) async {
    var current = url;
    final client = newClient();
    try {
      for (var i = 0; i < 5; i++) {
        final req = await client.getUrl(Uri.parse(current));
        req.followRedirects = false;
        _merge(current, headers(platform: platform, ua: ua))
            .forEach(req.headers.set);
        final resp = await req.close().timeout(const Duration(seconds: 15));
        _absorb(current, resp);
        await resp.drain<void>();
        final location = resp.headers.value('location');
        if (location == null || location.isEmpty) return current;
        current = Uri.parse(current).resolve(location).toString();
      }
      return current;
    } finally {
      client.close(force: true);
    }
  }
}
