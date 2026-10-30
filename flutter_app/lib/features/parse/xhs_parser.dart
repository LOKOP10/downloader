import 'dart:convert';

import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

import 'media_info.dart';

/// 小红书解析：纯 HTTP，读笔记详情页里的服务端渲染数据
/// （window.__INITIAL_STATE__），不需要 WebView。
///
/// 关键点（与旧 Kotlin 版 XiaohongshuParser 的实测结论一致）：
///   1. 分享短链（xhslink.com / xhslink.cn）必须跟随跳转拿最终地址；
///   2. 最终地址里的 xsec_token 要原样保留，去掉后详情页只回空壳；
///   3. SSR 的 JSON 里有裸 undefined / NaN，整体 decode 会失败，
///      所以先做「值位」替换，再退回「按键定位 + 括号配对」逐块提取；
///   4. 桌面 UA 拿到的 SSR 最全，移动 UA 常常只给骨架；
///   5. 超时给 12s：失败要尽快报错，不能让人看着「点了解析一直没反应」。
class XhsParser {
  const XhsParser();

  /// 笔记 ID：24 位十六进制，出现在 /explore/、/discovery/item/、/item/ 之后。
  static final _noteIdRe =
      RegExp(r'/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})');
  static final _bareIdRe = RegExp(r'/([0-9a-fA-F]{24})(?:[/?#]|$)');

  /// SSR JSON 里的裸 undefined / NaN（只在「值」的位置替换，避免误伤字符串）。
  static final _invalidRe =
      RegExp(r'(?<=[:,\[])\s*(?:undefined|NaN)(?=\s*[,}\]])');

  /// 详情页请求超时（照 Kotlin 端 1.0.51 的教训：默认 60s 会让人以为卡死）。
  static const _pageTimeout = Duration(seconds: 12);

  Future<MediaInfo> parse(String rawUrl) async {
    final trimmed = rawUrl.trim();
    if (trimmed.isEmpty) throw ParseException('先粘贴小红书笔记链接');

    var url = trimmed;
    var id = _idOf(url);
    final isShortLink = _isShortLink(_hostOf(url));
    // 短链（App 分享出来的就是这个）：先跟随跳转拿到带 xsec_token 的最终地址
    if (isShortLink) {
      url = await _expand(url);
      id = _idOf(url) ?? id;
    }
    if (id == null) {
      // 长链但形状不认识：再展开一次，看看是不是包了跳转
      final finalUrl = await _expand(url);
      if (finalUrl != url) {
        url = finalUrl;
        id = _idOf(url);
      }
    }
    if (id == null) {
      if (url.contains('/user/')) {
        throw ParseException('这是用户主页链接，请用单条笔记的分享链接');
      }
      throw ParseException(isShortLink
          ? '短链没能展开出笔记地址（可能已失效），请重新从 App 分享一次'
          : '请用小红书 App 分享的单条笔记链接（形如 xhslink.com/…）');
    }

    // 先按原地址（带 xsec_token）取，再退回不带 token 的 explore 页
    final candidates = <String>[
      url,
      if (!url.contains('xiaohongshu.com/explore/'))
        'https://www.xiaohongshu.com/explore/$id',
    ];
    Object? lastError;
    for (final target in candidates) {
      for (final ua in const [AppHttp.uaDesktop, AppHttp.uaMobile]) {
        try {
          final html = await AppHttp.getText(
            target,
            platform: SitePlatform.xhs,
            ua: ua,
            timeout: _pageTimeout,
          );
          final info = parseHtml(html, id);
          if (info != null) return info;
        } catch (error) {
          lastError = error;
        }
      }
    }
    throw ParseException('没取到笔记的媒体地址（可能链接已过期或需要登录）：'
        '请重新从 App 分享一次，或在设置里填小红书 Cookie 后重试'
        '${lastError == null ? '' : '（$lastError）'}');
  }

  /// 从详情页 HTML 里解析出媒体信息。抽成 public 便于离线测试。
  MediaInfo? parseHtml(String html, String id) {
    if (html.trim().isEmpty) return null;

    // 1) 整体 __INITIAL_STATE__（正常情况走这条）
    final state = _extractState(html);
    if (state != null) {
      final card = _findNote(state, id) ?? _findNote(state, '');
      if (card != null) {
        final info = _fromCard(card);
        if (info != null) return info;
      }
    }

    // 2) 兜底：按键定位 + 括号配对，逐块抠对象（JSON 整体坏掉时用）
    for (final key in const ['"noteCard"', '"note"', '"noteDetailMap"']) {
      var pos = html.indexOf(key);
      var guard = 0;
      while (pos >= 0 && guard++ < 40) {
        final start = html.indexOf('{', pos);
        if (start < 0 || start - pos > 100) {
          pos = html.indexOf(key, pos + key.length);
          continue;
        }
        final end = _matchBrace(html, start);
        if (end > start) {
          final raw =
              html.substring(start, end + 1).replaceAll(_invalidRe, 'null');
          try {
            final decoded = jsonDecode(raw);
            if (decoded is Map) {
              final card = _findNote(decoded, id) ?? _findNote(decoded, '');
              if (card != null) {
                final info = _fromCard(card);
                if (info != null) return info;
              }
            }
          } catch (_) {
            // 这一块不是合法 JSON，继续找下一个
          }
        }
        pos = html.indexOf(key, pos + key.length);
      }
    }
    return null;
  }

