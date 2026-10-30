import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/pornhub_parser.dart';

/// Pornhub 解析的离线回归：只验纯函数，不联网。
///
/// flashvars 那段是重点：它是页面里几千字符的 JS 对象字面量，
/// 正则括不到结尾（后面还有别的 `};`），必须靠花括号配对数出来。
void main() {
  group('viewkey', () {
    test('标准播放页', () {
      expect(
          PornhubParser.viewkey(
              'https://www.pornhub.com/view_video.php?viewkey=6a46ec3e259f3'),
          '6a46ec3e259f3');
    });

    test('embed 链接', () {
      expect(
          PornhubParser.viewkey('https://www.pornhub.com/embed/6a46ec3e259f3'),
          '6a46ec3e259f3');
    });

    test('纯 viewkey（用户直接粘 ID）', () {
      expect(PornhubParser.viewkey('6a46ec3e259f3'), '6a46ec3e259f3');
      expect(PornhubParser.viewkey('ph6a46ec3e259f3'), 'ph6a46ec3e259f3');
    });

    test('分享文案里夹着链接也能抠出来', () {
      expect(
          PornhubParser.viewkey(
              '看这个 https://www.pornhub.com/view_video.php?viewkey=abc123def4567 好看'),
          'abc123def4567');
    });

    test('认不出来返回空串', () {
      expect(PornhubParser.viewkey('https://example.com/x'), '');
      expect(PornhubParser.viewkey('随便一段文字'), '');
    });
  });

  group('flashvarsOf', () {
    test('字符串里带花括号与 }); 干扰也能正确配对', () {
      const html = '''
        <script>
          var flashvars_54230595 = {"video_title":"标题里有 {括号} 和 \\"引号\\"","mediaDefinitions":[{"format":"mp4","videoUrl":"https://x/get_media?s=1"}],"video_duration":910};
          var other = {"a":1};
          something_else();
        </script>
      ''';
      final fv = PornhubParser.flashvarsOf(html);
      expect(fv['video_title'], '标题里有 {括号} 和 "引号"');
      expect(fv['video_duration'], 910);
      expect((fv['mediaDefinitions'] as List).length, 1);
    });

    test('页面里没有 flashvars 时返回空 Map', () {
      expect(PornhubParser.flashvarsOf('<html>nothing</html>'), isEmpty);
    });
  });

  group('durationMs', () {
    test('video_duration 是秒数', () {
      expect(PornhubParser.durationMs('{"video_duration":910}'), 910000);
    });

    test('ld+json 的 ISO 8601 兜底', () {
      expect(
          PornhubParser.durationMs('{"duration":"PT1H2M3S"}'), 3723000);
      expect(PornhubParser.durationMs('{"duration":"PT15M10S"}'), 910000);
    });

    test('都没有时返回 0（界面显示 --）', () {
      expect(PornhubParser.durationMs('<html></html>'), 0);
    });
  });

  group('标题与上传者', () {
    test('og:title 还原 HTML 实体', () {
      const html =
          '<meta property="og:title" content="A &amp; B &#39;test&#39;">';
      expect(PornhubParser.ogTitle(html), "A & B 'test'");
    });

    test('上传者优先取 author 字段', () {
      expect(PornhubParser.uploader('{"author":"someone"}'), 'someone');
    });

    test('拿不到就留空，不猜', () {
      expect(PornhubParser.uploader('<html></html>'), '');
    });
  });
}
