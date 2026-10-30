import 'package:vdl/shared/site.dart';

/// 链接识别：从一整段分享文案里抠出 URL，并判断属于哪个站点。
///
/// 真实分享文案经常**没有协议头**（例如从 App 里复制出来就是
/// "v.douyin.com/jx6dSylJqFU/ gbA:/ 12/12"），所以这里两条路都要走：
///   1. 先找带 http(s):// 的地址；
///   2. 找不到再扫裸域名（host.tld/path），命中已知站点就补上 https:// 。
abstract final class LinkDetect {
  /// 带协议的完整地址（遇空白或中文标点即止）。
  static final _schemeRe = RegExp(
    'https?://[^\\s\\u4e00-\\u9fff，。！？；：、（）【】《》"\x27]+',
    caseSensitive: false,
  );

  /// 裸域名 + 路径：必须带 "/"，避免把 "d@N.jp"、版本号这类碎片当成链接。
  static final _bareRe = RegExp(
    '(?:[a-z0-9-]+\\.)+[a-z]{2,}(?::\\d+)?/[^\\s\\u4e00-\\u9fff，。！？；：、（）【】《》"\x27]*',
    caseSensitive: false,
  );

  /// 文末常见被吞进来的标点。
  static const _trailing = '),.;:!?，。；：！？）】》」”\x27"›»';

  static String? extract(String text) {
    final t = text.trim();
    if (t.isEmpty) return null;

    final withScheme = _schemeRe.firstMatch(t);
    if (withScheme != null) {
      final url = _trim(withScheme.group(0)!);
      return url.isEmpty ? null : url;
    }

    // 先挑「认得出站点」的那一条，避免前面还混着别的域名
    for (final m in _bareRe.allMatches(t)) {
      final candidate = 'https://' + _trim(m.group(0)!);
      if (platformOf(candidate) != null) return candidate;
    }
    final bare = _bareRe.firstMatch(t);
    if (bare != null) {
      final url = _trim(bare.group(0)!);
      return url.isEmpty ? null : 'https://' + url;
    }
    return null;
  }

  /// 补全协议头（裸域名变成 https://）。
  static String normalize(String url) {
    final u = url.trim();
    if (u.isEmpty) return u;
    if (u.startsWith('http://') || u.startsWith('https://')) return u;
    return 'https://${u.replaceFirst(RegExp(r'^/+'), '')}';
  }

  static String _trim(String url) {
    var u = url;
    while (u.isNotEmpty && _trailing.contains(u[u.length - 1])) {
      u = u.substring(0, u.length - 1);
    }
    return u;
  }

  /// 从 URL 判断站点；识别不了返回 null。
  static SitePlatform? platformOf(String url) {
    final host = _host(normalize(url));
    if (host.isEmpty) return null;
    if (host == 'iesdouyin.com' || host.endsWith('.iesdouyin.com')) {
      return SitePlatform.douyin;
    }
    if (host.endsWith('douyin.com')) return SitePlatform.douyin;
    // 小红书：站内域名 + App 分享用的短链（xhslink.com / xhslink.cn）
    if (host.endsWith('xiaohongshu.com') ||
        host.endsWith('xhslink.com') ||
        host.endsWith('xhslink.cn')) {
      return SitePlatform.xhs;
    }
    if (host.contains('bilibili.com') || host.contains('b23.tv')) {
      return SitePlatform.bili;
    }
    // X（推特）：站内域名 + t.co 短链
    if (host == 'x.com' ||
        host.endsWith('.x.com') ||
        host.contains('twitter.com') ||
        host == 't.co' ||
        host.endsWith('.t.co')) {
      return SitePlatform.x;
    }
    if (host.contains('iwara.tv')) return SitePlatform.iwara;
    if (host.contains('pornhub.com')) return SitePlatform.pornhub;
    if (host.contains('hanime1.me')) return SitePlatform.hanime1;
    // 禁漫：镜像域名很多，按特征词匹配（18comic*、jmcomic*、comic18*、18h.mm）
    if (host.contains('18comic') ||
        host.contains('jmcomic') ||
        host.contains('comic18') ||
        host == '18h.mm' ||
        host.endsWith('.18h.mm')) {
      return SitePlatform.jm;
    }
    return null;
  }

  static String _host(String url) {
    try {
      return Uri.parse(url).host.toLowerCase();
    } catch (_) {
      return '';
    }
  }

  /// 是否像一条可直接下载的媒体直链（mp4/m3u8 等）。
  static bool isDirectMedia(String url) {
    final path = Uri.tryParse(normalize(url))?.path.toLowerCase() ?? '';
    return RegExp(r'\.(mp4|mkv|webm|mov|m4v|flv|ts|m3u8)$').hasMatch(path);
  }
}
