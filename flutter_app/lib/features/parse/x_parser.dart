import 'dart:convert';
import 'dart:io';

import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

import 'media_info.dart';

/// X（推特）解析：走 cdn.syndication.twimg.com 的公开接口，**免登录**。
///
/// 关键点（与桌面端 parse_x 的结论一致）：
///   1. t.co 短链要先跟随跳转，才能拿到 /status/<id>；
///   2. syndication 接口要带 token：((id/1e15)*PI) 转 36 进制，去掉 0 与小数点；
///      UA 用 Googlebot —— 普通浏览器 UA 会被拒，敏感内容也大多能取到；
///   3. 视频 variants 里 **没有 bitrate**（实测全 undefined），
///      分辨率只能从地址路径的 /1920x1080/ 抠；HLS(m3u8) 变体跳过；
///   4. 接口不给字节数，所以解析后再对每个档位发一次 Range 探测拿真实大小；
///   5. 敏感/受限推文只回 TweetTombstone，游客态拿不到，直接给明确提示。
class XParser {
  const XParser();

  static final _statusRe = RegExp(r'/status(?:es)?/(\d+)');
  static final _sizeRe = RegExp(r'/(\d{2,5})x(\d{2,5})/');

  /// syndication 接口只认爬虫 UA（普通 UA 会 403）。
  static const _crawlerUa = 'Googlebot';

  /// X 网页端的公开 Bearer（前端常量，不是用户凭证）。
  static const _bearer = 'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
      '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA';

  /// TweetResultByRestId 的 queryId 会随前端轮换：前两个当前有效，全部失败才报错。
  static const _gqlQueryIds = <String>[
    'Xl0tsHf4AzflMRjbw9e70A',
    '2ICDjqPd81tulZcYrtpTuQ',
    'nBS-WpgA6ZG0CyNHD517JQ',
    'xOhkmRac04YFZmOzU9PJHg',
  ];

  /// GraphQL features：与 x.com 前端一致，少一项就可能 422。
  static const _gqlFeatures = <String, bool>{
    'creator_subscriptions_tweet_preview_api_enabled': true,
    'tweetypie_unmention_optimization_enabled': true,
    'responsive_web_edit_tweet_api_enabled': true,
    'graphql_is_translatable_rweb_tweet_is_translatable_enabled': true,
    'view_counts_everywhere_api_enabled': true,
    'longform_notetweets_consumption_enabled': true,
    'responsive_web_twitter_article_tweet_consumption_enabled': false,
    'tweet_awards_web_tipping_enabled': false,
    'freedom_of_speech_not_reach_fetch_enabled': true,
    'standardized_nudges_misinfo': true,
    'tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled': true,
    'longform_notetweets_rich_text_read_enabled': true,
    'longform_notetweets_inline_media_enabled': true,
    'responsive_web_graphql_exclude_directive_enabled': true,
    'verified_phone_label_enabled': false,
    'responsive_web_media_download_video_enabled': false,
    'responsive_web_graphql_skip_user_profile_image_extensions_enabled': false,
    'responsive_web_graphql_timeline_navigation_enabled': true,
    'responsive_web_enhance_cards_enabled': false,
  };

  Future<MediaInfo> parse(String rawUrl) async {
    var url = rawUrl.trim();
    if (url.isEmpty) throw ParseException('先粘贴 X（推特）推文链接');

    var id = _idOf(url);
    if (id == null) {
      // t.co 短链 / 别的形态：先跟随跳转
      url = await _expand(url);
      id = _idOf(url);
    }
    if (id == null) {
      throw ParseException('没识别到推文 ID，请用单条推文链接（x.com/用户名/status/…）');
    }

    final cookie = AppHttp.cookies[SitePlatform.x] ?? '';
    final authToken = _cookieValue(cookie, 'auth_token');
    final ct0 = _cookieValue(cookie, 'ct0');
    final loggedIn = authToken.isNotEmpty;

    Object? lastError;

    // 1) 已登录：GraphQL 带 Cookie —— 敏感/受限推文的媒体只在这条路上
    if (loggedIn) {
      try {
        final result = await _graphql(id, cookie: cookie, csrf: ct0);
        final info = buildFromGraphql(result, id);
        if (info != null) return await _withExactSizes(info);
        lastError = ParseException(
            'GraphQL 返回 ' + (result['__typename'] ?? '空结果').toString());
      } catch (error) {
        lastError = error;
      }
    }

    // 2) syndication：普通推文最完整（多图、转推都能展开）
    try {
      final data = await _tweetResult(id);
      return await _withExactSizes(buildInfo(data, id));
    } catch (error) {
      lastError = error;
    }

    // 3) 未登录时再用游客令牌试一次 GraphQL
    if (!loggedIn) {
      try {
        final result = await _graphql(id);
        final info = buildFromGraphql(result, id);
        if (info != null) return await _withExactSizes(info);
      } catch (error) {
        lastError = error;
      }
    }

    final Object failure = lastError;
    final reason = failure.toString();
    if (cookie.isNotEmpty && !loggedIn) {
      throw ParseException('设置里的 X Cookie 没有 auth_token，等于没登录。'
          '请重新复制完整 Cookie（至少含 auth_token 与 ct0）后再试（' + reason + '）');
    }
    if (!loggedIn) {
      throw ParseException('解析失败：这条推文可能被标记为敏感/受限内容，游客态取不到。'
          '请到「设置 → 账号 → X（推特）」粘贴含 auth_token 与 ct0 的 Cookie 后重试'
          '（' + reason + '）');
    }
    throw ParseException('解析失败：' + reason +
        '。如果 Cookie 已过期，请到设置里重新粘贴一次');
  }

