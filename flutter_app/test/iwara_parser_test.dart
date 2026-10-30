import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/parse/iwara_parser.dart';

/// Iwara 解析的离线回归：只验纯函数，不联网。
///
/// 签名算法与桌面端 `sites.py::_iwara_sign` 逐字一致：
/// sha1(`<fileId>_<expires>_<salt>`)。样值由 Python hashlib 独立算过，
/// 两边必须一模一样，否则档位接口会静默降级（只回 360/preview）。
void main() {
  group('videoId', () {
    test('video 链接', () {
      expect(IwaraParser.videoId('https://www.iwara.tv/video/qR94gLGO9VGEqL'),
          'qR94gLGO9VGEqL');
    });

    test('image 链接也认（同一个详情接口）', () {
      expect(IwaraParser.videoId('https://www.iwara.tv/image/AbCd123'),
          'AbCd123');
    });

    test('带参数与服务端后缀不影响', () {
      expect(
          IwaraParser.videoId(
              'https://www.iwara.tv/video/qR94gLGO9VGEqL?utm=x&playlist=1'),
          'qR94gLGO9VGEqL');
    });

    test('不是 iwara 链接返回空串', () {
      expect(IwaraParser.videoId('https://example.com/video/abc'), '');
    });
  });

  group('sign', () {
    test('与 Python hashlib 的已知值一致', () {
      expect(
        IwaraParser.sign('ffec0e6b-b92a-4fe0-ae35-11f2b0844b99', '1793273322'),
        '148fc8e29da15272da825246db4a62e693482cd3',
      );
    });
  });

  group('rankOf / qualityLabel', () {
    test('Source 排最前，其余按分辨率降序', () {
      final names = ['360', 'Source', '540', 'preview'];
      names.sort((a, b) => IwaraParser.rankOf(b) - IwaraParser.rankOf(a));
      expect(names, ['Source', '540', '360', 'preview']);
    });

    test('Source 用作品原始分辨率标注', () {
      expect(IwaraParser.qualityLabel('Source', 1920, 1080), '1920×1080 原始');
      expect(IwaraParser.qualityLabel('Source', 0, 0), '原始画质');
    });

    test('数字档位名换算成 Np', () {
      expect(IwaraParser.qualityLabel('540', 0, 0), '540p');
      expect(IwaraParser.qualityLabel('360p', 0, 0), '360p');
    });
  });

  group('absUrl', () {
    test('协议相对地址补 https', () {
      expect(IwaraParser.absUrl('//bailu.iwara.tv/x.mp4'),
          'https://bailu.iwara.tv/x.mp4');
    });

    test('站内绝对路径补域名', () {
      expect(IwaraParser.absUrl('/download?x=1'),
          'https://www.iwara.tv/download?x=1');
    });

    test('完整地址原样返回', () {
      expect(IwaraParser.absUrl('https://a.b/c.mp4'), 'https://a.b/c.mp4');
    });
  });
}
