import 'dart:convert';

import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

import 'media_info.dart';

/// B 站解析：**纯 HTTP**，view 接口拿标题/分P，playurl 接口拿播放地址。
///
/// 关键点（与旧 Kotlin 版 BiliParser、桌面端 parse_bilibili 的结论一致）：
///   1. 短链 b23.tv 要先跟随跳转，才能从落地页抠出 BV 号；
///   2. 链接里的 ?p=N 是分P 序号，忽略它会把第 3 P 静默下成第 1 P；
///   3. 两条取址路线一起问、一起列：
///      - DASH（fnval=4048）：1080P / 1080P+ / 4K 只在这里，音视频分离，
///        下载后要用原生 MediaMuxer 合并（见 MediaQuality.audioUrl）；
///      - durl（platform=html5 + high_quality=1）：音视频已合并的 mp4，
///        最高 720P，不用合并，作为兜底与「小文件」选项；
///   4. 未登录时服务端会把 qn 降级，返回的 data.quality 才是真实档位，
///      所以按「真实 quality」去重，而不是按请求的 qn；
///   5. 同一清晰度会有 avc1 / hev1 / av01 三种编码，优先 H.264（avc1）：
///      MediaMuxer 合并最稳，老机器也能播；
///   6. 长视频 durl 会被拆成多段，当前只取第一段（与桌面端一致）。
class BiliParser {
  const BiliParser();

  static final _bvRe = RegExp(r'(BV[0-9A-Za-z]{10})');
  static final _avRe = RegExp(r'/av(\d+)', caseSensitive: false);
  static final _pageRe = RegExp(r'[?&]p=(\d+)');

  static const _api = 'https://api.bilibili.com';
  static const _timeout = Duration(seconds: 12);

  /// 清晰度 id → 人话（B 站官方命名）。
  static const _qualityNames = <int, String>{
    127: '8K 超高清',
    126: '杜比视界',
    125: 'HDR 真彩',
    120: '4K 超清',
    116: '1080P60 高帧率',
    112: '1080P+ 高码率',
    80: '1080P 高清',
    74: '720P60 高帧率',
    64: '720P 高清',
    32: '480P 清晰',
    16: '360P 流畅',
  };

  Future<MediaInfo> parse(String rawUrl) async {
    var url = rawUrl.trim();
    if (url.isEmpty) throw ParseException('先粘贴 B 站视频链接');

    if (_hostOf(url).endsWith('b23.tv')) {
      url = await _expand(url);
    }

    final bv = _bvRe.firstMatch(url)?.group(1);
    final aid = _avRe.firstMatch(url)?.group(1);
    if (bv == null && (aid == null || aid.isEmpty)) {
      throw ParseException('未识别到 BV / av 号，请用 B 站视频详情页链接');
    }
    final query = bv != null ? 'bvid=' + bv : 'aid=' + aid!;

    final view = await _getJson('/x/web-interface/view?' + query);
    if (_asInt(view['code']) != 0) {
      throw ParseException('取视频信息失败：' + _message(view));
    }
    final data = view['data'];
    if (data is! Map) throw ParseException('视频信息为空');
    final viewData = data.cast<String, dynamic>();

    final pageIndex = _pageOf(url);
    final picked = _pickPage(viewData, pageIndex);
    if (picked.cid <= 0) throw ParseException('没拿到视频 cid（可能是分P 链接失效）');

    // 两条路线并行问：durl 兜底 + DASH 抢高清
    final results = await Future.wait([
      _fetchPlayUrls(picked.bvid, picked.cid),
      _dash(picked.bvid, picked.cid),
    ]);
    final playData = results[0] as List<Map<String, dynamic>>;
    final dashData = results[1] as Map<String, dynamic>?;

    return buildInfo(
      viewData: viewData,
      playData: playData,
      dashData: dashData,
      pageIndex: pageIndex,
    );
  }

