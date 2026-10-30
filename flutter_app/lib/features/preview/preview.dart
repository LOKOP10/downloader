import 'package:flutter/material.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/features/preview/ui/image_viewer.dart';
import 'package:vdl/features/preview/ui/video_viewer.dart';
import 'package:vdl/shared/site.dart';

/// 预览统一入口，**所有平台共用**：
///
/// - 图集：Preview.openImages(context, media.images, initialIndex: i)
/// - 视频：Preview.openVideo(context, url: ..., title: ..., platform: ...)
/// - 不关心类型：Preview.openMedia(context, media)
///
/// 解析器只需要产出 MediaInfo（图片列表 / 播放地址），不用管预览怎么实现。
abstract final class Preview {
  static Future<void> openImages(
    BuildContext context,
    List<String> urls, {
    int initialIndex = 0,
  }) {
    if (urls.isEmpty) return Future<void>.value();
    return Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => ImageViewer(urls: urls, initialIndex: initialIndex),
    ));
  }

  static Future<void> openVideo(
    BuildContext context, {
    required String url,
    required String title,
    SitePlatform? platform,
    String referer = '',
  }) {
    return Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => VideoViewer(
        url: url,
        title: title,
        platform: platform,
        referer: referer,
      ),
    ));
  }

  /// 按解析结果自动选择：图集开图片预览，视频开播放器。
  static Future<void> openMedia(
    BuildContext context,
    MediaInfo media, {
    int qualityIndex = 0,
    int imageIndex = 0,
  }) {
    if (media.isImageAlbum) {
      return openImages(context, media.images, initialIndex: imageIndex);
    }
    if (media.qualities.isEmpty) return Future<void>.value();
    final index = qualityIndex.clamp(0, media.qualities.length - 1);
    return openVideo(
      context,
      url: media.qualities[index].url,
      title: media.title,
      platform: media.platform,
      referer: media.referer,
    );
  }
}
