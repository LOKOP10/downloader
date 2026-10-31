import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';
import 'package:vdl/shared/app_log.dart';

/// 禁漫 App 接口客户端（移植自 Python jmcomic 2.7.7 的 JmApiClient 协议）。
class JmClient {
  JmClient._();

  static final JmClient instance = JmClient._();

  static const _secret = '185Hcomic3PAPP7R';
  static const _secretTemplate = '18comicAPPContent';
  static const _appVersion = '2.1.7';

  static const uaApp = 'Mozilla/5.0 (Linux; Android 9; V1938CT Build/PQ3A.190705.11211812; wv) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 '
      'Chrome/91.0.4472.114 Safari/537.36';

  /// 接口镜像。顺序按 2026-10 实测延迟排（快的在前），拿到第一个能用的就记住。
  static const apiDomains = <String>[
    'www.cdngwc.net',
    'www.cdnhjk.net',
    'www.cdngwc.club',
    'www.cdngwc.cc',
  ];

  /// 图片镜像（2026-10 实测全部可连；`.jmapinodeudzn.com` / `.jmtezrspp.com`
  /// / `cdnmhws*.cc` 那批已经 DNS 解析失败，不放进来了）。
  ///
  /// 顺序只是兜底顺序；真正下载前会用 [probeImageDomains] 实测延迟再按快慢排。
  static const imageDomains = <String>[
    'cdn-msp.jmapiproxy1.cc',
    'cdn-msp.jmapiproxy2.cc',
    'cdn-msp2.jmapiproxy2.cc',
    'cdn-msp3.jmapiproxy2.cc',
    'cdn-msp.jmapinodeudzn.net',
    'cdn-msp2.jmapinodeudzn.net',
    'cdn-msp3.jmapinodeudzn.net',
  ];

  static const _allowedSuffixes = <String>[
    'cdnhjk.net',
    'cdngwc.cc',
    'cdngwc.net',
    'cdngwc.club',
    'jmapiproxy1.cc',
    'jmapiproxy2.cc',
    'jmapinodeudzn.net',
  ];

  String _liveDomain = '';

  /// 各图片镜像的实测延迟（毫秒）。只在本进程内有效，App 重启后重新测。
  static final Map<String, int> _imageLatency = <String, int>{};

  /// 是否已经测过速（下载器据此决定要不要先测一测）。
  bool get imageSpeedProbed => _imageLatency.isNotEmpty;

  /// 按实测延迟排好的图片域名（没测到的按原顺序排在最后）。
  static List<String> rankedImageDomains() =>
      rankDomainsByLatency(imageDomains, _imageLatency);

  /// 并发测速：对每个镜像取图片首字节，只测还没测过的。
  ///
  /// 为什么值得花这一两秒：禁漫各镜像延迟能差 2~3 倍，一本百页的图是逐张下的，
  /// 选错镜像等于每页都多等一次 RTT。
  Future<void> probeImageDomains(String chapterId, String filename) async {
    final todo =
        imageDomains.where((d) => !_imageLatency.containsKey(d)).toList();
    if (todo.isEmpty) return;
    await Future.wait(todo.map((domain) async {
      final client = _newClient();
      final sw = Stopwatch()..start();
      try {
        final request = await client
            .getUrl(Uri.parse(imageUrl(chapterId, filename, domain)));
        request.headers.set('user-agent', uaApp);
        request.headers.set('referer', 'https://' + liveDomain + '/');
        request.headers.set('Range', 'bytes=0-0');
        final response =
            await request.close().timeout(const Duration(seconds: 8));
        await response.drain<void>();
        if (response.statusCode == 200 || response.statusCode == 206) {
          _imageLatency[domain] = sw.elapsedMilliseconds;
        }
      } catch (_) {
        // 测不通就不记 —— 排序时自然排到后面
      } finally {
        client.close(force: true);
      }
    }));
  }

  String get liveDomain => _liveDomain.isEmpty ? apiDomains.first : _liveDomain;

  bool isJmHost(String host) {
    final lower = host.toLowerCase();
    return _allowedSuffixes.any((suffix) => lower.endsWith(suffix));
  }

  static String _md5Hex(String text) {
    final digest = MD5Digest().process(Uint8List.fromList(utf8.encode(text)));
    final buffer = StringBuffer();
    for (final byte in digest) {
      buffer.write(byte.toRadixString(16).padLeft(2, '0'));
    }
    return buffer.toString();
  }

  static String decryptBody(String base64Text, int timestamp, String secret) {
    final raw = base64Decode(base64Text);
    final key = Uint8List.fromList(utf8.encode(_md5Hex(timestamp.toString() + secret)));
    final cipher = ECBBlockCipher(AESEngine())..init(false, KeyParameter(key));
    final out = Uint8List(raw.length);
    for (var offset = 0; offset + 16 <= raw.length; offset += 16) {
      cipher.processBlock(raw, offset, out, offset);
    }
    var length = out.length;
    if (length > 0) {
      final pad = out[length - 1];
      if (pad >= 1 && pad <= 16 && pad <= length) length -= pad;
    }
    return utf8.decode(out.sublist(0, length), allowMalformed: true);
  }

