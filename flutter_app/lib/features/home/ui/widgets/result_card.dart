import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/status_pill.dart';
import 'package:vdl/features/parse/media_info.dart';

/// 解析结果卡：封面 + 标题 + 元信息 + 画质行 + 下载按钮。
class HomeResultCard extends StatelessWidget {
  const HomeResultCard({
    super.key,
    required this.media,
    required this.qualityIndex,
    required this.onPickQuality,
    required this.onDownload,
    required this.onPreview,
  });

  final MediaInfo media;
  final int qualityIndex;
  final VoidCallback onPickQuality;
  final VoidCallback onDownload;

  /// 点封面/播放键预览（视频进播放器，图集进图片浏览）。
  final VoidCallback onPreview;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final isAlbum = media.isImageAlbum;
    final hasVideo = media.qualities.isNotEmpty;
    final canDownload = isAlbum || hasVideo;
    // 图集时 qualities 为空，绝不能按索引取档（越界会让整个卡片 build 崩掉）
    final quality = hasVideo
        ? media.qualities[qualityIndex.clamp(0, media.qualities.length - 1)]
        : null;
    return Column(
      children: [
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _Cover(
                    url: media.coverUrl,
                    playable: !media.isImageAlbum && media.qualities.isNotEmpty,
                    onTap: onPreview,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          media.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: palette.titleText,
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                            height: 1.3,
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          [media.author, media.durationText, media.platform.label]
                              .where((s) => s.isNotEmpty)
                              .join(' · '),
                          style: TextStyle(color: palette.hint, fontSize: 12),
                        ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: [
                            if (media.isImageAlbum)
                              StatusPill('图集 ${media.images.length} 张',
                                  color: palette.warning)
                            else
                              StatusPill('视频 ${media.qualities.length} 档',
                                  color: palette.success),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (hasVideo && quality != null) ...[
                const SizedBox(height: 12),
                Divider(height: 1, color: palette.rowDivider),
                InkWell(
                  onTap: onPickQuality,
                  child: SizedBox(
                    height: 46,
                    child: Row(
                      children: [
                        Text('画质',
                            style: TextStyle(
                                color: palette.labelText, fontSize: 14)),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            quality.label +
                                (quality.sizeBytes > 0
                                    ? ' · ${(quality.sizeBytes / 1024 / 1024).toStringAsFixed(1)}MB'
                                    : ''),
                            textAlign: TextAlign.right,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(color: palette.hint, fontSize: 13),
                          ),
                        ),
                        const SizedBox(width: 4),
                        Icon(Icons.chevron_right,
                            color: palette.chevron, size: 20),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: AppSize.cardGap),
        Row(
          children: [
            Expanded(
              child: SizedBox(
                height: 44,
                child: OutlinedButton(
                  onPressed: () => ScaffoldMessenger.of(context)
                    ..hideCurrentSnackBar()
                    ..showSnackBar(
                        const SnackBar(content: Text('稍后下载待接入'))),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: palette.accent,
                    side: BorderSide(color: palette.divider),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8)),
                  ),
                  child: const Text('稍后下载', style: TextStyle(fontSize: 14)),
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              flex: 2,
              child: SizedBox(
                height: 44,
                child: FilledButton(
                  onPressed: canDownload ? onDownload : null,
                  style: FilledButton.styleFrom(
                    backgroundColor: palette.accent,
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8)),
                  ),
                  child: Text(
                    isAlbum ? '选择图片下载（${media.images.length} 张）' : '开始下载',
                    style: const TextStyle(fontSize: 15),
                  ),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// 封面：网络图 + 加载中 / 加载失败的占位。
class _Cover extends StatelessWidget {
  const _Cover({required this.url, required this.onTap, this.playable = false});

  final String url;
  final VoidCallback onTap;
  final bool playable;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 92,
        height: 122,
        child: Stack(
          fit: StackFit.expand,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: url.isEmpty
                  ? Container(
                      color: palette.pageBg,
                      alignment: Alignment.center,
                      child: Icon(Icons.movie_outlined,
                          color: palette.chevron, size: 28),
                    )
                  : Image.network(
                      url,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stack) => Container(
                        color: palette.pageBg,
                        alignment: Alignment.center,
                        child: Icon(Icons.broken_image_outlined,
                            color: palette.chevron, size: 24),
                      ),
                      loadingBuilder: (context, child, progress) =>
                          progress == null
                              ? child
                              : Container(
                                  color: palette.pageBg,
                                  alignment: Alignment.center,
                                  child: SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: palette.accent),
                                  ),
                                ),
                    ),
            ),
            // 播放角标：视频可预览；图集不显示
            if (playable)
              Center(
                child: Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.45),
                    shape: BoxShape.circle,
                  ),
                  child:
                      const Icon(Icons.play_arrow, color: Colors.white, size: 20),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
