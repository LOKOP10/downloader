import 'dart:convert';

import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';
import 'media_info.dart';

/// 抖音解析：**纯 HTTP**，不依赖 WebView。
///
/// 关键点（实测得出，与 Kotlin 端注释一致）：
///   1. 冷会话请求分享页只会拿到 32KB 空壳（有 _ROUTER_DATA 但没有 play_addr）；
///   2. 第一次响应会下发 ttwid，**带这个 cookie 再请求同一页**就返回 41KB
///      完整数据，含 aweme_id / video.bit_rate / play_addr；
///   3. 所以最少要两轮请求，且必须共享 cookie（AppHttp 的进程内 cookie 罐负责）。
///
/// iteminfo / detail 两个老接口当前返回空响应，不再作为主路，仅作兜底。
class DouyinParser {
  const DouyinParser();

  static final _idRe = RegExp(r'/(?:video|note|slides)/(\d{15,20})');
  static final _anyIdRe = RegExp(r'(\d{15,20})');
  static final _routerRe =
      RegExp(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', dotAll: true);
  static final _renderRe = RegExp(
      '<script id="RENDER_DATA" type="application/json">(.*?)</script>',
      dotAll: true);

  Future<MediaInfo> parse(String rawUrl) async {
    var url = rawUrl.trim();
    var id = _idRe.firstMatch(url)?.group(1) ?? _anyIdRe.firstMatch(url)?.group(1);
    if (id == null) {
      final finalUrl = await AppHttp.resolveFinalUrl(
        url,
        platform: SitePlatform.douyin,
        ua: AppHttp.uaIphone,
      );
      if (finalUrl.contains('/user/')) {
        throw ParseException('这是用户主页链接，请用单个作品链接');
      }
      url = finalUrl;
      id = _idRe.firstMatch(url)?.group(1) ?? _anyIdRe.firstMatch(url)?.group(1);
    }
    if (id == null) throw ParseException('未识别到作品 ID，请用抖音分享链接');

    final isNote = url.contains('/note/') || url.contains('/slides/');
    // 两轮：第二轮带上一轮下发的 ttwid
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt++) {
      for (final page in isNote ? ['note', 'slides', 'video'] : ['video', 'note']) {
        final html = await _tryGet('https://www.iesdouyin.com/share/$page/$id/');
        if (html == null) continue;
        final json = _extractPageJson(html);
        if (json == null) continue;
        final item = _findItem(json, id);
        if (item == null) continue;
        final info = _buildInfo(item);
        return _expandQualities(info);
      }
      // 兜底：老接口（多数情况返回空，聊胜于无）
      try {
        final info = await _fromItemInfo(id);
        if (info != null) return info;
      } catch (error) {
        lastError = error;
      }
    }
    throw ParseException('作品信息获取失败：分享页与接口都没有返回数据'
        '${lastError == null ? '' : '（$lastError）'}');
  }

  Future<String?> _tryGet(String url) async {
    try {
      final body = await AppHttp.getText(
        url,
        platform: SitePlatform.douyin,
        ua: AppHttp.uaIphone,
        timeout: const Duration(seconds: 20),
      );
      return body.isEmpty ? null : body;
    } catch (_) {
      return null;
    }
  }

  Future<MediaInfo?> _fromItemInfo(String id) async {
    final body = await AppHttp.getText(
      'https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=$id',
      platform: SitePlatform.douyin,
      ua: AppHttp.uaIphone,
    );
    if (body.trim().isEmpty) return null;
    final json = jsonDecode(body);
    if (json is! Map) return null;
    final list = json['item_list'];
    if (list is! List || list.isEmpty) return null;
    final item = list.first;
    if (item is! Map) return null;
    return _buildInfo(item.cast<String, dynamic>());
  }