  HttpClient _newClient() {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 15);
    client.badCertificateCallback = (cert, host, port) => isJmHost(host);
    return client;
  }

  Map<String, String> _signedHeaders(String secret) {
    final ts = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    return {
      'user-agent': uaApp,
      'token': _md5Hex(ts.toString() + secret),
      'tokenparam': ts.toString() + ',' + _appVersion,
    };
  }

  Future<Map<String, dynamic>> apiGet(
    String path, {
    Map<String, String> params = const {},
    String secret = _secret,
    bool decrypt = true,
  }) async {
    final ts = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final headers = _signedHeaders(secret);
    final order = <String>[
      if (_liveDomain.isNotEmpty) _liveDomain,
    ];
    for (final domain in apiDomains) {
      if (domain != _liveDomain) order.add(domain);
    }
    Object? lastError;
    for (final domain in order) {
      final query = params.entries
          .map((e) => e.key + '=' + Uri.encodeQueryComponent(e.value))
          .join('&');
      final url = 'https://' + domain + path + (query.isEmpty ? '' : '?' + query);
      final client = _newClient();
      try {
        final request = await client.getUrl(Uri.parse(url));
        headers.forEach(request.headers.set);
        final response = await request.close().timeout(const Duration(seconds: 20));
        final body = await response.transform(utf8.decoder).join();
        if (response.statusCode != 200) {
          lastError = 'HTTP ' + response.statusCode.toString();
          continue;
        }
        if (!decrypt) {
          _liveDomain = domain;
          return {'raw': body};
        }
        final json = jsonDecode(body);
        if (json is! Map) {
          lastError = '返回的不是 JSON';
          continue;
        }
        final map = json.cast<String, dynamic>();
        if (_asInt(map['code']) != 200) {
          lastError = '接口返回 code=' + map['code'].toString();
          continue;
        }
        final data = (map['data'] ?? '').toString();
        if (data.isEmpty) {
          lastError = '接口无 data（本子可能不存在）';
          continue;
        }
        final plain = decryptBody(data, ts, secret);
        final decoded = jsonDecode(plain);
        if (decoded is! Map) {
          lastError = '解密后不是 JSON';
          continue;
        }
        _liveDomain = domain;
        return decoded.cast<String, dynamic>();
      } catch (error) {
        lastError = error.toString();
      } finally {
        client.close(force: true);
      }
    }
    throw JmException('禁漫接口不可用（' + lastError.toString() + '）');
  }

  Future<JmAlbum> album(String albumId) async {
    final data = await apiGet('/album', params: {'id': albumId});
    final chapters = <JmChapter>[];
    final series = data['series'];
    if (series is List) {
      for (final raw in series) {
        if (raw is! Map) continue;
        final id = (raw['id'] ?? '').toString();
        if (id.isEmpty) continue;
        chapters.add(JmChapter(
          id: id,
          name: (raw['name'] ?? '').toString(),
          sort: _asInt(raw['sort']),
        ));
      }
    }
    if (chapters.isEmpty) {
      chapters.add(JmChapter(id: albumId, name: '', sort: 1));
    }
    chapters.sort((a, b) => a.sort.compareTo(b.sort));
    return JmAlbum(
      id: albumId,
      name: (data['name'] ?? '').toString(),
      author: (data['author'] ?? '').toString(),
      totalPhotos: _asInt(data['total_photos']),
      coverUrl: coverUrl(albumId),
      chapters: chapters,
    );
  }

  Future<List<String>> chapterImages(String chapterId) async {
    final data = await apiGet('/chapter', params: {'id': chapterId});
    final images = data['images'];
    if (images is! List) return const [];
    return [
      for (final raw in images)
        if (raw.toString().isNotEmpty) raw.toString(),
    ];
  }

  String coverUrl(String albumId) =>
      'https://' + liveDomain + '/media/albums/' + albumId + '.jpg';

  String imageUrl(String chapterId, String filename, String domain) =>
      'https://' + domain + '/media/photos/' + chapterId + '/' + filename;

  /// 取封面图：禁漫封面在接口域名下，证书是自签名的，
  /// Flutter 的 Image.network 会直接失败，所以用同一个放行过的客户端下载。
  Future<Uint8List?> fetchCover(String albumId) async {
    // 封面走接口域名（4 个镜像都提供），逐个试；失败原因写进应用日志，便于排查
    final order = <String>[
      liveDomain,
      ...apiDomains.where((d) => d != liveDomain),
    ];
    for (final domain in order) {
      final client = _newClient();
      try {
        final request = await client.getUrl(
            Uri.parse('https://' + domain + '/media/albums/' + albumId + '.jpg'));
        request.headers.set('user-agent', uaApp);
        request.headers.set('referer', 'https://' + domain + '/');
        final response =
            await request.close().timeout(const Duration(seconds: 12));
        if (response.statusCode != 200) {
          AppLog.instance.add('jm-cover',
              '封面 ' + domain + ' 返回 HTTP ' + response.statusCode.toString());
          continue;
        }
        final builder = BytesBuilder(copy: false);
        await for (final chunk in response) {
          builder.add(chunk);
        }
        final bytes = builder.takeBytes();
        if (bytes.isEmpty) {
          AppLog.instance.add('jm-cover', '封面 ' + domain + ' 返回空内容');
          continue;
        }
        AppLog.instance.add('jm-cover', '封面 ' + domain + ' 成功，' + bytes.length.toString() + ' 字节');
        return bytes;
      } catch (error) {
        AppLog.instance.add('jm-cover', '封面 ' + domain + ' 失败：' + error.toString());
      } finally {
        client.close(force: true);
      }
    }
    return null;
  }

  static int _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value) ?? 0;
    return 0;
  }

  Future<String> scrambleId(String chapterId) async {
    try {
      final data = await apiGet(
        '/chapter_view_template',
        params: {
          'id': chapterId,
          'mode': 'vertical',
          'page': '0',
          'app_img_shunt': '1',
          'express': 'off',
          'v': (DateTime.now().millisecondsSinceEpoch ~/ 1000).toString(),
        },
        secret: _secretTemplate,
        decrypt: false,
      );
      final html = (data['raw'] ?? '').toString();
      final match = RegExp(r'var scramble_id\s*=\s*(\d+);').firstMatch(html);
      if (match != null) return match.group(1)!;
    } catch (_) {
      // 拿不到就用默认值：老图本来不需要重排
    }
    return '220980';
  }

  Future<Uint8List> fetchImage(
    String chapterId,
    String filename, {
    int startIndex = 0,
  }) async {
    final ranked = rankedImageDomains();
    // 测过速就主要用最快的两个（既快又少画质抖动），失败再按顺序降级
    final pool = _imageLatency.isNotEmpty && ranked.length >= 2
        ? ranked.take(2).toList()
        : ranked;
    Object? lastError;
    for (var i = 0; i < ranked.length; i++) {
      final domain = i < pool.length
          ? pool[(startIndex + i) % pool.length]
          : ranked[i];
      final client = _newClient();
      try {
        final request =
            await client.getUrl(Uri.parse(imageUrl(chapterId, filename, domain)));
        request.headers.set('user-agent', uaApp);
        request.headers.set('referer', 'https://' + liveDomain + '/');
        final response = await request.close().timeout(const Duration(seconds: 30));
        if (response.statusCode != 200) {
          lastError = 'HTTP ' + response.statusCode.toString();
          continue;
        }
        final builder = BytesBuilder(copy: false);
        await for (final chunk in response) {
          builder.add(chunk);
        }
        final bytes = builder.takeBytes();
        if (bytes.isEmpty) {
          lastError = '空响应';
          continue;
        }
        return bytes;
      } catch (error) {
        lastError = error.toString();
      } finally {
        client.close(force: true);
      }
    }
    throw JmException('图片下载失败（' + lastError.toString() + '）');
  }
}

