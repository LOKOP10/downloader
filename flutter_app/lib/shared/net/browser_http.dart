import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// 一次「浏览器化」HTTPS 请求的结果（文本形态）。
class BrowserResponse {
  BrowserResponse({
    required this.url,
    required this.statusCode,
    required this.headers,
    required List<int> bytes,
  }) : body = utf8.decode(bytes, allowMalformed: true);

  final String url;
  final int statusCode;

  /// 键统一转小写，取值直接 `header('content-type')`。
  final Map<String, String> headers;
  final String body;

  bool get ok => statusCode >= 200 && statusCode < 300;

  String header(String name) => headers[name.toLowerCase()] ?? '';

  @override
  String toString() => 'HTTP $statusCode $url（${body.length} 字符）';
}

/// 一次「浏览器化」HTTPS 请求的结果（二进制形态）。
class BrowserBytes {
  BrowserBytes({
    required this.url,
    required this.statusCode,
    required this.headers,
    required this.bytes,
  });

  final String url;
  final int statusCode;

  /// 键统一转小写，取值直接 `header('content-type')`。
  final Map<String, String> headers;
  final List<int> bytes;

  bool get ok => statusCode >= 200 && statusCode < 300;

  String header(String name) => headers[name.toLowerCase()] ?? '';

  @override
  String toString() => 'HTTP $statusCode $url（${bytes.length} 字节）';
}

/// Cloudflare 站点专用的「浏览器化」HTTPS 请求。
///
/// 为什么不能直接用 `dart:io` 的 `HttpClient`：
/// iwara.tv / api.iwara.tv / hanime1.me，以及 **Pornhub 的 HLS CDN
/// （hv-h.phncdn.com）** 都在 Cloudflare 后面，并开了「浏览器完整性检查」。
/// **同样一组请求头**，用 `HttpClient` 发出去一律回 403（页面）或 410（m3u8，
/// 实测），换成裸 TLS（`SecureSocket`）按 Chrome 的顺序手写请求行，同一个 URL、
/// 同一台机器、同一个代理就回 200（2026-10 实测）。差别在于 `HttpClient` 会自己
/// 补 `accept-encoding`、归一化头部名字与顺序，CF 拿这个组合判定成非浏览器。
///
/// 所以这里绕开 `HttpClient`：TCP（必要时先走 HTTP CONNECT 隧道进本地代理）
/// → `SecureSocket` → 按 Chrome 的头顺序手写请求 → 自己解析响应（含 chunked
/// 与 gzip/deflate）。**只有被 Cloudflare 拦的站点才用它**，普通站点继续走
/// `AppHttp`（那边有 cookie 罐、多连接分段下载等现成能力）。
///
/// 边界：
///  · 只支持 GET，只支持 https；
///  · 不支持 br（Dart 没有 brotli 解码器），所以请求头里只要 gzip, deflate；
///  · 不跟随跳转（三个站点都不需要，调用方按需自己处理 30x）；
///  · 每次请求都新建 TCP + TLS（约 100~200ms），分片多得靠并发摊平。
abstract final class BrowserHttp {
  /// 与 `AppHttp.uaDesktop` 一致：CF 认这一套 UA 与头组合，换 UA 会重新被拦。
  static const ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

  /// 进程级代理（形如 `127.0.0.1:7897`），只给桌面实测脚本用。
  ///
  /// 安卓上不设：项目其余链路都不读系统代理，流量由系统 VPN / 全局代理接管，
  /// 这里保持一致，避免给真机凭空加一条「代理没开就全挂」的隐式依赖。
  static String? proxy;

  /// GET 一个页面 / 接口，按 UTF-8 解成字符串。
  ///
  /// [referer] 会写进 `Referer`；[extra] 追加自定义头（如 iwara 的 `X-Version`）；
  /// [cors] 为真时按 XHR 的 Sec-Fetch-* 组合发，拿 JSON 接口更自然。
  static Future<BrowserResponse> get(
    String url, {
    String? referer,
    Map<String, String>? extra,
    bool cors = false,
    Duration timeout = const Duration(seconds: 30),
  }) async {
    final raw = await rawGet(
      url,
      referer: referer,
      extra: extra,
      cors: cors,
      timeout: timeout,
    );
    return BrowserResponse(
      url: raw.url,
      statusCode: raw.statusCode,
      headers: raw.headers,
      bytes: raw.bytes,
    );
  }

  /// 取二进制（视频分片这类不能按文本解的东西）。
  static Future<BrowserBytes> getBytes(
    String url, {
    String? referer,
    Map<String, String>? extra,
    Duration timeout = const Duration(seconds: 60),
  }) =>
      rawGet(url,
          referer: referer,
          extra: extra,
          cors: true,
          timeout: timeout);

