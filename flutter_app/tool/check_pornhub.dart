// Pornhub 解析端到端实测：解析 → 列档位 → 下载最高档到临时目录。
//
// 用法：dart run tool/check_pornhub <作品链接> [--dry] [--proxy 127.0.0.1:7897]
//
// Pornhub 会按 IP 限流，解析器内部已退避重试；CDN 直链必须带作品页 Referer。
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/download/hls_downloader.dart';
import 'package:vdl/features/parse/pornhub_parser.dart';
import 'package:vdl/shared/net/browser_http.dart';
import 'package:vdl/shared/net/http.dart';

Future<void> main(List<String> args) async {
  var dry = false;
  String? proxy;
  final rest = <String>[];
  for (var i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--dry':
        dry = true;
      case '--proxy':
        if (i + 1 < args.length) proxy = args[++i];
      default:
        rest.add(args[i]);
    }
  }
  final url = rest.isEmpty ? '' : rest.first;
  if (url.isEmpty) {
    print('用法：dart run tool/check_pornhub <作品链接> [--dry] [--proxy 127.0.0.1:7897]');
    exit(2);
  }
  if (proxy != null) {
    AppHttp.proxy = proxy;
    BrowserHttp.proxy = proxy;
    print('代理：' + proxy);
  }
  print('输入：' + url);

  final sw = Stopwatch()..start();
  try {
    final info = await const PornhubParser().parse(url);
    sw.stop();
    print('✔ 解析成功（' + sw.elapsedMilliseconds.toString() + 'ms）');
    print('  站点：' + info.platform.label);
    print('  标题：' + info.title);
    print('  作者：' + info.author + '  时长：' + info.durationText);
    print('  作品页 Referer：' + (info.referer.isEmpty ? '（用站点根域）' : info.referer));
    print('  档位：' + info.qualities.length.toString());
    for (final q in info.qualities) {
      final size = q.sizeBytes > 0
          ? ' · ' + (q.sizeBytes / 1024 / 1024).toStringAsFixed(1) + 'MB'
          : '';
      print('    - ' + q.label + size);
      print('      ' + q.url);
    }
    if (dry) {
      print('（--dry：跳过下载）');
      return;
    }
    if (info.qualities.isEmpty) {
      print('✘ 没有可下载档位');
      exitCode = 1;
      return;
    }
    final outDir = Directory(
        Directory.systemTemp.path + Platform.pathSeparator + '_tmp_check_pornhub_dl');
    await outDir.create(recursive: true);
    final headers = AppHttp.headers(
        platform: info.platform, ua: AppHttp.uaDesktop);
    if (info.referer.isNotEmpty) headers['Referer'] = info.referer;

    final best = info.qualities.first;
    // HLS（m3u8）走分片拼接，产物是 TS 流；其余走多连接单文件下行
    final isHls = best.url.contains('.m3u8');
    final target = File(outDir.path +
        Platform.pathSeparator +
        'check_pornhub_best' +
        (isHls ? '.ts' : '.mp4'));
    print('');
    print('下载最高档：' + best.label + (isHls ? '（HLS → .ts）' : ''));
    final dlSw = Stopwatch()..start();
    if (isHls) {
      await const HlsDownloader().download(
        url: best.url,
        target: target,
        headers: headers,
        onProgress: (_) {},
      );
    } else {
      await const Downloader().download(
        url: best.url,
        target: target,
        connections: 4,
        headers: headers,
        onProgress: (_) {},
      );
    }
    dlSw.stop();
    print('✔ 下载完成：' +
        (await target.length()).toString() +
        ' 字节，用时 ' +
        dlSw.elapsedMilliseconds.toString() +
        'ms');
    print('  文件：' + target.path);
  } catch (e) {
    sw.stop();
    print('✘ 失败：' + e.toString());
    exitCode = 1;
  }
}