  /// 从页面里抠出 JSON：优先 RENDER_DATA（URL 编码），其次 _ROUTER_DATA。
  Map<String, dynamic>? _extractPageJson(String html) {
    final render = _renderRe.firstMatch(html);
    if (render != null) {
      try {
        final decoded = Uri.decodeComponent(render.group(1)!);
        final json = jsonDecode(decoded);
        if (json is Map) return json.cast<String, dynamic>();
      } catch (_) {}
    }
    final router = _routerRe.firstMatch(html);
    if (router != null) {
      try {
        final json = jsonDecode(router.group(1)!);
        if (json is Map) return json.cast<String, dynamic>();
      } catch (_) {}
    }
    return null;
  }

  /// 递归找作品对象（页面结构不固定，只认「有 video/images 且 aweme_id 对得上」）。
  Map<String, dynamic>? _findItem(Object? node, String id, [int depth = 0]) {
    if (node == null || depth > 12) return null;
    if (node is Map) {
      final map = node.cast<String, dynamic>();
      if (_hasMedia(map)) {
        final awemeId = (map['aweme_id'] ?? '').toString();
        if (id.isEmpty || awemeId.isEmpty || awemeId == id) return map;
      }
      for (final key in const ['item_list', 'itemList', 'aweme_list', 'aweme_detail']) {
        final value = map[key];
        if (value is List && value.isNotEmpty) {
          final hit = _findItem(value.first, id, depth + 1);
          if (hit != null) return hit;
        } else if (value is Map) {
          final hit = _findItem(value, id, depth + 1);
          if (hit != null) return hit;
        }
      }
      for (final value in map.values) {
        final hit = _findItem(value, id, depth + 1);
        if (hit != null) return hit;
      }
    } else if (node is List) {
      for (final value in node) {
        final hit = _findItem(value, id, depth + 1);
        if (hit != null) return hit;
      }
    }
    return null;
  }

  bool _hasMedia(Map<String, dynamic> item) {
    final video = item['video'];
    if (video is Map) {
      if (video['play_addr'] is Map) return true;
      final bitRate = video['bit_rate'];
      if (bitRate is List && bitRate.isNotEmpty) return true;
    }
    final images = item['images'];
    return images is List && images.isNotEmpty;
  }

