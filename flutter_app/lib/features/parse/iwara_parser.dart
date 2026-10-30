import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/shared/net/browser_http.dart';
import 'package:vdl/shared/site.dart';

/// Iwara（iwara.tv）：纯 HTTP，免登录。
///
/// 链路（与桌面端 `sites.py::parse_iwara` 同一套）：
///   1) `GET api.iwara.tv/video/<id>` → 标题 / 作者 / 原始文件信息 + fileUrl
///   2) 带 `X-Version` 签名请求 fileUrl → 档位表（Source / 540 / 360 / preview）
///   3) 并发 `Range: bytes=0-0` 探针取每档精确字节数（该 CDN 挡 HEAD）
///
/// `preview` 是站点自己的预览片段，直接丢掉；`Source` 就是上传的原始文件。
/// 档位接口每请求一次就重新签发一批「角色名」CDN 域名（bailu / herta / …），
/// 所以这里固定取两轮，多攒一组备用地址 —— 实测个别域名会连不上。
///
/// ⚠️ 这两个域名都在 Cloudflare 后面，`HttpClient` 会被判成非浏览器（403），
/// 必须走 [BrowserHttp]（裸 TLS + Chrome 头序），详见该文件的说明。
class IwaraParser {
  const IwaraParser();

  static const host = 'https://www.iwara.tv';
  static const api = 'https://api.iwara.tv';

  /// X-Version 签名盐（算法来自前端 main.js）。
  ///
  /// 换过一轮：旧的 `5nFp9kmbNnHdAFhaqMvt` 仍能回 200，但只给 360/preview
  /// 两档（静默降级、不报错），带正确签名才会返回含 Source 的完整档位。
  static const signSalt = 'mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN';

  static final _idRe = RegExp(r'iwara\.tv/(?:video|image)/([A-Za-z0-9]+)',
      caseSensitive: false);

  /// `preview` 是站点预览片段，不算可选画质。
  static const _skipFormats = {'preview'};

  /// 档位接口取几轮（每轮一组随机 CDN 域名）。
  static const _formatRounds = 2;

  /// 从链接里取作品 ID（video / image 均可），识别失败返回空串。
  static String videoId(String url) =>
      _idRe.firstMatch(url)?.group(1) ?? '';

  /// `X-Version` 签名：sha1(`<fileId>_<expires>_<salt>`)。
  static String sign(String fileId, String expires) {
    final digest = SHA1Digest()
        .process(Uint8List.fromList(utf8.encode('${fileId}_${expires}_$signSalt')));
    return digest.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
  }

  Future<MediaInfo> parse(String rawUrl) async {
    final vid = videoId(rawUrl);
    if (vid.isEmpty) {
      throw ParseException('无法从链接中识别 Iwara 作品 ID（应形如 iwara.tv/video/xxxx）');
    }

    var detail = await _detail(vid);
    if ((detail['embedUrl'] ?? '').toString().isNotEmpty) {
      throw ParseException('该 Iwara 作品是外链投稿（正文里给了网盘 / 外部链接），无法直接下载');
    }

    var fileObj = _asMap(detail['file']);
    var formats = await _formats(detail);
    if (formats.isEmpty) {
      // fileUrl 的签名有时效：重取一次详情再试
      detail = await _detail(vid);
      fileObj = _asMap(detail['file']);
      formats = await _formats(detail);
    }
    if (formats.isEmpty) {
      throw ParseException('Iwara 没返回可下载档位。可能作品刚上传还在转码，或需要登录才能查看');
    }

    // 画质从高到低：Source 最前，然后按分辨率数字降序
    final names = formats.keys
        .where((name) => !_skipFormats.contains(name.toLowerCase()))
        .toList()
      ..sort((a, b) => rankOf(b) - rankOf(a));
    if (names.isEmpty) {
      throw ParseException('Iwara 只返回了预览片段，没有可下载的完整视频');
    }

    final width = _asInt(fileObj['width']);
    final height = _asInt(fileObj['height']);
    final picked = <MediaQuality>[];
    for (final name in names) {
      final urls = (formats[name] ?? const <String>[])
          .where((url) => url.isNotEmpty)
          .toList();
      if (urls.isEmpty) continue;
      picked.add(MediaQuality(
        label: qualityLabel(name, width, height),
        url: urls.first,
      ));
    }
    if (picked.isEmpty) throw ParseException('Iwara 未解析到可用的下载地址');

    // 档位接口不给字节数：每档发一次 1 字节 Range 探针（拿不到就不显示，不影响下载）
    final sizes = await _probeSizes([for (final q in picked) q.url]);
    final qualities = [
      for (final q in picked)
        MediaQuality(label: q.label, url: q.url, sizeBytes: sizes[q.url] ?? 0),
    ];

    final user = _asMap(detail['user']);
    final title = (detail['title'] ?? '').toString().trim();
    final fid = (fileObj['id'] ?? '').toString();
    final thumb = detail['thumbnail'];
    final cover = fid.isNotEmpty && thumb is int
        ? 'https://files.iwara.tv/image/original/$fid/'
            'thumbnail-${thumb.toString().padLeft(2, '0')}.jpg'
        : '';

    return MediaInfo(
      platform: SitePlatform.iwara,
      title: title.isEmpty ? vid : title,
      author: ((user['name'] ?? user['username']) ?? '').toString().trim(),
      coverUrl: cover,
      durationMs: _asInt(fileObj['duration']) * 1000,
      qualities: qualities,
      sourceId: vid,
      referer: '$host/',
    );
  }