  /// GraphQL 的 tweetResult.result → MediaInfo；拿不到媒体返回 null。
  MediaInfo? buildFromGraphql(Map<String, dynamic> result, String id) {
    final typename = (result['__typename'] ?? '').toString();
    // 墓碑 = 敏感/受限，游客态永远拿不到；由调用方按「有没有登录」给提示
    if (typename == 'TweetTombstone' || typename == 'TweetUnavailable') {
      return null;
    }
    var node = result;
    if (typename == 'TweetWithVisibilityResults') {
      final inner = result['tweet'];
      if (inner is Map) node = inner.cast<String, dynamic>();
    }

    var legacy = _legacyOf(node);
    var media = _mediaOf(legacy);
    // 转推：媒体挂在被转推的原推文里
    if (media.isEmpty) {
      final retweet = legacy['retweeted_status_result'];
      if (retweet is Map) {
        final inner = retweet['result'];
        if (inner is Map) {
          var innerNode = inner.cast<String, dynamic>();
          if ((innerNode['__typename'] ?? '').toString() ==
              'TweetWithVisibilityResults') {
            final wrapped = innerNode['tweet'];
            if (wrapped is Map) innerNode = wrapped.cast<String, dynamic>();
          }
          final innerLegacy = _legacyOf(innerNode);
          final innerMedia = _mediaOf(innerLegacy);
          if (innerMedia.isNotEmpty) {
            legacy = innerLegacy;
            media = innerMedia;
          }
        }
      }
    }
    if (media.isEmpty) return null;

    final author = _graphqlAuthor(node);
    final text = cleanTitle(
        (legacy['full_text'] ?? legacy['text'] ?? '').toString());
    final qualities = <MediaQuality>[];
    final images = <String>[];
    var durationMs = 0;
    for (final raw in media) {
      if (raw is! Map) continue;
      final item = raw.cast<String, dynamic>();
      if ((item['type'] ?? '').toString() == 'photo') {
        final url = _cleanUrl((item['media_url_https'] ?? '').toString());
        if (url.isNotEmpty && !images.contains(url)) images.add(url);
        continue;
      }
      final videoInfo = item['video_info'];
      if (videoInfo is! Map) continue;
      final millis = _asInt(videoInfo['duration_millis']);
      if (millis > durationMs) durationMs = millis;
      final variants = videoInfo['variants'];
      if (variants is List) {
        qualities.addAll(_qualitiesFromVariants(variants));
      }
    }

    if (qualities.isEmpty && images.isEmpty) return null;
    var cover = images.isNotEmpty ? images.first : '';
    if (cover.isEmpty) {
      final first = media.first;
      if (first is Map) {
        cover = _cleanUrl((first['media_url_https'] ?? '').toString());
      }
    }
    return MediaInfo(
      platform: SitePlatform.x,
      title: text.isEmpty ? 'X 推文' : text,
      author: author,
      coverUrl: cover,
      durationMs: durationMs,
      qualities: qualities,
      images: qualities.isEmpty ? images : const [],
      sourceId: id,
    );
  }