  /// 从字符串里抠出配平的 {...}，正确处理引号与转义。
  static int _matchBrace(String text, int start, {int limit = 400000}) {
    var depth = 0;
    var quoted = false;
    var escaped = false;
    final end = start + limit < text.length ? start + limit : text.length;
    for (var i = start; i < end; i++) {
      final unit = text.codeUnitAt(i);
      if (quoted) {
        if (escaped) {
          escaped = false;
        } else if (unit == 0x5C) {
          escaped = true;
        } else if (unit == 0x22) {
          quoted = false;
        }
      } else if (unit == 0x22) {
        quoted = true;
      } else if (unit == 0x7B) {
        depth++;
      } else if (unit == 0x7D) {
        depth--;
        if (depth == 0) return i;
      }
    }
    return -1;
  }

  Map<String, dynamic>? _extractState(String html) {
    final idx = html.indexOf('window.__INITIAL_STATE__');
    if (idx < 0) return null;
    final start = html.indexOf('{', idx);
    if (start < 0 || start - idx > 100) return null;
    final end = _matchBrace(html, start);
    if (end < 0) return null;
    final raw = html.substring(start, end + 1).replaceAll(_invalidRe, 'null');
    try {
      final json = jsonDecode(raw);
      if (json is Map) return json.cast<String, dynamic>();
    } catch (_) {}
    return null;
  }

  /// 深搜出「像一条笔记」的对象。
  ///
  /// id 非空时只认 ID 完全一致的；id 为空表示放宽（页面上没有 ID 时用，
  /// 例如 SSR 结构变化后只留了 noteCard）。这样详情页里夹着「推荐笔记」
  /// 时不会被抢先命中。
  Map<String, dynamic>? _findNote(Object? node, String id, [int depth = 0]) {
    if (node == null || depth > 14) return null;
    if (node is Map) {
      final map = node.cast<String, dynamic>();
      if (_looksLikeNote(map)) {
        final noteId =
            (map['noteId'] ?? map['note_id'] ?? map['id'] ?? '').toString();
        if (id.isEmpty || noteId.isEmpty || noteId == id) return map;
      }
      for (final value in map.values) {
        final hit = _findNote(value, id, depth + 1);
        if (hit != null) return hit;
      }
    } else if (node is List) {
      for (final value in node) {
        final hit = _findNote(value, id, depth + 1);
        if (hit != null) return hit;
      }
    }
    return null;
  }

  bool _looksLikeNote(Map<String, dynamic> map) {
    final images = map['imageList'];
    if (images is List && images.isNotEmpty) return true;
    final video = map['video'];
    if (video is Map) {
      final media = video['media'];
      if (media is Map && media['stream'] is Map) return true;
    }
    return false;
  }

