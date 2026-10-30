import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/hanime1_parser.dart';

/// hanime1 解析的离线回归：只验纯函数，不联网。
///
/// 页面里三个 <source> 的顺序是乱的（720 → 480 → 1080），
/// 档位高度只能从 size 属性读，不能靠下标。
void main() {
  group('videoId', () {
    test('标准 watch 链接', () {
      expect(Hanime1Parser.videoId('https://hanime1.me/watch?v=408885'),
          '408885');
    });

    test('纯数字 id', () {
      expect(Hanime1Parser.videoId('408885'), '408885');
    });

    test('路径式 / 别的参数名都不认（站点自己也不认）', () {
      expect(Hanime1Parser.videoId('https://hanime1.me/watch/408885'), '');
      expect(Hanime1Parser.videoId('https://hanime1.me/watch?id=408885'), '');
    });
  });

  group('sources', () {
    test('按 size 属性取档位，不靠 HTML 顺序', () {
      const html = '''
        <video poster="https://x/poster.jpg">
          <source src="https://vdownload.hembed.com/408885-720p.mp4?secure=abc,1" size="720" type="video/mp4">
          <source src="https://vdownload.hembed.com/408885-480p.mp4?secure=def,1" size="480" type="video/mp4">
          <source src="https://vdownload.hembed.com/408885-1080p.mp4?secure=ghi,1" size="1080" type="video/mp4">
        </video>
      ''';
      final found = Hanime1Parser.sources(html);
      expect(found.length, 3);
      expect(found.map((s) => s.height).toList(), [720, 480, 1080]);
      expect(found.first.url, contains('720p.mp4'));
    });

    test('属性顺序颠倒也能取（src 在后）', () {
      const html = '<source size="1080" type="video/mp4" '
          'src="https://vdownload.hembed.com/1-1080p.mp4">';
      final found = Hanime1Parser.sources(html);
      expect(found.length, 1);
      expect(found.single.height, 1080);
    });

    test('非 mp4 的 source 跳过', () {
      const html = '<source src="https://x/a.webm" size="1080" '
          'type="video/webm">';
      expect(Hanime1Parser.sources(html), isEmpty);
    });
  });

  group('pageTitle', () {
    test('详情块的 h3 优先，且去掉标签', () {
      const html = '<h3 id="shareBtn-title"><span>标题</span> &amp; 副标题</h3>';
      expect(Hanime1Parser.pageTitle(html), '标题 & 副标题');
    });

    test('兜底 og:title 时去掉站点后缀', () {
      const html =
          '<meta property="og:title" content="某番剧 - Hanime1.me">';
      expect(Hanime1Parser.pageTitle(html), '某番剧');
    });
  });

  group('uploader / cover', () {
    test('上传者取 a#video-artist-name', () {
      const html = '<a id="video-artist-name" href="#">社团 &amp; 作者</a>';
      expect(Hanime1Parser.uploader(html), '社团 & 作者');
    });

    test('没有 artist 时退回头像 alt', () {
      const html = '<img id="video-user-avatar" alt="作者名">';
      expect(Hanime1Parser.uploader(html), '作者名');
    });

    test('封面优先 video poster', () {
      const html = '<video poster="https://x/p.jpg"><source src="a.mp4"></video>';
      expect(Hanime1Parser.cover(html), 'https://x/p.jpg');
    });

    test('没有 poster 时退回 og:image', () {
      const html = '<meta property="og:image" content="https://x/og.jpg">';
      expect(Hanime1Parser.cover(html), 'https://x/og.jpg');
    });
  });
}
