import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/download/download_engine.dart';

void main() {
  const mb = 1024 * 1024;

  group('并发段数规划', () {
    test('小于 2MB 的文件不切段', () {
      expect(Downloader.planParts(1 * mb, 8), 1);
      expect(Downloader.planParts(2 * mb - 1, 8), 1);
    });

    test('大文件按并发数切段', () {
      expect(Downloader.planParts(64 * mb, 8), 8);
      expect(Downloader.planParts(199 * mb, 16), 16);
    });

    test('段大小不足 8MB 时自动降段数（避免碎请求拖慢）', () {
      expect(Downloader.planParts(64 * mb, 16), 8); // 64/16=4MB < 8MB → 降到 8
      expect(Downloader.planParts(20 * mb, 16), 2); // 20/8=2 段
      expect(Downloader.planParts(9 * mb, 8), 1); // 只够 1 段
    });

    test('并发数被夹到 1~16', () {
      expect(Downloader.planParts(200 * mb, 0), 1);
      expect(Downloader.planParts(200 * mb, 99), 16);
    });

    test('总长度未知（0）时退化为单连接', () {
      expect(Downloader.planParts(0, 8), 1);
    });
  });
}