  MediaInfo? _fromCard(Map<String, dynamic> card) {
    final noteId = (card['noteId'] ?? card['note_id'] ?? '').toString();
    if (noteId.isEmpty) return null;

    // 图片（图集用；视频笔记的 imageList 第一张就是封面）
    final images = <String>[];
    final list = card['imageList'] ?? card['image_list'];
    if (list is List) {
      for (final entry in list) {
        if (entry is! Map) continue;
        final url = _cleanUrl(_pickImageUrl(entry.cast<String, dynamic>()));
        if (url.startsWith('https://') && !images.contains(url)) {
          images.add(url);
        }
      }
    }

    // 视频档位：video.media.stream.<codec>[]
    final qualities = <MediaQuality>[];
    final video = card['video'];
    if (video is Map) {
      final media = video['media'];
      final stream = (media is Map) ? media['stream'] : null;
      if (stream is Map) {
        final labels = <String>{};
        for (final codecEntry in stream.entries) {
          final variants = codecEntry.value;
          if (variants is! List) continue;
          for (final raw in variants) {
            if (raw is! Map) continue;
            final variant = raw.cast<String, dynamic>();
            final urls = <String>[];
            final master = _cleanUrl((variant['masterUrl'] ?? '').toString());
            if (master.startsWith('https://')) urls.add(master);
            final backups = variant['backupUrls'];
            if (backups is List) {
              for (final backup in backups) {
                final url = _cleanUrl(backup.toString());
                if (url.startsWith('https://') && !urls.contains(url)) {
                  urls.add(url);
                }
              }
            }
            if (urls.isEmpty) continue;
            final w = _asInt(variant['width']);
            final h = _asInt(variant['height']);
            final codec = (variant['videoCodec'] ?? codecEntry.key).toString();
            final label = (w > 0 && h > 0) ? '$w×$h' : '视频';
            final key = '$label ($codec)';
            if (!labels.add(key)) continue;
            qualities.add(MediaQuality(
              label: key,
              url: urls.first,
              sizeBytes: _asInt(variant['size']),
            ));
          }
        }
      }
    }
    // 大文件 = 高码率，按体积降序
    qualities.sort((a, b) => b.sizeBytes.compareTo(a.sizeBytes));

    if (qualities.isEmpty && images.isEmpty) return null;

    final desc = (card['desc'] ?? '').toString().trim();
    var title = (card['title'] ?? '').toString().trim();
    if (title.isEmpty) {
      title = desc.length > 60 ? desc.substring(0, 60) : desc;
    }
    // 小红书正文里的「#话题[话题]#」是标记不是标题：直接当标题/文件夹名会是
    // 一长串井号，所以在解析阶段就洗干净。
    title = cleanTitle(title);
    if (title.isEmpty) title = '小红书笔记';
    final user = card['user'];
    final author = (user is Map) ? (user['nickname'] ?? '').toString() : '';

    return MediaInfo(
      platform: SitePlatform.xhs,
      title: title,
      author: author,
      coverUrl: images.isNotEmpty ? images.first : '',
      durationMs: _durationMs(video),
      qualities: qualities,
      images: qualities.isEmpty ? images : const [],
      sourceId: noteId,
    );
  }

  /// 图片取最清晰的一档：urlDefault（默认原图）→ urlPre → infoList 首项。
  String _pickImageUrl(Map<String, dynamic> image) {
    for (final key in const ['urlDefault', 'url_default', 'urlPre', 'url_pre']) {
      final value = (image[key] ?? '').toString();
      if (value.isNotEmpty) return value;
    }
    final infoList = image['infoList'] ?? image['info_list'];
    if (infoList is List) {
      for (final entry in infoList) {
        if (entry is! Map) continue;
        final url = (entry['url'] ?? '').toString();
        if (url.isNotEmpty) return url;
      }
    }
    return '';
  }

  int _durationMs(Object? video) {
    if (video is! Map) return 0;
    final capa = video['capa'];
    if (capa is Map) {
      final value = capa['duration'];
      if (value is num) return (value * 1000).round();
    }
    final direct = video['duration'];
    if (direct is num) return (direct * 1000).round();
    return 0;
  }

  /// 去掉正文里的话题标记，只留能给人看的标题。
  ///
  /// 例：「#职场冷知识科普[话题]# #符合[话题]# 今天的分享」→「今天的分享」。
  /// 只剩话题的笔记会返回空串，调用方回落到「小红书笔记」。
  static String cleanTitle(String raw) {
    var text = raw
        .replaceAll(RegExp(r'#[^#\s]{0,40}?\[话题\]#'), ' ')
        .replaceAll('[话题]', ' ')
        .replaceAll(RegExp(r'[#\[\]【】]'), ' ')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    // 去掉首尾剩下的分隔符
    text = text.replaceAll(RegExp(r'^[\s·|,，、]+|[\s·|,，、]+$'), '').trim();
    return text;
  }
  /// 修掉 SSR 里的转义斜杠与协议头（http:// 统一成 https://）。
  static String _cleanUrl(String value) {
    var url = value
        .replaceAll(r'\u002F', '/')
        .replaceAll(r'\u002f', '/')
        .replaceAll(r'\u0026', '&')
        .replaceAll(r'\/', '/')
        .replaceAll('&amp;', '&')
        .trim();
    if (url.startsWith('//')) url = 'https:$url';
    if (url.startsWith('http://')) {
      url = 'https://${url.substring('http://'.length)}';
    }
    return url;
  }

  static String _hostOf(String url) {
    final text = url.trim();
    final withScheme = text.contains('://')
        ? text
        : 'https://${text.replaceFirst(RegExp(r'^/+'), '')}';
    try {
      return Uri.parse(withScheme).host.toLowerCase();
    } catch (_) {
      return '';
    }
  }

  static bool _isShortLink(String host) =>
      host == 'xhslink.com' ||
      host.endsWith('.xhslink.com') ||
      host == 'xhslink.cn' ||
      host.endsWith('.xhslink.cn');

  static String? _idOf(String url) {
    final hit = _noteIdRe.firstMatch(url) ?? _bareIdRe.firstMatch(url);
    return hit?.group(1);
  }

  Future<String> _expand(String url) async {
    try {
      return await AppHttp.resolveFinalUrl(
        url,
        platform: SitePlatform.xhs,
        ua: AppHttp.uaDesktop,
      );
    } catch (_) {
      return url;
    }
  }

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }
}