  MediaInfo _buildInfo(Map<String, dynamic> item) {
    final video = (item['video'] is Map)
        ? (item['video'] as Map).cast<String, dynamic>()
        : <String, dynamic>{};

    // 图集
    final imageUrls = <String>[];
    final images = item['images'];
    if (images is List) {
      for (final entry in images) {
        if (entry is! Map) continue;
        final urls = entry['url_list'];
        if (urls is! List || urls.isEmpty) continue;
        // 桌面版实测「最后一个地址」最清晰
        imageUrls.add(urls.length > 1 ? urls.last.toString() : urls.first.toString());
      }
    }

    final qualities = <MediaQuality>[];
    String cover;

    if (imageUrls.isNotEmpty) {
      final coverUrls = (video['cover'] is Map) ? video['cover']['url_list'] : null;
      cover = (coverUrls is List && coverUrls.isNotEmpty)
          ? coverUrls.first.toString()
          : imageUrls.first;
    } else {
      final entries = <_Entry>[];
      final sizes = <String, int>{};
      final bitRate = video['bit_rate'];
      if (bitRate is List) {
        for (final raw in bitRate) {
          if (raw is! Map) continue;
          final entry = raw.cast<String, dynamic>();
          final playAddr = entry['play_addr'];
          if (playAddr is! Map) continue;
          final urls = playAddr['url_list'];
          if (urls is! List || urls.isEmpty) continue;
          final url = urls.first.toString();
          if (url.isEmpty) continue;
          final rate = _asInt(entry['bit_rate']);
          final w = _asInt(playAddr['width']);
          final h = _asInt(playAddr['height']);
          final fps = _asInt(entry['FPS']);
          final gear = (entry['gear_name'] ?? '').toString().replaceAll('_', ' ').trim();
          var label = w > 0 && h > 0
              ? '${w}×${h}'
              : h > 0
                  ? '${h}p'
                  : gear.isNotEmpty
                      ? gear
                      : '${rate ~/ 1000}kbps';
          if (rate > 0) label += ' · ${rate ~/ 1000}kbps';
          if (fps > 0) label += ' · ${fps}fps';
          entries.add(_Entry(w, h, fps, rate, label, url));
          var size = _asInt(entry['data_size']);
          if (size <= 0) size = _asInt(playAddr['data_size']);
          if (size > 0) sizes[url] = size;
        }
      }
      // 分辨率 → 帧率 → 码率 降序（只按码率排会默认落到 30fps 那条）
      entries.sort((a, b) {
        final bySize = (a.w > a.h ? a.w : a.h).compareTo(b.w > b.h ? b.w : b.h);
        if (bySize != 0) return -bySize;
        final byShort = (a.w < a.h ? a.w : a.h).compareTo(b.w < b.h ? b.w : b.h);
        if (byShort != 0) return -byShort;
        if (a.fps != b.fps) return b.fps.compareTo(a.fps);
        return b.rate.compareTo(a.rate);
      });
      final seen = <String>{};
      for (final e in entries) {
        if (!seen.add(e.url)) continue;
        // playwm → play 即无水印
        qualities.add(MediaQuality(
          label: e.label,
          url: e.url.replaceAll('playwm', 'play'),
          sizeBytes: sizes[e.url] ?? 0,
        ));
      }
      if (qualities.isEmpty) {
        final playAddr = video['play_addr'];
        if (playAddr is Map) {
          final urls = playAddr['url_list'];
          if (urls is List && urls.isNotEmpty) {
            final url = urls.first.toString().replaceAll('playwm', 'play');
            if (url.isNotEmpty) {
              final w = _asInt(playAddr['width']);
              final h = _asInt(playAddr['height']);
              final vw = w > 0 ? w : _asInt(video['width']);
              final vh = h > 0 ? h : _asInt(video['height']);
              qualities.add(MediaQuality(
                label: vw > 0 && vh > 0 ? '${vw}×${vh}' : '最高画质',
                url: url,
                sizeBytes: _asInt(playAddr['data_size']),
              ));
            }
          }
        }
      }
      final originCover = video['origin_cover'];
      final normalCover = video['cover'];
      cover = '';
      if (originCover is Map && originCover['url_list'] is List &&
          (originCover['url_list'] as List).isNotEmpty) {
        cover = (originCover['url_list'] as List).first.toString();
      } else if (normalCover is Map && normalCover['url_list'] is List &&
          (normalCover['url_list'] as List).isNotEmpty) {
        cover = (normalCover['url_list'] as List).first.toString();
      }
    }

    if (qualities.isEmpty && imageUrls.isEmpty) {
      throw ParseException('作品数据中无有效下载地址');
    }

    final desc = (item['desc'] ?? '').toString();
    final author = (item['author'] is Map) ? item['author']['nickname'] : null;
    return MediaInfo(
      platform: SitePlatform.douyin,
      title: desc.isEmpty ? '抖音作品' : desc,
      author: author?.toString() ?? '',
      coverUrl: cover,
      durationMs: _asInt(video['duration']),
      qualities: qualities,
      sourceId: item['aweme_id']?.toString(),
      images: imageUrls,
    );
  }

