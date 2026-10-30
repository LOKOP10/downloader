import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/xhs_parser.dart';

/// 小红书解析的离线回归测试：只用内嵌的 SSR 样本，不联网。
///
/// 样本按真实详情页的结构裁剪：note 转 noteDetailMap 再转 note 对象，
/// 图片走 imageList 的 urlDefault，视频走 video 下的 media.stream 按编码器分档。
void main() {
  const parser = XhsParser();

  group('parseHtml', () {
    test('图集：http 升 https、重复地址去重、标题与作者', () {
      const html = '''
<html><body><script>window.__INITIAL_STATE__={
"note":{"noteDetailMap":{"65a1b2c3d4e5f6a7b8c9d0e1":{"note":{
"noteId":"65a1b2c3d4e5f6a7b8c9d0e1",
"title":"三张图的笔记",
"desc":"图文笔记正文",
"user":{"nickname":"作者A"},
"imageList":[
 {"urlDefault":"http://sns-webpic-qc.xhscdn.com/a.jpg"},
 {"urlDefault":"https://sns-webpic-qc.xhscdn.com/b.jpg"},
 {"urlPre":"https://sns-webpic-qc.xhscdn.com/b.jpg"},
 {"urlDefault":"https://sns-webpic-qc.xhscdn.com/c.jpg"}
],
"video":{}
}}}}}</script></body></html>
''';
      final info = parser.parseHtml(html, '65a1b2c3d4e5f6a7b8c9d0e1');
      expect(info, isNotNull);
      expect(info!.title, '三张图的笔记');
      expect(info.author, '作者A');
      expect(info.isImageAlbum, isTrue);
      expect(info.images.length, 3);
      expect(info.images.first, 'https://sns-webpic-qc.xhscdn.com/a.jpg');
      expect(info.qualities, isEmpty);
      expect(info.coverUrl, info.images.first);
    });

    test('视频：裸 undefined 能修复、档位按体积降序、时长换算成毫秒', () {
      const html = '''
<script>window.__INITIAL_STATE__={
"note":{"noteDetailMap":{"65a1b2c3d4e5f6a7b8c9d0e2":{"note":{
"noteId":"65a1b2c3d4e5f6a7b8c9d0e2",
"title":"视频笔记",
"user":{"nickname":"作者B"},
"imageList":[{"urlDefault":"https://sns-webpic-qc.xhscdn.com/cover.jpg"}],
"video":{"capa":{"duration":12.5},"media":{"stream":{
 "h264":[
  {"masterUrl":"http://sns-video-bd.xhscdn.com/small.mp4","width":640,"height":360,"size":500000,"videoCodec":"h264"},
  {"masterUrl":"http://sns-video-bd.xhscdn.com/big.mp4","backupUrls":["https://sns-video-bd.xhscdn.com/big2.mp4"],"width":1280,"height":720,"size":2000000,"videoCodec":"h264"}
 ],
 "h265":undefined
}}}}}}}</script>
''';
      final info = parser.parseHtml(html, '65a1b2c3d4e5f6a7b8c9d0e2');
      expect(info, isNotNull);
      expect(info!.isImageAlbum, isFalse);
      expect(info.qualities.length, 2);
      expect(info.qualities.first.label, '1280×720 (h264)');
      expect(info.qualities.first.url, 'https://sns-video-bd.xhscdn.com/big.mp4');
      expect(info.qualities.first.sizeBytes, 2000000);
      expect(info.qualities.last.label, '640×360 (h264)');
      expect(info.durationMs, 12500);
      expect(info.coverUrl, 'https://sns-webpic-qc.xhscdn.com/cover.jpg');
    });

    test('整体 SSR 坏掉时退回「按键定位 + 括号配对」', () {
      const html = '''
<script>window.__INITIAL_STATE__={"broken":,"a":1}</script>
<script>{"noteCard":{"noteId":"65a1b2c3d4e5f6a7b8c9d0e3","title":"兜底笔记",
"imageList":[{"urlDefault":"https://sns-webpic-qc.xhscdn.com/x.jpg"}]}}</script>
''';
      final info = parser.parseHtml(html, '65a1b2c3d4e5f6a7b8c9d0e3');
      expect(info, isNotNull);
      expect(info!.title, '兜底笔记');
      expect(info.images.length, 1);
    });

    test('标题为空时用正文前 60 字', () {
      const html = '''
<script>window.__INITIAL_STATE__={"note":{"note":{"noteId":"65a1b2c3d4e5f6a7b8c9d0e4",
"title":"","desc":"这是一段很长的正文描述","imageList":[{"urlDefault":"https://a/1.jpg"}]}}}</script>
''';
      final info = parser.parseHtml(html, '65a1b2c3d4e5f6a7b8c9d0e4');
      expect(info!.title, '这是一段很长的正文描述');
    });

    test('没有媒体数据时返回 null', () {
      expect(parser.parseHtml('', 'abc'), isNull);
      expect(parser.parseHtml('<html><body>页面不存在</body></html>', 'abc'), isNull);
      expect(
          parser.parseHtml('<script>window.__INITIAL_STATE__={"note":{}}</script>', 'abc'),
          isNull);
    });
  });

  group('cleanTitle', () {
    test('话题标记不留进标题', () {
      expect(XhsParser.cleanTitle('#日常[话题]# 今天的午饭'), '今天的午饭');
      expect(XhsParser.cleanTitle('今天的午饭 #日常[话题]#'), '今天的午饭');
      expect(XhsParser.cleanTitle('#只有话题[话题]# #另一个[话题]#'), '');
    });

    test('普通标题原样保留', () {
      expect(XhsParser.cleanTitle('济州岛亲测8家美食小店'), '济州岛亲测8家美食小店');
    });
  });
}