  /// 作品详情（含 fileUrl / file / user / title）。
  Future<Map<String, dynamic>> _detail(String vid) async {
    BrowserResponse res;
    try {
      res = await BrowserHttp.get('$api/video/$vid', referer: '$host/', cors: true);
    } catch (error) {
      throw ParseException(
          'Iwara 接口连接失败（$error）。iwara.tv 需要能出网，请确认代理已开启');
    }
    if (res.statusCode == 404) {
      throw ParseException('Iwara 作品 $vid 不存在或已删除');
    }
    if (!res.ok) {
      throw ParseException('Iwara 详情接口返回 HTTP ${res.statusCode}');
    }
    final json = jsonDecode(res.body);
    if (json is! Map) {
      throw ParseException('Iwara 详情接口返回的不是 JSON（可能被拦截，检查代理）');
    }
    return json.cast<String, dynamic>();
  }

  /// 请求 fileUrl 拿档位表：`{档位名: [主地址, 备用地址, …]}`（保持服务端顺序）。
  Future<Map<String, List<String>>> _formats(Map<String, dynamic> detail) async {
    final fileUrl = (detail['fileUrl'] ?? '').toString().trim();
    if (fileUrl.isEmpty) return {};
    final Uri uri;
    try {
      uri = Uri.parse(fileUrl);
    } catch (_) {
      return {};
    }
    final expires = uri.queryParameters['expires'] ?? '';
    final hash = uri.queryParameters['hash'] ?? '';
    final segments = uri.path.split('/').where((s) => s.isNotEmpty).toList();
    final fid = segments.isEmpty ? '' : segments.last;
    if (expires.isEmpty || fid.isEmpty) return {};

    final base = '${uri.scheme}://${uri.host}${uri.path}'
        '?expires=$expires&hash=$hash';
    // 前端会把 download 参数设成 "Iwara - <标题> [<id>].mp4"（空格用 + 编码）
    final dlName = Uri.encodeQueryComponent(
        'Iwara - ${detail['title'] ?? ''} [${detail['id'] ?? ''}].mp4');
    final extra = <String, String>{'X-Version': sign(fid, expires)};

    final out = <String, List<String>>{};
    for (var round = 0; round < _formatRounds; round++) {
      BrowserResponse res;
      try {
        res = await BrowserHttp.get(
          '$base&download=$dlName',
          referer: '$host/',
          cors: true,
          extra: extra,
        );
      } catch (_) {
        break;
      }
      if (!res.ok) break;
      dynamic json;
      try {
        json = jsonDecode(res.body);
      } catch (_) {
        break;
      }
      if (json is! List || json.isEmpty) break;
      for (final raw in json) {
        if (raw is! Map) continue;
        final name = (raw['name'] ?? '').toString().trim();
        final src = raw['src'];
        final url = src is Map
            ? absUrl((src['download'] ?? src['view'] ?? '').toString())
            : '';
        if (name.isEmpty || url.isEmpty) continue;
        final bucket = out.putIfAbsent(name, () => <String>[]);
        if (!bucket.contains(url)) bucket.add(url);
      }
    }
    return out;
  }

  /// 档位地址常以 `//host/path` 给出，补上 scheme。
  static String absUrl(String value) {
    final url = value.trim();
    if (url.startsWith('//')) return 'https:$url';
    if (url.startsWith('/')) return '$host$url';
    return url;
  }

  /// 档位排序键（越大越好）：Source 最高，然后按名字里的分辨率数字降序。
  ///
  /// 抽成公开方法便于离线测试（不联网也能验排序规则）。
  static int rankOf(String name) {
    final low = name.toLowerCase();
    if (low == 'source') return 1000000;
    final match = RegExp(r'(\d{3,4})').firstMatch(low);
    return match == null ? 0 : (int.tryParse(match.group(1)!) ?? 0);
  }

  /// 档位名 → 画质标签；`Source` 用作品的原始分辨率标注。
  static String qualityLabel(String name, int width, int height) {
    final low = name.toLowerCase();
    if (low == 'source') {
      return width > 0 && height > 0 ? '$width×$height 原始' : '原始画质';
    }
    final match = RegExp(r'(\d{3,4})').firstMatch(low);
    if (match != null) return '${match.group(1)}p';
    return name.isEmpty ? '视频' : name;
  }

  /// 并发探针取每档精确字节数（拿不到就返回 0，不影响下载）。
  ///
  /// 该 CDN 对 HEAD 回 405，只能发 `Range: bytes=0-0` 从 `Content-Range` 读总长。
  Future<Map<String, int>> _probeSizes(List<String> urls) async {
    final out = <String, int>{};
    if (urls.isEmpty) return out;
    await Future.wait([
      for (final url in urls.toSet())
        () async {
          try {
            final res = await BrowserHttp.get(
              url,
              referer: '$host/',
              extra: const {'Range': 'bytes=0-0'},
              timeout: const Duration(seconds: 15),
            );
            final range = res.header('content-range');
            final slash = range.lastIndexOf('/');
            final total = slash >= 0
                ? int.tryParse(range.substring(slash + 1).trim()) ?? 0
                : 0;
            final length = int.tryParse(res.header('content-length')) ?? 0;
            final size = total > 0 ? total : length;
            if (size > 0) out[url] = size;
          } catch (_) {
            // 探针失败只是不显示大小，别影响解析结果
          }
        }(),
    ]);
    return out;
  }

  static Map<String, dynamic> _asMap(Object? value) =>
      value is Map ? value.cast<String, dynamic>() : <String, dynamic>{};

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }
}
