// 抖音解析端到端实测：解析 → 列出档位 → 下载最高档。
// 用法：dart run tool/check_douyin.dart [分享链接]
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/parse/douyin_parser.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

Future<void> main(List<String> args) async {
  final url = args.isNotEmpty ? args.first : 'https://v.douyin.com/oCkjqDMTkzY/';
  print('输入：$url');
  final sw = Stopwatch()..start();
  try {
    final info = await const DouyinParser().parse(url);
    sw.stop();
    print('✔ 解析成功（${sw.elapsedMilliseconds}ms）');
    print('  标题：${info.title}');
    print('  作者：${info.author}  时长：${info.durationText}');
    print('  封面：${info.coverUrl.isEmpty ? "(无)" : info.coverUrl.substring(0, info.coverUrl.length.clamp(0, 90))}');
    print('  图集：${info.images.length} 张');
  print('  作品 ID：${info.sourceId ?? '（空！补档会失效）'}');
    print('  画质档位：${info.qualities.length}');
    for (final q in info.qualities.take(6)) {
      final size = q.sizeBytes > 0 ? ' ${(q.sizeBytes / 1024 / 1024).toStringAsFixed(1)}MB' : '';
      print('    - ${q.label}$size');
      print('      ${q.url.substring(0, q.url.length.clamp(0, 110))}');
    }
    if (info.qualities.isEmpty) {
      print('（无视频档位，可能是图集作品）');
      return;
    }
    // 下载最高档验证端到端
    final outDir = Directory('C:\\Users\\18752\\AppData\\Local\\Temp\\_tmp_dy_dl');
    await outDir.create(recursive: true);
    final target = File('${outDir.path}\\douyin_best.mp4');
    final best = info.qualities.first;
    print('\n下载最高档：${best.label}');
    final dlSw = Stopwatch()..start();
    var lastRatio = -1.0;
    await const Downloader().download(
      url: best.url,
      target: target,
      connections: 4,
      headers: AppHttp.headers(platform: SitePlatform.douyin, ua: AppHttp.uaMobile),
      onProgress: (p) {
        final r = p.ratio;
        if ((r * 10).floor() != (lastRatio * 10).floor()) {
          lastRatio = r;
          print('  ${(r * 100).toStringAsFixed(0)}%  ${p.receivedText} / ${p.totalText}  ${p.speedText}');
        }
      },
    );
    dlSw.stop();
    final size = await target.length();
    print('✔ 下载完成：${size} 字节，用时 ${dlSw.elapsedMilliseconds}ms');
    print('  文件：${target.path}');
  } catch (e) {
    sw.stop();
    print('✘ 失败：$e');
  }
}
