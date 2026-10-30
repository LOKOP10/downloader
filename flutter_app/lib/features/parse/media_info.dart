import 'package:vdl/shared/site.dart';

/// 一档画质。
class MediaQuality {
  const MediaQuality({
    required this.label,
    required this.url,
    this.sizeBytes = 0,
    this.audioUrl,
  });

  final String label;
  final String url;
  final int sizeBytes;

  /// DASH 分离出来的音频轨地址（B 站 1080P / 4K 才有）。
  /// 有值时下载完视频还要拉这条音频，再合并成一个 mp4。
  final String? audioUrl;

  bool get needsMerge => audioUrl != null && audioUrl!.isNotEmpty;
}

/// 一次解析的产物（与 UI 无关）。
class MediaInfo {
  const MediaInfo({
    required this.platform,
    required this.title,
    this.author = '',
    this.coverUrl = '',
    this.durationMs = 0,
    this.qualities = const [],
    this.images = const [],
    this.sourceId,
    this.referer = '',
  });

  final SitePlatform platform;
  final String title;
  final String author;
  final String coverUrl;
  final int durationMs;
  final List<MediaQuality> qualities;

  /// 作品在站点侧的 ID（抖音的 aweme_id）。补档 / 预览等二次请求要用。
  final String? sourceId;

  /// 图集作品的图片地址（视频作品为空）。
  final List<String> images;

  /// 下载与预览时要带的 Referer。
  ///
  /// 多数站点补站根就行（`AppHttp.refererOf`），但 Pornhub 的 CDN 直链
  /// **必须带视频页**做 Referer，否则一律 404，所以由解析器显式给出作品页地址。
  /// 为空时回落到站点根域。
  final String referer;

  bool get isImageAlbum => images.isNotEmpty;

  String get durationText {
    if (durationMs <= 0) return '--';
    final total = durationMs ~/ 1000;
    final m = total ~/ 60;
    final s = total % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}

class ParseException implements Exception {
  ParseException(this.message);

  final String message;

  @override
  String toString() => message;
}
