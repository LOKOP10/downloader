import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/features/parse/x_parser.dart';

/// X（推特）解析的离线回归：用真实 syndication 响应的结构裁剪成样本。
///
/// 实测注意点：variants 里没有 bitrate，分辨率只能从地址路径抠；
/// HLS(m3u8) 变体要跳过；图片走 photos[]，老接口走 mediaDetails[]。
void main() {
  const parser = XParser();

  Map<String, dynamic> videoData({
    int durationMs = 59893,
    List<String> heights = const ['480x270', '640x360', '1280x720', '1920x1080'],
  }) => {
        '__typename': 'Tweet',
        'text': 'We are not slowing down.  Our Artemis III mission will see '
            'humanity return to the lunar surface.\nhttps://t.co/abcdefg',
        'user': {'name': 'NASA', 'screen_name': 'NASA'},
        'favorite_count': 1234,
        'video': {
          'durationMs': durationMs,
          'variants': [
            {
              'type': 'application/x-mpegURL',
              'src': 'https://video.twimg.com/x/pl/abc.m3u8?tag=16',
            },
            for (final wh in heights)
              {
                'type': 'video/mp4',
                'src': 'https://video.twimg.com/amplify_video/1/vid/avc1/' +
                    wh +
                    '/clip.mp4?tag=16',
              },
          ],
        },
        'mediaDetails': [
          {
            'type': 'video',
            'media_url_https': 'https://pbs.twimg.com/amplify_thumb/one.jpg',
          }
        ],
        'photos': [],
      };

  group('buildInfo', () {
    test('视频推文：跳过 m3u8、按分辨率降序、标题去掉 t.co', () {
      final info = parser.buildInfo(videoData(), '2113776971603689874');
      expect(info.platform.label, 'X');
      expect(info.author, 'NASA');
      expect(info.title.contains('t.co'), isFalse);
      expect(info.title.contains('Artemis III'), isTrue);
      expect(info.durationMs, 59893);
      expect(info.sourceId, '2113776971603689874');

      expect(info.qualities.length, 4);
      expect(info.qualities.first.label, '1920×1080');
      expect(info.qualities.last.label, '480×270');
      expect(info.qualities.first.url, contains('1920x1080'));
      // 有视频就不当图集
      expect(info.isImageAlbum, isFalse);
      // 封面取视频缩略图
      expect(info.coverUrl, contains('amplify_thumb'));
    });

    test('多图推文：走图集，按 photos 顺序', () {
      final data = {
        '__typename': 'Tweet',
        'text': 'Night sky this week',
        'user': {'name': 'NASA', 'screen_name': 'NASA'},
        'photos': [
          {'url': 'https://pbs.twimg.com/media/a.jpg'},
          {'url': 'https://pbs.twimg.com/media/b.jpg'},
          {'url': 'https://pbs.twimg.com/media/c.jpg'},
          {'url': 'https://pbs.twimg.com/media/d.jpg'},
        ],
        'mediaDetails': [
          {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/media/a.jpg'},
        ],
      };
      final info = parser.buildInfo(data, '2114130258912043194');
      expect(info.isImageAlbum, isTrue);
      expect(info.images.length, 4);
      expect(info.images.first, 'https://pbs.twimg.com/media/a.jpg');
      expect(info.qualities, isEmpty);
      expect(info.coverUrl, 'https://pbs.twimg.com/media/a.jpg');
    });

    test('老接口只有 mediaDetails 时也能补出图片', () {
      final data = {
        '__typename': 'Tweet',
        'text': 'photo only',
        'user': {'name': 'someone'},
        'mediaDetails': [
          {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/media/x.jpg'},
          {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/media/y.jpg'},
        ],
      };
      final info = parser.buildInfo(data, '1');
      expect(info.images.length, 2);
      expect(info.coverUrl, 'https://pbs.twimg.com/media/x.jpg');
    });

    test('没有媒体时抛 ParseException', () {
      expect(
        () => parser.buildInfo({
          '__typename': 'Tweet',
          'text': 'just setting up my twttr',
          'user': {'name': 'jack'},
        }, '20'),
        throwsA(isA<ParseException>()),
      );
    });
  });

  group('syndicationToken', () {
    test('与 JS/Python 实现一致（钉住算法，避免 36 进制退化）', () {
      // ((20/1e15)*PI).toString(36).replace(/(0+|\.)/g,'') === '6dq1a2xwd93'
      expect(XParser.syndicationToken('20'), '6dq1a2xwd93');
      // 现代 19 位推文 ID
      expect(XParser.syndicationToken('2113776971603689874'), isNotEmpty);
      expect(XParser.syndicationToken('2113776971603689874').contains('.'), isFalse);
      expect(XParser.syndicationToken('2113776971603689874').contains('0'), isFalse);
    });
  });

  group('cleanTitle', () {
    test('截断尾部 t.co 与多余空白', () {
      expect(
        XParser.cleanTitle('Hello world\nhttps://t.co/abc'),
        'Hello world',
      );
      expect(XParser.cleanTitle('  多个   空格  '), '多个 空格');
      expect(XParser.cleanTitle('看这个 https://example.com/a 很好'), '看这个 很好');
    });
  });

  group('buildFromGraphql', () {
    Map<String, dynamic> mediaItem({
      String type = 'video',
      List<String> heights = const ['480x270', '1280x720'],
    }) => {
          'type': type,
          'media_url_https': 'https://pbs.twimg.com/thumb.jpg',
          'video_info': {
            'duration_millis': 59893,
            'variants': [
              {'content_type': 'application/x-mpegURL', 'url': 'https://video.twimg.com/x.m3u8'},
              for (final wh in heights)
                {
                  'content_type': 'video/mp4',
                  'bitrate': 1000,
                  'url': 'https://video.twimg.com/vid/avc1/' + wh + '/clip.mp4',
                },
            ],
          },
        };

    Map<String, dynamic> tweet({
      String typename = 'Tweet',
      List<Map<String, dynamic>>? media,
      Map<String, dynamic>? retweet,
    }) => {
          '__typename': typename,
          'legacy': {
            'full_text': 'GraphQL 正文 https://t.co/zzz',
            if (media != null)
              'extended_entities': {'media': media},
            if (retweet != null) 'retweeted_status_result': {'result': retweet},
          },
          'core': {
            'user_results': {
              'result': {
                'core': {'name': 'NASA', 'screen_name': 'NASA'},
              }
            }
          },
        };

    test('视频：写 content_type/url 也能认，跳过 m3u8，标题去短链', () {
      final info = parser.buildFromGraphql(
        tweet(media: [mediaItem()]),
        '2113776971603689874',
      );
      expect(info, isNotNull);
      expect(info!.author, 'NASA');
      expect(info.title, 'GraphQL 正文');
      expect(info.qualities.length, 2);
      expect(info.qualities.first.label, '1280×720');
      expect(info.durationMs, 59893);
    });

    test('TweetWithVisibilityResults 要拆开取里面那条推文', () {
      final inner = tweet(media: [mediaItem(type: 'photo')]);
      // photo 分支没有 video_info，改造成图片
      (inner['legacy'] as Map)['extended_entities'] = {
        'media': [
          {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/media/a.jpg'},
        ],
      };
      final info = parser.buildFromGraphql(
        {'__typename': 'TweetWithVisibilityResults', 'tweet': inner},
        '1',
      );
      expect(info, isNotNull);
      expect(info!.images.single, 'https://pbs.twimg.com/media/a.jpg');
    });

    test('转推：媒体在被转推的原推文里', () {
      final info = parser.buildFromGraphql(
        tweet(retweet: tweet(media: [mediaItem()])),
        '2',
      );
      expect(info, isNotNull);
      expect(info!.qualities.length, 2);
    });

    test('墓碑（敏感内容）返回 null，交给调用方按登录态提示', () {
      expect(
        parser.buildFromGraphql({'__typename': 'TweetTombstone'}, '3'),
        isNull,
      );
      expect(
        parser.buildFromGraphql(
            {'__typename': 'TweetUnavailable', 'reason': 'Nsfw'}, '4'),
        isNull,
      );
    });

    test('纯文字推文返回 null', () {
      expect(parser.buildFromGraphql(tweet(), '5'), isNull);
    });
  });
}
