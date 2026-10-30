import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/download/data/download_location.dart';

void main() {
  group('路径规范化', () {
    test('漏了开头的斜杠要补上（真机上踩到的写法）', () {
      expect(
        DownloadLocation.normalizePath('storage/emulated/0/Pictures/Gallery'),
        '/storage/emulated/0/Pictures/Gallery',
      );
      expect(
        DownloadLocation.normalizePath('sdcard/Download'),
        '/sdcard/Download',
      );
    });

    test('去掉结尾与重复的斜杠', () {
      expect(
        DownloadLocation.normalizePath('/storage/emulated/0/Pictures/Gallery/'),
        '/storage/emulated/0/Pictures/Gallery',
      );
      expect(
        DownloadLocation.normalizePath('//storage//emulated/0//Pic//'),
        '/storage/emulated/0/Pic',
      );
    });

    test('空值与非 Android 路径原样保留', () {
      expect(DownloadLocation.normalizePath(''), '');
      expect(DownloadLocation.normalizePath('C:\\Users\\x\\Downloads'),
          'C:\\Users\\x\\Downloads');
    });
  });
}