  /// 把单档（play_addr）扩展成多档。
  ///
  /// 实测结论：分享页的 video.bit_rate 恒为 null，拿不到官方档位表；但播放地址
  /// https://aweme.snssdk.com/aweme/v1/play/?video_id=xxx&ratio=NNN 换 ratio 会返回
  /// 不同清晰度的流（同一作品实测 1080p→1920×1080、720p→1280×720、540p→1024×576，
  /// 360p 会回落到源）。所以这里按候选 ratio 逐个探测大小，**大小相同的视为同一档**
  /// （说明服务端回落），最终按大小降序给出真正存在的档位。
  Future<MediaInfo> _expandQualities(MediaInfo info) async {
    if (info.qualities.isEmpty || info.isImageAlbum) return info;
    final play = info.qualities.first;
    final videoId =
        RegExp(r'video_id=([^&]+)').firstMatch(play.url)?.group(1);
    if (videoId == null) return info;

    // 默认档（分享页给的地址，通常就是原画/最高）
    final baseUrl = _withRatio(play.url, videoId, null);
    const ratios = <String>['1080p', '720p', '540p'];
    final found = <String, int>{baseUrl: 0};

    await Future.wait([
      for (final ratio in ratios)
        () async {
          final url = _withRatio(play.url, videoId, ratio);
          final size = await AppHttp.probeSize(
            url,
            platform: SitePlatform.douyin,
            ua: AppHttp.uaIphone,
          );
          if (size > 0) found[url] = size;
        }(),
      () async {
        final size = await AppHttp.probeSize(
          baseUrl,
          platform: SitePlatform.douyin,
          ua: AppHttp.uaIphone,
        );
        if (size > 0) found[baseUrl] = size;
      }(),
    ]);

    if (found.isEmpty) return info;

    // 按大小降序（大 = 清晰度高），大小重复的只留一个
    final entries = found.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final seenSizes = <int>{};
    final qualities = <MediaQuality>[];
    for (final entry in entries) {
      final size = entry.value;
      // 大小相同视为同一档（服务端回落），保留先出现的那条（字节更大者优先拿到）
      if (!seenSizes.add(size)) continue;
      qualities.add(MediaQuality(
        label: _ratioLabel(entry.key, baseUrl, size),
        url: entry.key,
        sizeBytes: size,
      ));
    }
    // 去重之后按清晰度排（1080p > 720p > 540p），不按字节数 ——
    // 540p 的码率可能比 720p 高，按大小排会让顺序看起来不对
    qualities.sort((a, b) => _ratioRank(b.url, b.label, baseUrl)
        .compareTo(_ratioRank(a.url, a.label, baseUrl)));
    if (qualities.isEmpty) return info;
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

  /// 把播放地址换成指定 ratio（ratio 为 null 时保留原样，仅去掉水印参数）。
  /// 把播放地址换成指定 ratio（ratio 为 null 时保留原样，只去掉水印/logo 参数）。
  String _withRatio(String url, String videoId, String? ratio) {
    final cleaned = url
        .replaceAll('playwm', 'play')
        .replaceAll(RegExp(r'[&?]logo_name=[^&]*'), '');
    if (ratio == null) return cleaned;
    if (RegExp(r'[?&]ratio=').hasMatch(cleaned)) {
      // Dart 的 replaceAll 不支持 $1 分组引用，必须用 replaceAllMapped
      return cleaned.replaceAllMapped(
          RegExp(r'([?&])ratio=[^&]*'), (m) => '${m.group(1)}ratio=$ratio');
    }
    return '$cleaned${cleaned.contains('?') ? '&' : '?'}ratio=$ratio';
  }

  /// 档位名：能对上 ratio 就用它，否则按大小给个大致描述。
  String _ratioLabel(String url, String baseUrl, int size) {
    final m = RegExp(r'[?&]ratio=([^&]+)').firstMatch(url);
    final ratio = m?.group(1) ?? '';
    if (url == baseUrl) {
      return ratio.isEmpty ? '默认' : '$ratio（默认）';
    }
    return ratio.isEmpty ? '${(size / 1024 / 1024).toStringAsFixed(1)}MB' : ratio;
  }

  /// 清晰度权重：用于排序（数字取自 ratio 名）。
  int _ratioRank(String url, String label, String baseUrl) {
    final m = RegExp(r'[?&]ratio=(\d+)').firstMatch(url);
    if (m != null) return int.tryParse(m.group(1)!) ?? 0;
    final d = RegExp(r'(\d+)').firstMatch(label);
    if (d != null) return int.tryParse(d.group(1)!) ?? 0;
    // 默认档（没写 ratio 的原始地址）：按 720 处理，排在 1080p 之后、540p 之前
    return 720;
  }

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }
}

class _Entry {
  _Entry(this.w, this.h, this.fps, this.rate, this.label, this.url);

  final int w;
  final int h;
  final int fps;
  final int rate;
  final String label;
  final String url;
}