  /// 用 view + playurl 的响应拼出 MediaInfo。抽成公开方法便于离线测试。
  MediaInfo buildInfo({
    required Map<String, dynamic> viewData,
    required List<Map<String, dynamic>> playData,
    Map<String, dynamic>? dashData,
    int pageIndex = 1,
  }) {
    final picked = _pickPage(viewData, pageIndex);
    final qualities = buildAllQualities(
      playData: playData,
      dashData: dashData,
      durationSec: picked.durationSec,
    );
    if (qualities.isEmpty) {
      throw ParseException('没有取到可下载的音视频文件'
          '（该视频可能需要登录，或是番剧/付费内容，仅 App 内可看）');
    }
    final pic = (viewData['pic'] ?? '').toString();
    return MediaInfo(
      platform: SitePlatform.bili,
      title: picked.title,
      author: _ownerName(viewData),
      coverUrl: pic.startsWith('http://')
          ? 'https://' + pic.substring('http://'.length)
          : pic,
      durationMs: picked.durationSec * 1000,
      qualities: qualities,
      sourceId: picked.bvid,
    );
  }

  /// 全部候选档位：同一清晰度优先用 durl（单文件、不用合并），
  /// 其余按清晰度 id 降序 —— 1080P / 4K 只存在于 DASH。
  List<MediaQuality> buildAllQualities({
    required List<Map<String, dynamic>> playData,
    Map<String, dynamic>? dashData,
    int durationSec = 0,
  }) {
    final byId = <int, MediaQuality>{};
    final durl = _durlEntries(playData);
    final dash = dashData == null
        ? <int, MediaQuality>{}
        : _dashEntries(dashData, durationSec);
    for (final entry in durl.entries) {
      byId[entry.key] = entry.value;
    }
    for (final entry in dash.entries) {
      byId.putIfAbsent(entry.key, () => entry.value);
    }
    final ids = byId.keys.toList()..sort((a, b) => b.compareTo(a));
    return [for (final id in ids) byId[id]!];
  }

  /// 只要 durl 档位（音视频已合并的 mp4）。
  List<MediaQuality> buildQualities(List<Map<String, dynamic>> playData) {
    final entries = _durlEntries(playData);
    final ids = entries.keys.toList()
      ..sort((a, b) => entries[b]!.sizeBytes.compareTo(entries[a]!.sizeBytes));
    return [for (final id in ids) entries[id]!];
  }

  /// DASH 档位（音视频分离）：每个视频流配最好的一条音频，下载后需合并。
  List<MediaQuality> buildDashQualities(
    Map<String, dynamic> dashData,
    int durationSec,
  ) {
    final entries = _dashEntries(dashData, durationSec);
    final ids = entries.keys.toList()..sort((a, b) => b.compareTo(a));
    return [for (final id in ids) entries[id]!];
  }

  Map<int, MediaQuality> _durlEntries(List<Map<String, dynamic>> playData) {
    final byQuality = <int, MediaQuality>{};
    for (final data in playData) {
      final quality = _asInt(data['quality']);
      final durl = data['durl'];
      if (durl is! List || durl.isEmpty) continue;
      final first = durl.first;
      if (first is! Map) continue;
      final url = (first['url'] ?? '').toString();
      if (url.isEmpty) continue;
      if (byQuality.containsKey(quality)) continue;
      final size = _asInt(first['size']);
      final segments = durl.length;
      var label = _qualityName(quality);
      if (size > 0) {
        label = label + ' · ' + (size / 1024 / 1024).toStringAsFixed(1) + 'MB';
      }
      if (segments > 1) {
        label = label + ' · 仅第 1/' + segments.toString() + ' 段';
      }
      byQuality[quality] = MediaQuality(label: label, url: url, sizeBytes: size);
    }
    return byQuality;
  }

