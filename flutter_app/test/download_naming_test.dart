import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/download/task_store.dart';

/// 落盘命名回归。
///
/// 真机上踩到两件事：
///   1. 小红书图集 6 张图全被存成 001.mp4 / 002.mp4 —— 图片打不开，
///      因为 CDN 地址路径里没有扩展名，只写了「!nd_dft_wlteh_jpg_3」这种尾巴；
///   2. 图集文件夹名是「#职场冷知识科普[话题]# #符合[话题]# …」一串井号。
void main() {
  group('extensionFor', () {
    test('HLS 播放列表落成 .ts（分片拼好就是 TS 流，不假装是 mp4）', () {
      const url = 'https://hv-h.phncdn.com/hls/c6251/videos/202607/02/54230595/'
          '1080P_4000K_54230595.mp4/master.m3u8?h=abc&e=1793273610&f=1';
      expect(TaskStore.extensionFor(url), '.ts');
      expect(TaskStore.isHlsUrl(url), isTrue);
      expect(TaskStore.isHlsUrl('https://x/a.mp4'), isFalse);
    });

    test('小红书图片：路径无扩展名，靠 !nd_dft_wlteh_jpg_3 尾巴认 .jpg', () {
      const url = 'https://sns-webpic-qc.xhscdn.com/202610290926/'
          '4cd4ec1e78b012ba19fbb944c0ac156c/'
          '1040g00831q6o5istng005nf4r9b08t93dua3hd0!nd_dft_wlteh_jpg_3';
      expect(TaskStore.extensionFor(url, fallback: '.jpg'), '.jpg');
    });

    test('小红书 webp 预览变体认成 .webp', () {
      const url = 'https://sns-webpic-qc.xhscdn.com/202610290926/abc/'
          '1040g00831q6o5istng005nf4r9b08t93dua3hd0!nd_prv_wlteh_webp_3';
      expect(TaskStore.extensionFor(url, fallback: '.jpg'), '.webp');
    });

    test('小红书视频地址带真扩展名，后缀参数不影响', () {
      const url = 'https://sns-video-v6.xhscdn.com/stream/1/110/258/'
          '01ea096c3318b652010370019e34d06e2d_258.mp4?sign=df22a4e6&t=6ae73e4d';
      expect(TaskStore.extensionFor(url), '.mp4');
    });

    test('抖音图集：带 .webp 的认识别，只有 ~tplv 尾巴的按图片兜底', () {
      const withExt = 'https://p3-sign.douyinpic.com/tos-cn-i-0813/abc~tplv-dy-aweme-images:q75.webp?x-expires=1';
      expect(TaskStore.extensionFor(withExt, fallback: '.jpg'), '.webp');
      const noExt = 'https://p3-sign.douyinpic.com/tos-cn-i-0813/abc~tplv-obj.image';
      expect(TaskStore.extensionFor(noExt, fallback: '.jpg'), '.jpg');
    });

    test('看不出格式时用调用方给的兜底', () {
      expect(TaskStore.extensionFor('https://example.com/download', fallback: '.jpg'),
          '.jpg');
      expect(TaskStore.extensionFor('https://example.com/download'), '.mp4');
    });
  });

  group('safeName', () {
    test('话题标记清干净：不留井号、方括号与「话题」字样', () {
      const raw = '#职场冷知识科普[话题]# #符号[话题]# #职... #薪酬[话题]#';
      final name = TaskStore.safeName(raw);
      expect(name.contains('#'), isFalse);
      expect(name.contains('['), isFalse);
      expect(name.contains('话题'), isFalse);
    });

    test('正常标题原样保留', () {
      expect(TaskStore.safeName('快速自搭了一套小葱新衣服'), '快速自搭了一套小葱新衣服');
    });

    test('只剩话题时给兜底名，不会得到空文件名', () {
      final name = TaskStore.safeName('#只有话题[话题]#');
      expect(name.isEmpty, isFalse);
      expect(name.startsWith('media_'), isTrue);
    });

    test('非法文件名字符换成空格', () {
      expect(TaskStore.safeName('a/b:c*d?e'), 'a b c d e');
    });
  });

  group('galleryFileName', () {
    test('图集平铺命名：标题_001、标题_002', () {
      expect(TaskStore.galleryFileName('小葱新衣服', 0), '小葱新衣服_001');
      expect(TaskStore.galleryFileName('小葱新衣服', 9), '小葱新衣服_010');
    });

    test('超长标题截断后仍带序号', () {
      final long = 'a' * 100;
      final name = TaskStore.galleryFileName(long, 0);
      expect(name.endsWith('_001'), isTrue);
      expect(name.length <= 64, isTrue);
    });
  });
}
