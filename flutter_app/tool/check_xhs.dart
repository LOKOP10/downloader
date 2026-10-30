// 小红书解析端到端实测：解析 → 列出档位 → 下载最高档 / 首张图到临时目录。
// 用法：dart run tool/check_xhs.dart [分享链接或笔记链接]
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/parse/xhs_parser.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

Future<void> main(List<String> args) async {
  final url = args.isNotEmpty ? args.first : '';
  if (url.isEmpty) {
    print('用法：dart run tool/check_xhs.dart <小红书分享链接>');
    exit(2);
  }
  print('输入：' + url);
  final sw = Stopwatch()..start();
  try {
    final info = await const XhsParser().parse(url);
    sw.stop();
    print('✔ 解析成功（' + sw.elapsedMilliseconds.toString() + 'ms）');
    print('  标题：' + info.title);
    print('  作者：' + info.author + '  时长：' + info.durationText);
    print('  图集：' + info.images.length.toString() + ' 张');
    print('  画质档位：' + info.qualities.length.toString());
    for (final q in info.qualities) {
      final size =
          q.sizeBytes > 0 ? ' ' + (q.sizeBytes / 1024 / 1024).toStringAsFixed(1) + 'MB' : '';
      print('    - ' + q.label + size);
      print('      ' + q.url);
    }
    final outDir = Directory(
        Directory.systemTemp.path + Platform.pathSeparator + '_tmp_xhs_dl');
    await outDir.create(recursive: true);

    if (info.isImageAlbum) {
      final first = info.images.first;
      final target = File(outDir.path + Platform.pathSeparator + 'xhs_first.jpg');
      print('\n下载首张图：' + first);
      await const Downloader().download(
        url: first,
        target: target,
        connections: 1,
        headers: AppHttp.headers(platform: SitePlatform.xhs, ua: AppHttp.uaMobile),
        onProgress: (_) {},
      );
      print('✔ 下载完成：' + (await target.length()).toString() + ' 字节');
      print('  文件：' + target.path);
      return;
    }

    final best = info.qualities.first;
    final target = File(outDir.path + Platform.pathSeparator + 'xhs_best.mp4');
    print('\n下载最高档：' + best.label);
    final dlSw = Stopwatch()..start();
    var last = -1.0;
    await const Downloader().download(
      url: best.url,
      target: target,
      connections: 4,
      headers: AppHttp.headers(platform: SitePlatform.xhs, ua: AppHttp.uaMobile),
      onProgress: (p) {
        if ((p.ratio * 10).floor() != (last * 10).floor()) {
          last = p.ratio;
          print('  ' + (p.ratio * 100).toStringAsFixed(0) + '%  ' + p.speedText);
        }
      },
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
