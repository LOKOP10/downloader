import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/bili_parser.dart';
import 'package:vdl/features/parse/media_info.dart';

/// B 站解析的离线回归：只用内嵌的接口响应样本，不联网。
///
/// 样本按真实响应裁剪：view 给 bvid/title/pic/duration/cid/owner/pages，
/// playurl 给 quality + durl（音视频已合并，自带 size）。
void main() {
  const parser = BiliParser();

  Map<String, dynamic> viewData({
    String title = '测试视频',
    int duration = 213,
    int pagesCount = 1,
  }) => {
        'bvid': 'BV1GJ411x7h7',
        'title': title,
        'pic': 'http://i1.hdslb.com/bfs/archive/cover.jpg',
        'duration': duration,
        'cid': 1001,
        'owner': {'name': 'UP主'},
        'pages': [
          for (var i = 0; i < pagesCount; i++)
            {
              'cid': 1001 + i,
              'page': i + 1,
              'part': '第' + (i + 1).toString() + '集',
              'duration': duration,
            },
        ],
      };

  Map<String, dynamic> playData({
    int quality = 64,
    int size = 51973319,
    int segments = 1,
  }) => {
        'quality': quality,
        'durl': [
          for (var i = 0; i < segments; i++)
            {
              'url': 'https://cn-jsnj.bilivideo.com/seg' + i.toString() + '.mp4',
              'size': size ~/ segments,
            },
        ],
      };

  group('buildInfo', () {
    test('单P：标题、UP主、封面升 https、时长换算', () {
      final info = parser.buildInfo(
        viewData: viewData(),
        playData: [playData()],
      );
      expect(info.title, '测试视频');
      expect(info.author, 'UP主');
      expect(info.platform.label, 'B站');
      expect(info.coverUrl, 'https://i1.hdslb.com/bfs/archive/cover.jpg');
      expect(info.durationMs, 213000);
      expect(info.qualities.length, 1);
      expect(info.qualities.first.label, contains('720P'));
      expect(info.qualities.first.label, contains('49.6MB'));
      expect(info.qualities.first.url, contains('seg0.mp4'));
      expect(info.sourceId, 'BV1GJ411x7h7');
    });

    test('多P：按 ?p= 选 P，标题带 P 名', () {
      final info = parser.buildInfo(
        viewData: viewData(pagesCount: 3),
        playData: [playData()],
        pageIndex: 2,
      );
      expect(info.title, contains('P2'));
      expect(info.title, contains('第2集'));
    });

    test('服务端降级：多档同 quality 只保留一档', () {
      final qualities = parser.buildQualities([
        playData(quality: 64, size: 50000000),
        playData(quality: 64, size: 50000000),
        playData(quality: 32, size: 12000000),
      ]);
      expect(qualities.length, 2);
      // 按体积降序：720P 在前
      expect(qualities.first.label, contains('720P'));
      expect(qualities.last.label, contains('480P'));
    });

    test('长视频被拆成多段时，标签里说明只取第一段', () {
      final qualities = parser.buildQualities([
        playData(quality: 64, size: 90000000, segments: 3),
      ]);
      expect(qualities.first.label, contains('1/3'));
    });

    test('没有 durl 时抛 ParseException（番剧/付费/需登录）', () {
      expect(
        () => parser.buildInfo(
          viewData: viewData(),
          playData: [
            {'quality': 64, 'durl': []}
          ],
        ),
        throwsA(isA<ParseException>()),
      );
    });
  });

  group('DASH（音视频分离）', () {
    Map<String, dynamic> dashData({
      List<int> videoIds = const [80],
      String codecs = 'avc1.640028',
    }) => {
          'dash': {
            'duration': 213,
            'audio': [
              {'id': 30216, 'bandwidth': 66000, 'baseUrl': 'https://cdn/a-low.m4s'},
              {'id': 30280, 'bandwidth': 203786, 'baseUrl': 'https://cdn/a-best.m4s'},
            ],
            'video': [
              for (final id in videoIds)
                {
                  'id': id,
                  'width': 1920,
                  'height': 1080,
                  'bandwidth': 2000000,
                  'frameRate': '16000/672',
                  'codecs': codecs,
                  'baseUrl': 'https://cdn/v' + id.toString() + '.m4s',
                  'backupUrl': ['https://upos/v' + id.toString() + '-bak.m4s'],
                },
            ],
          },
        };

    test('生成 DASH 档位：带 audioUrl、标签含分辨率/码率/帧率', () {
      final qualities = parser.buildDashQualities(dashData(), 213);
      expect(qualities.length, 1);
      final q = qualities.first;
      expect(q.label, contains('1080P'));
      expect(q.label, contains('1920×1080'));
      expect(q.label, contains('2.0Mbps'));
      expect(q.label, contains('24fps')); // 16000/672 ≈ 23.8 → 24
      expect(q.needsMerge, isTrue);
      // 音频挑码率最高的那条
      expect(q.audioUrl, 'https://cdn/a-best.m4s');
    });

    test('同一清晰度优先 H.264（avc1），AV1 排最后', () {
      final av1 = parser.buildDashQualities(dashData(codecs: 'av01.0.08M.08'), 213);
      expect(av1.first.needsMerge, isTrue);
      final mixed = {
        'dash': {
          'audio': [
            {'bandwidth': 100000, 'baseUrl': 'https://cdn/a.m4s'}
          ],
          'video': [
            {'id': 80, 'width': 1920, 'height': 1080, 'bandwidth': 3000000, 'codecs': 'av01.0.08M.08', 'baseUrl': 'https://cdn/av1.m4s'},
            {'id': 80, 'width': 1920, 'height': 1080, 'bandwidth': 2000000, 'codecs': 'avc1.640028', 'baseUrl': 'https://cdn/avc.m4s'},
          ],
        },
      };
      final q = parser.buildDashQualities(mixed, 100).first;
      expect(q.url, 'https://cdn/avc.m4s');
    });

    test('DASH 1080P 与 durl 720P 并存时按清晰度降序，同档优先 durl', () {
      final all = parser.buildAllQualities(
        playData: [playData(quality: 64, size: 51973319)],
        dashData: dashData(),
        durationSec: 213,
      );
      expect(all.length, 2);
      expect(all.first.label, contains('1080P'));
      expect(all.first.needsMerge, isTrue);
      expect(all.last.label, contains('720P'));
      expect(all.last.needsMerge, isFalse);

      // 两边都有 720P 时，用 durl（单文件，不必合并）
      final sameId = parser.buildAllQualities(
        playData: [playData(quality: 64, size: 51973319)],
        dashData: dashData(videoIds: [64]),
        durationSec: 213,
      );
      expect(sameId.length, 1);
      expect(sameId.first.needsMerge, isFalse);
      expect(sameId.first.label, contains('720P'));
    });

    test('没有音频流时不产出需要合并的档位（避免下了没声音）', () {
      final noAudio = {
        'dash': {
          'audio': [],
          'video': [
            {'id': 80, 'width': 1920, 'height': 1080, 'bandwidth': 2000000, 'codecs': 'avc1.640028', 'baseUrl': 'https://cdn/v.m4s'},
          ],
        },
      };
      expect(parser.buildDashQualities(noAudio, 213), isEmpty);
    });
  });
}