  /// 真正干活的那一层：建连 → 发请求 → 收响应（二进制）。
  static Future<BrowserBytes> rawGet(
    String url, {
    String? referer,
    Map<String, String>? extra,
    bool cors = false,
    Duration timeout = const Duration(seconds: 30),
  }) async {
    final uri = Uri.parse(url);
    if (uri.scheme != 'https') {
      throw ArgumentError('BrowserHttp 只支持 https：$url');
    }
    final sw = Stopwatch()..start();
    Duration left() {
      final rest = timeout - sw.elapsed;
      return rest > Duration.zero ? rest : const Duration(milliseconds: 1);
    }

    final port = uri.hasPort ? uri.port : 443;
    Socket socket;
    final proxyHost = proxy;
    if (proxyHost != null && proxyHost.isNotEmpty) {
      final p = Uri.parse(
          proxyHost.contains('://') ? proxyHost : 'http://$proxyHost');
      socket = await Socket.connect(p.host, p.port, timeout: left());
      // 先跟代理握手指令，隧道建立后再在它上面做 TLS
      socket.write('CONNECT ${uri.host}:$port HTTP/1.1\r\n'
          'Host: ${uri.host}:$port\r\n'
          '\r\n');
      final tunnel = _StreamReader(socket);
      final tunnelHead = await tunnel.readHead(left());
      if (tunnelHead == null ||
          !(tunnelHead.startsWith('HTTP/1.1 2') ||
              tunnelHead.startsWith('HTTP/1.0 2'))) {
        socket.destroy();
        throw HttpException('代理隧道建立失败（${tunnelHead ?? '无响应'}）',
            uri: uri);
      }
    } else {
      socket = await Socket.connect(uri.host, port, timeout: left());
    }

    SecureSocket tls;
    try {
      tls = await SecureSocket.secure(
        socket,
        host: uri.host,
        onBadCertificate: (_) => false,
      ).timeout(left());
    } catch (error) {
      socket.destroy();
      throw HttpException('TLS 握手失败：$error', uri: uri);
    }

    try {
      tls.write(_requestText(uri, referer: referer, extra: extra, cors: cors));
      final reader = _StreamReader(tls);
      final head = await reader.readHead(left());
      if (head == null) {
        throw HttpException('连接被对端关闭（没收到响应头）', uri: uri);
      }
      final status = _parseStatus(head);
      final headers = _parseHeaders(head);
      final raw = await reader.readBody(headers, left());
      return BrowserBytes(
        url: url,
        statusCode: status,
        headers: headers,
        bytes: _decode(raw, headers['content-encoding'] ?? ''),
      );
    } finally {
      tls.destroy();
    }
  }

  /// 按 Chrome 的头部顺序拼请求。**顺序本身也是被判定的特征之一**，别随手排序。
  static String _requestText(
    Uri uri, {
    String? referer,
    Map<String, String>? extra,
    required bool cors,
  }) {
    final path = uri.path.isEmpty ? '/' : uri.path;
    final target = uri.hasQuery ? '$path?${uri.query}' : path;
    final accept = cors
        ? 'application/json, text/plain, */*'
        : 'text/html,application/xhtml+xml,application/xml;q=0.9,'
            'image/avif,image/webp,*/*;q=0.8';
    final sb = StringBuffer()
      ..write('GET $target HTTP/1.1\r\n')
      ..write('Host: ${uri.host}\r\n')
      ..write('Connection: keep-alive\r\n')
      ..write('sec-ch-ua: "Chromium";v="124", "Google Chrome";v="124", '
          '"Not-A.Brand";v="99"\r\n')
      ..write('sec-ch-ua-mobile: ?0\r\n')
      ..write('sec-ch-ua-platform: "Windows"\r\n');
    if (!cors) sb.write('Upgrade-Insecure-Requests: 1\r\n');
    sb
      ..write('User-Agent: $ua\r\n')
      ..write('Accept: $accept\r\n');
    if (cors) {
      sb
        ..write('Sec-Fetch-Site: same-site\r\n')
        ..write('Sec-Fetch-Mode: cors\r\n')
        ..write('Sec-Fetch-Dest: empty\r\n');
    } else {
      sb
        ..write('Sec-Fetch-Site: none\r\n')
        ..write('Sec-Fetch-Mode: navigate\r\n')
        ..write('Sec-Fetch-User: ?1\r\n')
        ..write('Sec-Fetch-Dest: document\r\n');
    }
    if (referer != null && referer.isNotEmpty) {
      sb.write('Referer: $referer\r\n');
    }
    if (extra != null) {
      for (final entry in extra.entries) {
        sb.write('${entry.key}: ${entry.value}\r\n');
      }
    }
    sb
      ..write('Accept-Encoding: gzip, deflate\r\n')
      ..write('Accept-Language: zh-CN,zh;q=0.9,en;q=0.8\r\n')
      ..write('\r\n');
    return sb.toString();
  }

  static int _parseStatus(String head) {
    final line = head.split('\r\n').first;
    final parts = line.split(' ');
    if (parts.length < 2) return 0;
    return int.tryParse(parts[1]) ?? 0;
  }

