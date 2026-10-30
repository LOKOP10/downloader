import 'package:vdl/features/parse/bili_parser.dart';
import 'package:vdl/features/parse/douyin_parser.dart';
import 'package:vdl/features/parse/hanime1_parser.dart';
import 'package:vdl/features/parse/iwara_parser.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/features/parse/pornhub_parser.dart';
import 'package:vdl/features/parse/x_parser.dart';
import 'package:vdl/features/parse/xhs_parser.dart';
import 'package:vdl/shared/site.dart';

/// 解析器注册表：把「站点 → 解析实现」收在一个地方。
///
/// 新增站点只要：写一个 Parser（返回 MediaInfo）+ 在这里登记一行，
/// 首页与下载链路都不用改。
abstract final class ParserRegistry {
  static const DouyinParser _douyin = DouyinParser();
  static const XhsParser _xhs = XhsParser();
  static const BiliParser _bili = BiliParser();
  static const XParser _x = XParser();
  static const IwaraParser _iwara = IwaraParser();
  static const PornhubParser _pornhub = PornhubParser();
  static const Hanime1Parser _hanime1 = Hanime1Parser();

  /// 该站点是否已经能解析。
  static bool supports(SitePlatform platform) =>
      platform == SitePlatform.douyin ||
      platform == SitePlatform.xhs ||
      platform == SitePlatform.bili ||
      platform == SitePlatform.x ||
      platform == SitePlatform.iwara ||
      platform == SitePlatform.pornhub ||
      platform == SitePlatform.hanime1 ||
      platform == SitePlatform.jm;

  static List<SitePlatform> get readySites =>
      SitePlatform.values.where(supports).toList();

  static Future<MediaInfo> parse(SitePlatform platform, String url) {
    switch (platform) {
      case SitePlatform.douyin:
        return _douyin.parse(url);
      case SitePlatform.xhs:
        return _xhs.parse(url);
      case SitePlatform.bili:
        return _bili.parse(url);
      case SitePlatform.x:
        return _x.parse(url);
      case SitePlatform.iwara:
        return _iwara.parse(url);
      case SitePlatform.pornhub:
        return _pornhub.parse(url);
      case SitePlatform.hanime1:
        return _hanime1.parse(url);

      case SitePlatform.jm:
        // 禁漫产物是一本书（zip），首页会先拦下来走 JmParser + JmDownloader，
        // 这里只是兜底：真走到这儿说明调用方绕过了首页。
        throw ParseException('禁漫请回到首页解析，用「下载到书架」保存整本');
    }
  }
}