  /// 从 variants 里挑 mp4 档位：跳过 HLS(m3u8)，按分辨率降序去重。
  ///
  /// syndication 用 type/src、GraphQL 用 content_type/url，两种写法都认。
  List<MediaQuality> _qualitiesFromVariants(List<dynamic> variants) {
    final entries = <_Variant>[];
    for (final raw in variants) {
      if (raw is! Map) continue;
      final type = (raw['content_type'] ?? raw['type'] ?? '').toString();
      if (!type.startsWith('video/mp4')) continue;
      final src = _cleanUrl((raw['url'] ?? raw['src'] ?? '').toString());
      if (src.isEmpty || entries.any((e) => e.url == src)) continue;
      final match = _sizeRe.firstMatch(src);
      final width = match == null ? 0 : (int.tryParse(match.group(1)!) ?? 0);
      final height = match == null ? 0 : (int.tryParse(match.group(2)!) ?? 0);
      entries.add(_Variant(width, height, src));
    }
    entries.sort((a, b) => (b.width * b.height).compareTo(a.width * a.height));
    return [
      for (final entry in entries)
        MediaQuality(label: entry.label, url: entry.url),
    ];
  }

  /// TweetResultByRestId：登录态带 Cookie，游客态先拿 guest token。
  Future<Map<String, dynamic>> _graphql(
    String id, {
    String cookie = '',
    String csrf = '',
  }) async {
    final headers = <String, String>{
      'authorization': 'Bearer ' + _bearer,
      'content-type': 'application/json',
      'Accept': 'application/json',
    };
    if (cookie.isNotEmpty) {
      headers['Cookie'] = cookie;
      if (csrf.isNotEmpty) headers['x-csrf-token'] = csrf;
    } else {
      final guest = await _guestToken();
      if (guest.isNotEmpty) headers['x-guest-token'] = guest;
    }

    final query = Uri(queryParameters: {
      'variables': jsonEncode(<String, dynamic>{
        'tweetId': id,
        'withCommunity': false,
        'includePromotedContent': false,
        'withVoice': false,
      }),
      'features': jsonEncode(_gqlFeatures),
      'fieldToggles': jsonEncode(<String, dynamic>{
        'withArticleRichContentState': false,
      }),
    }).query;

    Object? lastError;
    for (final queryId in _gqlQueryIds) {
      final url =
          'https://api.x.com/graphql/' + queryId + '/TweetResultByRestId?' + query;
      try {
        final body = await AppHttp.getText(
          url,
          ua: AppHttp.uaDesktop,
          timeout: const Duration(seconds: 12),
          extra: headers,
        );
        final json = jsonDecode(body);
        if (json is! Map) continue;
        final data = json['data'];
        if (data is! Map) continue;
        final tweetResult = data['tweetResult'];
        if (tweetResult is! Map) continue;
        final result = tweetResult['result'];
        if (result is! Map) continue;
        return result.cast<String, dynamic>();
      } catch (error) {
        // 422/400/404 基本都是 queryId 轮换，换下一个再试
        lastError = error;
        continue;
      }
    }
    throw ParseException('X 接口没有返回可用数据' +
        (lastError == null ? '' : '（' + lastError.toString() + '）'));
  }

