// B 站解析端到端实测：解析 → 列出档位 → 下载最高档到临时目录。
// 用法：dart run tool/check_bili.dart <视频链接或 BV 号> [--dry]（--dry 只解析不下载）
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/parse/bili_parser.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

Future<void> main(List<String> args) async {
  final dry = args.contains('--dry');
  final input = args.where((a) => a != '--dry').isNotEmpty
      ? args.firstWhere((a) => a != '--dry')
      : '';
  if (input.isEmpty) {
    print('用法：dart run tool/check_bili.dart <视频链接或 BV 号>');
    exit(2);
  }
  print('输入：' + input);
  final sw = Stopwatch()..start();
  try {
    final info = await const BiliParser().parse(input);
    sw.stop();
    print('✔ 解析成功（' + sw.elapsedMilliseconds.toString() + 'ms）');
    print('  标题：' + info.title);
    print('  作者：' + info.author + '  时长：' + info.durationText);
    print('  封面：' + info.coverUrl);
    print('  档位：' + info.qualities.length.toString());
    for (final q in info.qualities) {
      print('    - ' + q.label + (q.needsMerge ? '  [需合并音轨]' : ''));
      print('      ' + q.url.substring(0, q.url.length.clamp(0, 110)));
    }
    if (dry) {
      print('（--dry：跳过下载）');
      return;
    }
    final best = info.qualities.first;
    final outDir = Directory(
        Directory.systemTemp.path + Platform.pathSeparator + '_tmp_bili_dl');
    await outDir.create(recursive: true);
    final target = File(outDir.path + Platform.pathSeparator + 'bili.mp4');
    print('\n下载最高档：' + best.label);
    final dlSw = Stopwatch()..start();
    var last = -1.0;
    await const Downloader().download(
      url: best.url,
      target: target,
      connections: 4,
      // B 站 DASH 流只认桌面 UA
      headers: AppHttp.headers(platform: SitePlatform.bili, ua: AppHttp.uaDesktop),
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