  static Map<String, String> _parseHeaders(String head) {
    final out = <String, String>{};
    final lines = head.split('\r\n');
    for (var i = 1; i < lines.length; i++) {
      final line = lines[i];
      if (line.isEmpty) break;
      final colon = line.indexOf(':');
      if (colon <= 0) continue;
      final name = line.substring(0, colon).trim().toLowerCase();
      final value = line.substring(colon + 1).trim();
      // 同名头（set-cookie 之类）用换行拼起来，调用方需要时自己拆
      out[name] = out.containsKey(name) ? '${out[name]}\n$value' : value;
    }
    return out;
  }

  static List<int> _decode(List<int> bytes, String encoding) {
    final e = encoding.toLowerCase();
    if (bytes.isEmpty) return bytes;
    try {
      if (e.contains('gzip')) return GZipCodec().decode(bytes);
      if (e.contains('deflate')) return ZLibDecoder().convert(bytes);
    } catch (_) {
      // 解压失败就当明文用：宁可解析出错，也别把原始字节丢掉
    }
    return bytes;
  }
}

/// 从 socket 上按需取字节：先攒够响应头，再按 Content-Length / chunked 取正文。
class _StreamReader {
  _StreamReader(Socket socket)
      : _iterator = StreamIterator<List<int>>(socket);

  final StreamIterator<List<int>> _iterator;
  final List<int> _buffer = <int>[];
  bool _closed = false;

  int get buffered => _buffer.length;

  /// 再读一块进缓冲；流结束返回 false。
  Future<bool> _fill(Duration timeout) async {
    if (_closed) return false;
    final has = await _iterator.moveNext().timeout(timeout);
    if (!has) {
      _closed = true;
      return false;
    }
    _buffer.addAll(_iterator.current);
    return true;
  }

  List<int> _take(int count) {
    if (count <= 0 || _buffer.isEmpty) return const <int>[];
    final end = count > _buffer.length ? _buffer.length : count;
    final slice = _buffer.sublist(0, end);
    _buffer.removeRange(0, end);
    return slice;
  }

  /// 读到头部结束（\r\n\r\n）为止，返回头部文本（不含结尾空行）；对端提前关闭返回 null。
  Future<String?> readHead(Duration timeout) async {
    while (true) {
      final index = _indexOfHeadEnd();
      if (index >= 0) {
        final head = latin1.decode(_take(index + 4));
        return head.substring(0, head.length - 4);
      }
      if (!await _fill(timeout)) return null;
    }
  }

  int _indexOfHeadEnd() {
    for (var i = 3; i < _buffer.length; i++) {
      if (_buffer[i] == 10 &&
          _buffer[i - 1] == 13 &&
          _buffer[i - 2] == 10 &&
          _buffer[i - 3] == 13) {
        return i - 3;
      }
    }
    return -1;
  }

  Future<List<int>> readBody(
    Map<String, String> headers,
    Duration timeout,
  ) async {
    final transfer = (headers['transfer-encoding'] ?? '').toLowerCase();
    if (transfer.contains('chunked')) return _readChunked(timeout);

    final length = int.tryParse(headers['content-length'] ?? '');
    if (length != null && length >= 0) {
      final out = <int>[];
      while (out.length < length) {
        if (buffered == 0 && !await _fill(timeout)) break;
        out.addAll(_take(length - out.length));
      }
      return out;
    }

    // 没给长度（HTTP/1.0 或代理分块不明）：读到对端关闭
    final out = <int>[];
    out.addAll(_take(buffered));
    while (await _fill(timeout)) {
      out.addAll(_take(buffered));
    }
    return out;
  }

  Future<List<int>> _readChunked(Duration timeout) async {
    final out = <int>[];
    while (true) {
      // 1) 读 chunk 大小行
      var lineEnd = _indexOfCrlf();
      while (lineEnd < 0) {
        if (!await _fill(timeout)) return out;
        lineEnd = _indexOfCrlf();
      }
      final line = latin1.decode(_take(lineEnd + 2));
      final token = line.substring(0, line.indexOf('\r')).split(';').first;
      final size = int.tryParse(token.trim(), radix: 16) ?? 0;
      if (size == 0) {
        // 结尾块之后可能还有 trailer，正文已经拿齐，直接收工
        return out;
      }
      // 2) 读 size 字节
      while (buffered < size) {
        if (!await _fill(timeout)) return out;
      }
      out.addAll(_take(size));
      // 3) 吃掉块尾的 CRLF
      while (buffered < 2) {
        if (!await _fill(timeout)) return out;
      }
      _take(2);
    }
  }

  int _indexOfCrlf() {
    for (var i = 1; i < _buffer.length; i++) {
      if (_buffer[i] == 10 && _buffer[i - 1] == 13) return i - 1;
    }
    return -1;
  }
}