  /// 游客令牌：POST /1.1/guest/activate.json（AppHttp 只有 GET，这里用 HttpClient）。
  Future<String> _guestToken() async {
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 10);
    try {
      final request = await client.postUrl(
          Uri.parse('https://api.x.com/1.1/guest/activate.json'));
      request.headers.set('User-Agent', AppHttp.uaDesktop);
      request.headers.set('authorization', 'Bearer ' + _bearer);
      request.headers.set('content-type', 'application/json');
      final response =
          await request.close().timeout(const Duration(seconds: 10));
      final body = await response.transform(utf8.decoder).join();
      final json = jsonDecode(body);
      if (json is Map) return (json['guest_token'] ?? '').toString();
      return '';
    } catch (_) {
      return '';
    } finally {
      client.close(force: true);
    }
  }

  static Map<String, dynamic> _legacyOf(Map<String, dynamic> node) {
    final legacy = node['legacy'];
    return legacy is Map ? legacy.cast<String, dynamic>() : <String, dynamic>{};
  }

  static List<dynamic> _mediaOf(Map<String, dynamic> legacy) {
    final entities = legacy['extended_entities'];
    if (entities is Map) {
      final media = entities['media'];
      if (media is List) return media;
    }
    final basic = legacy['entities'];
    if (basic is Map) {
      final media = basic['media'];
      if (media is List) return media;
    }
    return const [];
  }

  static String _graphqlAuthor(Map<String, dynamic> node) {
    final core = node['core'];
    if (core is Map) {
      final userResults = core['user_results'];
      if (userResults is Map) {
        final result = userResults['result'];
        if (result is Map) {
          final innerCore = result['core'];
          if (innerCore is Map) {
            final name = (innerCore['name'] ?? '').toString();
            if (name.isNotEmpty) return name;
            return (innerCore['screen_name'] ?? '').toString();
          }
          final legacy = result['legacy'];
          if (legacy is Map) return (legacy['name'] ?? '').toString();
        }
      }
    }
    return '';
  }

  /// 从 Cookie 串里取某个键（形如 a=1; b=2）。
  static String _cookieValue(String cookie, String name) {
    if (cookie.isEmpty) return '';
    for (final part in cookie.split(';')) {
      final index = part.indexOf('=');
      if (index <= 0) continue;
      if (part.substring(0, index).trim() == name) {
        return part.substring(index + 1).trim();
      }
    }
    return '';
  }
  /// 用 syndication 的 JSON 拼出 MediaInfo。抽成公开方法便于离线测试。
  MediaInfo buildInfo(Map<String, dynamic> data, String id) {
    final user = data['user'];
    final author = user is Map
        ? ((user['name'] ?? user['screen_name']) ?? '').toString()
        : '';
    final text = cleanTitle((data['text'] ?? data['full_text'] ?? '').toString());

    final video = data['video'];
    final qualities = <MediaQuality>[];
    var durationMs = 0;
    if (video is Map) {
      durationMs = _asInt(video['durationMs']);
      final variants = video['variants'];
      if (variants is List) {
        final entries = <_Variant>[];
        for (final raw in variants) {
          if (raw is! Map) continue;
          final type = (raw['type'] ?? '').toString();
          // 只收 mp4；m3u8 播放列表这里下不了，直接跳过
          if (!type.startsWith('video/mp4')) continue;
          final src = _cleanUrl((raw['src'] ?? '').toString());
          if (src.isEmpty) continue;
          if (entries.any((e) => e.url == src)) continue;
          final match = _sizeRe.firstMatch(src);
          final width = match == null ? 0 : (int.tryParse(match.group(1)!) ?? 0);
          final height = match == null ? 0 : (int.tryParse(match.group(2)!) ?? 0);
          entries.add(_Variant(width, height, src));
        }
        // 按像素量降序：X 不给我们码率，只能靠分辨率判断高低
        entries.sort((a, b) => (b.width * b.height).compareTo(a.width * a.height));
        for (final entry in entries) {
          qualities.add(MediaQuality(label: entry.label, url: entry.url));
        }
      }
    }

    final images = <String>[];
    final photos = data['photos'];
    if (photos is List) {
      for (final raw in photos) {
        if (raw is! Map) continue;
        final url = _cleanUrl((raw['url'] ?? '').toString());
        if (url.isNotEmpty && !images.contains(url)) images.add(url);
      }
    }

    var cover = images.isNotEmpty ? images.first : '';
    final details = data['mediaDetails'];
    if (details is List && details.isNotEmpty) {
      if (cover.isEmpty) {
        final first = details.first;
        if (first is Map) {
          cover = _cleanUrl((first['media_url_https'] ?? '').toString());
        }
      }
      // 老接口没有 photos 时，从 mediaDetails 里补图片
      if (images.isEmpty) {
        for (final raw in details) {
          if (raw is! Map) continue;
          if ((raw['type'] ?? '').toString() != 'photo') continue;
          final url = _cleanUrl((raw['media_url_https'] ?? '').toString());
          if (url.isNotEmpty && !images.contains(url)) images.add(url);
        }
      }
    }

    if (qualities.isEmpty && images.isEmpty) {
      throw ParseException('这条推文里没有可下载的视频或图片');
    }

    return MediaInfo(
      platform: SitePlatform.x,
      title: text.isEmpty ? 'X 推文' : text,
      author: author,
      coverUrl: cover,
      durationMs: durationMs,
      qualities: qualities,
      // 有视频就只给视频档位（与抖音/小红书一致），纯图推文才走图集
      images: qualities.isEmpty ? images : const [],
      sourceId: id,
    );
  }

  /// 正文里常粘着自身的 t.co 短链，去掉；顺手清掉多余空白并限长。
  static String cleanTitle(String raw) {
    var text = raw;
    final shortLink = text.indexOf('https://t.co/');
    if (shortLink > 0) text = text.substring(0, shortLink);
    text = text.replaceAll(RegExp(r'https?://\S+'), ' ');
    text = text.replaceAll(RegExp(r'\s+'), ' ').trim();
    if (text.length > 100) text = text.substring(0, 100).trim();
    return text;
  }

  /// syndication 接口的 token：((id/1e15)*PI) 转 36 进制，再去掉 0 与小数点。
  static String syndicationToken(String id) {
    const digits = '0123456789abcdefghijklmnopqrstuvwxyz';
    final tweetId = int.tryParse(id) ?? 0;
    final value = (tweetId / 1e15) * 3.141592653589793;
    var whole = value.floor();
    var fraction = value - whole;
    var text = whole == 0 ? '0' : '';
    while (whole > 0) {
      text = digits[whole % 36] + text;
      whole ~/= 36;
    }
    if (fraction > 0) {
      text = text + '.';
      for (var i = 0; i < 20; i++) {
        fraction *= 36;
        final digit = fraction.floor();
        text = text + digits[digit];
        fraction -= digit;
        if (fraction <= 0) break;
      }
    }
    return text.replaceAll('0', '').replaceAll('.', '');
  }

  Future<Map<String, dynamic>> _tweetResult(String id) async {
    final url = 'https://cdn.syndication.twimg.com/tweet-result?id=' + id +
        '&token=' + syndicationToken(id);
    String body;
    try {
      body = await AppHttp.getText(
        url,
        ua: _crawlerUa,
        timeout: const Duration(seconds: 12),
        extra: const {'Accept': 'application/json'},
      );
    } catch (error) {
      throw ParseException('取推文失败（可能已删除、设为私密，或链接不对）：'
          + error.toString());
    }
    final json = jsonDecode(body);
    if (json is! Map) throw ParseException('推文接口返回格式异常');
    final data = json.cast<String, dynamic>();
    if ((data['__typename'] ?? '').toString() == 'TweetTombstone') {
      throw ParseException('这条推文是 X 的敏感/受限内容，游客态取不到，'
          '需要登录态才能解析');
    }
    return data;
  }

  /// 接口不给字节数：对每个档位发一次 Range 探测，拿真实大小写进标签。
  Future<MediaInfo> _withExactSizes(MediaInfo info) async {
    if (info.qualities.isEmpty) return info;
    final sizes = await Future.wait([
      for (final quality in info.qualities)
        AppHttp.probeSize(
          quality.url,
          timeout: const Duration(seconds: 10),
        ),
    ]);
    final qualities = <MediaQuality>[];
    for (var i = 0; i < info.qualities.length; i++) {
      final quality = info.qualities[i];
      final size = sizes[i];
      var label = quality.label;
      if (size > 0) {
        label = label + ' · ' + (size / 1024 / 1024).toStringAsFixed(1) + 'MB';
      }
      qualities.add(MediaQuality(
        label: label,
        url: quality.url,
        sizeBytes: size > 0 ? size : quality.sizeBytes,
      ));
    }
    return MediaInfo(
      platform: info.platform,
      title: info.title,
      author: info.author,
      coverUrl: info.coverUrl,
      durationMs: info.durationMs,
      qualities: qualities,
      images: info.images,
      sourceId: info.sourceId,
    );
  }

  static String? _idOf(String url) => _statusRe.firstMatch(url)?.group(1);

  static String _hostOf(String url) {
    final text = url.trim();
    final withScheme = text.contains('://')
        ? text
        : 'https://' + text.replaceFirst(RegExp(r'^/+'), '');
    try {
      return Uri.parse(withScheme).host.toLowerCase();
    } catch (_) {
      return '';
    }
  }

  Future<String> _expand(String url) async {
    try {
      return await AppHttp.resolveFinalUrl(
        url,
        platform: SitePlatform.x,
        ua: AppHttp.uaDesktop,
      );
    } catch (error) {
      // 短链展开失败不必直接报错：调用方会看到「没识别到推文 ID」
      if (_hostOf(url) == 't.co') {
        throw ParseException('t.co 短链没能展开（可能已失效）：' + error.toString());
      }
      return url;
    }
  }

  /// 清掉 JSON 转义与前后空白。
  static String _cleanUrl(String value) {
    var url = value
        .replaceAll(r'\u0026', '&')
        .replaceAll(r'\/', '/')
        .trim();
    if (url.startsWith('//')) url = 'https:' + url;
    return url;
  }

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }
}

class _Variant {
  _Variant(this.width, this.height, this.url);

  final int width;
  final int height;
  final String url;

  String get label => width > 0 && height > 0
      ? width.toString() + '×' + height.toString()
      : '视频';
}
