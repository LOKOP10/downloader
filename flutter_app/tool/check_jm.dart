// 禁漫解析端到端实测：解析 → 列章节/页数 → 下一张图并校验重排参数。
// 用法：dart run tool/check_jm.dart <album 或 photo 链接/车号>
import 'dart:io';

import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_parser.dart';
import 'package:vdl/features/manga/data/jm_scramble.dart';

Future<void> main(List<String> args) async {
  final input = args.isNotEmpty ? args.first : '';
  if (input.isEmpty) {
    print('用法：dart run tool/check_jm.dart <链接或车号>');
    exit(2);
  }
  final url = input.startsWith('http') ? input : 'https://18comic.vip/album/' + input;
  print('输入：' + url);
  final sw = Stopwatch()..start();
  try {
    final album = await JmParser.parse(url);
    sw.stop();
    print('✔ 解析成功（' + sw.elapsedMilliseconds.toString() + 'ms）');
    print('  标题：' + album.name);
    print('  作者：' + album.author);
    print('  页数：' + album.pageText);
    print('  章节：' + album.chapters.map((c) => c.id + '(' + c.label + ')').join(', '));

    var total = 0;
    for (final chapter in album.chapters) {
      final images = await JmClient.instance.chapterImages(chapter.id);
      final scramble = await JmClient.instance.scrambleId(chapter.id);
      total += images.length;
      print('  第 ' + chapter.label + '：' + images.length.toString() + ' 张，scramble_id=' + scramble);
      if (images.isNotEmpty) {
        final num = JmScramble.segmentationNum(scramble, chapter.id, images.first);
        print('    首图 ' + images.first + ' 切片数=' + num.toString());
        final bytes = await JmClient.instance.fetchImage(chapter.id, images.first);
        print('    下载成功：' + bytes.length.toString() + ' 字节');
      }
    }
    print('  合计实际页数：' + total.toString() + '（服务端 total_photos=' + album.totalPhotos.toString() + '）');
    if (album.totalPhotos > 0 && total < album.totalPhotos) {
      print('  ⚠ 实际少于 total_photos，可能有章节被限制');
    }
  } catch (e) {
    sw.stop();
    print('✘ 失败：' + e.toString());
    exitCode = 1;
  }
}