  Map<int, MediaQuality> _dashEntries(
    Map<String, dynamic> dashData,
    int durationSec,
  ) {
    final dash = dashData['dash'];
    if (dash is! Map) return const {};

    // 音频只挑一路：码率最高的那条（DASH 的音频与视频分开，且与清晰度无关）
    var audioUrl = '';
    var audioBw = 0;
    final audios = dash['audio'];
    if (audios is List) {
      for (final raw in audios) {
        if (raw is! Map) continue;
        final bandwidth = _asInt(raw['bandwidth']);
        if (bandwidth <= audioBw) continue;
        final urls = _streamUrls(raw);
        if (urls.isEmpty) continue;
        audioBw = bandwidth;
        audioUrl = urls.first;
      }
    }
    if (audioUrl.isEmpty) return const {};

    final videos = dash['video'];
    if (videos is! List) return const {};

    // 同一 id 有多种编码：优先 avc1(H.264)，其次 hev1/hvc1，再其次 av01
    final best = <int, Map<String, dynamic>>{};
    for (final raw in videos) {
      if (raw is! Map) continue;
      final stream = raw.cast<String, dynamic>();
      final id = _asInt(stream['id']);
      final current = best[id];
      if (current == null || _preferStream(stream, current)) best[id] = stream;
    }

    final out = <int, MediaQuality>{};
    for (final entry in best.entries) {
      final stream = entry.value;
      final urls = _streamUrls(stream);
      if (urls.isEmpty) continue;
      final bandwidth = _asInt(stream['bandwidth']);
      final width = _asInt(stream['width']);
      final height = _asInt(stream['height']);
      final fps = _fpsOf(stream);
      var label = _qualityName(entry.key);
      if (width > 0 && height > 0) {
        label = label + ' · ' + width.toString() + '×' + height.toString();
      }
      if (bandwidth > 0) {
        label = label + ' · ' + (bandwidth / 1000 / 1000).toStringAsFixed(1) + 'Mbps';
      }
      if (fps > 0) label = label + ' · ' + fps.toString() + 'fps';
      // DASH 没有字节数：用 (视频+音频) 码率 × 时长估算
      final size = durationSec > 0
          ? ((bandwidth + audioBw) * durationSec / 8).round()
          : 0;
      out[entry.key] = MediaQuality(
        label: label,
        url: urls.first,
        sizeBytes: size,
        audioUrl: audioUrl,
      );
    }
    return out;
  }

  /// 同一清晰度里挑更合适的那路：先比编码兼容性，再比码率。
  static bool _preferStream(
    Map<String, dynamic> candidate,
    Map<String, dynamic> current,
  ) {
    final rank = _codecRank(candidate['codecs']);
    final other = _codecRank(current['codecs']);
    if (rank != other) return rank > other;
    return _asInt(candidate['bandwidth']) > _asInt(current['bandwidth']);
  }

  /// avc1(H.264) 最稳；HEVC 次之；AV1 放最后。
  static int _codecRank(Object? codecs) {
    final text = (codecs ?? '').toString().toLowerCase();
    if (text.startsWith('avc1')) return 3;
    if (text.startsWith('hev1') || text.startsWith('hvc1')) return 2;
    return 1;
  }

  /// frameRate 既可能是 "60"，也可能是 "16000/672" 这种分数。
  static int _fpsOf(Map<String, dynamic> stream) {
    for (final key in const ['frameRate', 'frame_rate']) {
      final raw = (stream[key] ?? '').toString().trim();
      if (raw.isEmpty) continue;
      double value;
      if (raw.contains('/')) {
        final parts = raw.split('/');
        if (parts.length != 2) continue;
        final a = double.tryParse(parts[0]);
        final b = double.tryParse(parts[1]);
        if (a == null || b == null || b == 0) continue;
        value = a / b;
      } else {
        final parsed = double.tryParse(raw);
        if (parsed == null) continue;
        value = parsed;
      }
      if (value > 0) return value.round();
    }
    return 0;
  }

  /// 一路流可能给 baseUrl 或 base_url，备用地址同样两种写法。
  static List<String> _streamUrls(Map<dynamic, dynamic> stream) {
    final out = <String>[];
    for (final key in const ['baseUrl', 'base_url', 'backupUrl', 'backup_url']) {
      final value = stream[key];
      if (value is String && value.isNotEmpty && !out.contains(value)) {
        out.add(value);
      } else if (value is List) {
        for (final item in value) {
          final text = item.toString();
          if (text.isNotEmpty && !out.contains(text)) out.add(text);
        }
      }
    }
    return out;
  }

