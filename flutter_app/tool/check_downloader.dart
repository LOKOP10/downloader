// 下载引擎离线实测：真实 HTTP、分段、取消，以及分段写入的内容正确性。
import 'dart:async';
import 'dart:io';

import 'package:vdl/features/download/download_engine.dart';

const outDir = 'C:\\Users\\18752\\AppData\\Local\\Temp\\_tmp_dl_check';

Future<void> main() async {
  await Directory(outDir).create(recursive: true);

  const url = 'https://download.samplelib.com/mp4/sample-5s.mp4';
  final single = File('${outDir}\\cmp_single.mp4');
  final multi = File('${outDir}\\cmp_multi.mp4');

  final sw = Stopwatch()..start();
  await const Downloader().download(
      url: url, target: single, connections: 1, onProgress: (_) {});
  final singleMs = sw.elapsedMilliseconds;

  sw.reset();
  await const Downloader().download(
      url: url, target: multi, connections: 4, onProgress: (_) {});
  final multiMs = sw.elapsedMilliseconds;

  final a = await single.readAsBytes();
  final b = await multi.readAsBytes();
  var diff = -1;
  if (a.length != b.length) {
    diff = -2;
  } else {
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) {
        diff = i;
        break;
      }
    }
  }
  print('单连接：${a.length} 字节 / ${singleMs}ms');
  print('4 连接：${b.length} 字节 / ${multiMs}ms');
  print(diff == -1
      ? '✔ 两种方式字节完全一致（分段写入没有错位）'
      : '✘ 内容不一致：${diff == -2 ? "长度不同" : "首个差异字节位置 $diff"}');

  // 取消
  final token = CancelToken();
  final timer = Timer(const Duration(milliseconds: 300), token.cancel);
  var killed = false;
  try {
    await const Downloader().download(
        url: url,
        target: File('${outDir}\\cmp_cancel.mp4'),
        connections: 4,
        cancelToken: token,
        onProgress: (_) {});
  } on DownloadCancelled {
    killed = true;
  } finally {
    timer.cancel();
  }
  print('取消测试：${killed ? "✔ 中断成功" : "✘ 未中断"}');
}