class JmException implements Exception {
  JmException(this.message);

  final String message;

  @override
  String toString() => message;
}

class JmChapter {
  const JmChapter({required this.id, required this.name, required this.sort});

  final String id;
  final String name;
  final int sort;

  String get label {
    if (name.isNotEmpty) return name;
    return '第 ' + sort.toString() + ' 章';
  }
}

class JmAlbum {
  const JmAlbum({
    required this.id,
    required this.name,
    required this.author,
    required this.totalPhotos,
    required this.coverUrl,
    required this.chapters,
  });

  final String id;
  final String name;
  final String author;
  final int totalPhotos;
  final String coverUrl;
  final List<JmChapter> chapters;

  bool get multiChapter => chapters.length > 1;

  String get pageText => multiChapter
      ? chapters.length.toString() + ' 章 · 共 ' + totalPhotos.toString() + ' 页'
      : totalPhotos.toString() + ' 页';
}

/// 按实测延迟排序：测到的按毫秒升序在前，没测到的保持原顺序排在最后。
///
/// 抽成顶层纯函数是为了能离线单测（真实测速要联网）。
List<String> rankDomainsByLatency(List<String> domains, Map<String, int> latency) {
  final measured = <String>[];
  final unknown = <String>[];
  for (final domain in domains) {
    if (latency.containsKey(domain)) {
      measured.add(domain);
    } else {
      unknown.add(domain);
    }
  }
  measured.sort((a, b) => latency[a]!.compareTo(latency[b]!));
  return [...measured, ...unknown];
}