  /// 同一视频按几个 qn 各问一次，得到 durl 的真实档位。
  Future<List<Map<String, dynamic>>> _fetchPlayUrls(
    String bvid,
    int cid,
  ) async {
    const qns = [127, 80, 32];
    final results = await Future.wait([
      for (final qn in qns) _playurl(bvid, cid, qn),
    ]);
    return results.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>?> _playurl(String bvid, int cid, int qn) async {
    final path = '/x/player/playurl?bvid=' + bvid +
        '&cid=' + cid.toString() +
        '&fnval=0&fnver=0&fourk=1&qn=' + qn.toString() +
        '&platform=html5&high_quality=1';
    try {
      final json = await _getJson(path);
      if (_asInt(json['code']) != 0) return null;
      final data = json['data'];
      return data is Map ? data.cast<String, dynamic>() : null;
    } catch (_) {
      return null;
    }
  }

  /// DASH（音视频分离）：fnval=4048 才会下发 dash 结构。
  Future<Map<String, dynamic>?> _dash(String bvid, int cid) async {
    final path = '/x/player/playurl?bvid=' + bvid +
        '&cid=' + cid.toString() +
        '&fnval=4048&fnver=0&fourk=1&qn=127';
    try {
      final json = await _getJson(path);
      if (_asInt(json['code']) != 0) return null;
      final data = json['data'];
      return data is Map ? data.cast<String, dynamic>() : null;
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>> _getJson(String path) async {
    final body = await AppHttp.getText(
      _api + path,
      platform: SitePlatform.bili,
      ua: AppHttp.uaDesktop,
      timeout: _timeout,
      extra: const {'Origin': 'https://www.bilibili.com'},
    );
    final json = jsonDecode(body);
    if (json is! Map) throw ParseException('B 站接口返回格式异常');
    return json.cast<String, dynamic>();
  }

  /// 链接里的分P 序号：?p=3，缺省或非法值都当第 1 P。
  static int _pageOf(String url) {
    final raw = _pageRe.firstMatch(url)?.group(1);
    final value = int.tryParse(raw ?? '') ?? 1;
    return value < 1 ? 1 : value;
  }

  _Picked _pickPage(Map<String, dynamic> data, int pageIndex) {
    final pages = data['pages'];
    Map<String, dynamic>? page;
    if (pages is List && pages.isNotEmpty) {
      final index = (pageIndex - 1).clamp(0, pages.length - 1);
      final entry = pages[index];
      if (entry is Map) page = entry.cast<String, dynamic>();
    }
    final bvid = (data['bvid'] ?? '').toString();
    final cid = _asInt(page?['cid']) > 0
        ? _asInt(page?['cid'])
        : _asInt(data['cid']);
    final base = (data['title'] ?? '').toString();
    var title = base.isEmpty ? 'B站视频' : base;
    // 多 P 视频必须带 P 名，否则几个 P 会下成同一个文件名
    if (pages is List && pages.length > 1) {
      final part = (page?['part'] ?? '').toString();
      title = title + ' P' + pageIndex.toString() + (part.isEmpty ? '' : ' ' + part);
    }
    final duration = _asInt(page?['duration']) > 0
        ? _asInt(page?['duration'])
        : _asInt(data['duration']);
    return _Picked(bvid: bvid, cid: cid, title: title, durationSec: duration);
  }

  static String _ownerName(Map<String, dynamic> data) {
    final owner = data['owner'];
    return owner is Map ? (owner['name'] ?? '').toString() : '';
  }

  static String _qualityName(int quality) =>
      _qualityNames[quality] ?? ('清晰度 ' + quality.toString());

  static String _message(Map<String, dynamic> json) {
    final message = (json['message'] ?? '').toString();
    return message.isEmpty ? '未知错误' : message;
  }

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
        platform: SitePlatform.bili,
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

class _Picked {
  const _Picked({
    required this.bvid,
    required this.cid,
    required this.title,
    required this.durationSec,
  });

  final String bvid;
  final int cid;
  final String title;
  final int durationSec;
}
