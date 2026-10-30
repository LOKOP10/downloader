import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/shared/links.dart';
import 'package:vdl/shared/site.dart';

/// 链接识别回归测试：真机上踩过「无协议头 → 整段文案被当地址 → DNS 报错」，
/// 这里把各种真实分享文案都钉住。
void main() {
  group('extract', () {
    test('无协议头的裸域名（真机遇到的格式）', () {
      const text = 'v.douyin.com/jx6dSylJqFU/ gbA:/ 12/12 :1pm d@N.jp';
      expect(LinkDetect.extract(text), 'https://v.douyin.com/jx6dSylJqFU/');
    });

    test('抖音官方分享文案（带协议 + 中文干扰）', () {
      const text = '7.43 复制打开抖音，看看【某某的作品】'
          'https://v.douyin.com/abcdefG/ 复制此链接，打开抖音搜索，直接观看视频！';
      expect(LinkDetect.extract(text), 'https://v.douyin.com/abcdefG/');
    });

    test('裸域名 + 中文尾巴', () {
      const text = '看看这个 v.douyin.com/x1y2z3/ 复制打开抖音';
      expect(LinkDetect.extract(text), 'https://v.douyin.com/x1y2z3/');
    });

    test('分享页长链原样保留参数', () {
      const text = 'https://www.iesdouyin.com/share/video/7701019311405583657/?region=CN&mid=1';
      final url = LinkDetect.extract(text)!;
      expect(url, contains('/share/video/7701019311405583657/'));
      expect(url, contains('region=CN'));
    });

    test('媒体直链', () {
      const text = 'https://example.com/videos/demo.mp4';
      expect(LinkDetect.extract(text), 'https://example.com/videos/demo.mp4');
      expect(LinkDetect.isDirectMedia(text), isTrue);
    });

    test('没有链接的文字返回 null', () {
      expect(LinkDetect.extract('随便一段没有地址的话，12/12 15:30'), isNull);
      expect(LinkDetect.extract(''), isNull);
    });
  });

  group('platformOf', () {
    test('抖音各种域名', () {
      for (final url in [
        'https://v.douyin.com/abc/',
        'https://www.douyin.com/video/123',
        'https://www.iesdouyin.com/share/video/123/',
        'v.douyin.com/abc/',
      ]) {
        expect(LinkDetect.platformOf(url), SitePlatform.douyin, reason: url);
      }
    });

    test('其它站点', () {
      expect(LinkDetect.platformOf('https://www.bilibili.com/video/BV1xx411c7mD'),
          SitePlatform.bili);
      expect(LinkDetect.platformOf('https://www.xiaohongshu.com/explore/abc'),
          SitePlatform.xhs);
      expect(LinkDetect.platformOf('https://www.iwara.tv/video/abc'),
          SitePlatform.iwara);
      expect(LinkDetect.platformOf('https://www.pornhub.com/view_video.php?viewkey=abc'),
          SitePlatform.pornhub);
      expect(LinkDetect.platformOf('https://hanime1.me/watch?v=408885'),
          SitePlatform.hanime1);
      expect(LinkDetect.platformOf('https://example.com/a.mp4'), isNull);
    });

    test('三个站点的裸域名（分享文案里常常没有协议头）', () {
      expect(LinkDetect.platformOf('www.iwara.tv/video/qR94gLGO9VGEqL'),
          SitePlatform.iwara);
      expect(LinkDetect.platformOf('www.pornhub.com/view_video.php?viewkey=6a46ec3e259f3'),
          SitePlatform.pornhub);
      expect(LinkDetect.platformOf('hanime1.me/watch?v=408885'),
          SitePlatform.hanime1);
    });
  });

  group('normalize', () {
    test('补协议头', () {
      expect(LinkDetect.normalize('v.douyin.com/a/'), 'https://v.douyin.com/a/');
      expect(LinkDetect.normalize('/v.douyin.com/a/'), 'https://v.douyin.com/a/');
      expect(LinkDetect.normalize('https://x.com/a'), 'https://x.com/a');
    });
  });
}
