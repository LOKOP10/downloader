// X（推特）解析端到端实测：解析 → 列出档位 → 下载最高档到临时目录。
// 用法：dart run tool/check_x.dart <推文链接> [--dry]
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/parse/x_parser.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

Future<void> main(List<String> args) async {
  final dry = args.contains('--dry');
  final url = args.where((a) => a != '--dry').isNotEmpty
      ? args.firstWhere((a) => a != '--dry')
      : '';
  if (url.isEmpty) {
    print('用法：dart run tool/check_x.dart <推文链接> [--dry]');
    exit(2);
  }
  print('输入：' + url);
  final sw = Stopwatch()..start();
  try {
    final info = await const XParser().parse(url);
    sw.stop();
    print('✔ 解析成功（' + sw.elapsedMilliseconds.toString() + 'ms）');
    print('  标题：' + info.title);
    print('  作者：' + info.author + '  时长：' + info.durationText);
    print('  图集：' + info.images.length.toString() + ' 张');
    print('  档位：' + info.qualities.length.toString());
    for (final q in info.qualities) {
      print('    - ' + q.label);
      print('      ' + q.url);
    }
    if (dry) {
      print('（--dry：跳过下载）');
      return;
    }
    final outDir = Directory(
        Directory.systemTemp.path + Platform.pathSeparator + '_tmp_x_dl');
    await outDir.create(recursive: true);
    final headers = AppHttp.headers(platform: SitePlatform.x, ua: AppHttp.uaMobile);

    if (info.isImageAlbum) {
      final target = File(outDir.path + Platform.pathSeparator + 'x_first.jpg');
      await const Downloader().download(
        url: info.images.first,
        target: target,
        connections: 1,
        headers: headers,
        onProgress: (_) {},
      );
      print('✔ 首图下载完成：' + (await target.length()).toString() + ' 字节');
      return;
    }
    final best = info.qualities.first;
    final target = File(outDir.path + Platform.pathSeparator + 'x_best.mp4');
    print('\n下载最高档：' + best.label);
    final dlSw = Stopwatch()..start();
    await const Downloader().download(
      url: best.url,
      target: target,
      connections: 4,
      headers: headers,
      onProgress: (_) {},
    );
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
