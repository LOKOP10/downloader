import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/status_pill.dart';
import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_downloader.dart';
import 'package:vdl/features/manga/ui/jm_preview_page.dart';

/// 禁漫解析结果卡：标题 / 章节 / 页数 + 预览 + 下载到书架。
class JmPanel extends StatefulWidget {
  const JmPanel({super.key, required this.album, this.onOpenLibrary});

  final JmAlbum album;
  final VoidCallback? onOpenLibrary;

  @override
  State<JmPanel> createState() => _JmPanelState();
}

class _JmPanelState extends State<JmPanel> {
  bool _busy = false;
  String? _error;
  bool _done = false;
  Uint8List? _cover;

  @override
  void initState() {
    super.initState();
    _loadCover();
  }

  @override
  void didUpdateWidget(covariant JmPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 换了一本本子：复用同一个 State，必须手动重置封面与状态
    if (oldWidget.album.id != widget.album.id) {
      setState(() {
        _cover = null;
        _done = false;
        _error = null;
        _busy = false;
      });
      _loadCover();
    }
  }

  /// 封面：禁漫封面域名的证书是自签名的，Image.network 会失败，
  /// 所以用同一个放行过证书的客户端把字节拉回来。
  Future<void> _loadCover() async {
    final bytes = await JmClient.instance.fetchCover(widget.album.id);
    if (!mounted) return;
    setState(() => _cover = bytes);
  }

  Future<void> _download() async {
    setState(() {
      _busy = true;
      _error = null;
      _done = false;
    });
    // 进度一律看「任务」页，首页不再重复显示
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(const SnackBar(content: Text('已开始下载，进度见「任务」页')));
    try {
      await JmDownloader.instance.downloadAlbum(widget.album);
      if (!mounted) return;
      setState(() {
        _busy = false;
        _done = true;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.toString();
      });
    }
  }

  Future<void> _preview() async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => JmPreviewPage(album: widget.album),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final album = widget.album;
    return Column(
      children: [
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: SizedBox(
                      width: 64,
                      height: 88,
                      child: _cover == null
                          ? GestureDetector(
                              onTap: _loadCover,
                              child: Container(
                                decoration: BoxDecoration(
                                  color: palette.pageBg,
                                  borderRadius: BorderRadius.circular(8),
                                  border: Border.all(color: palette.divider),
                                ),
                                alignment: Alignment.center,
                                child: Icon(Icons.menu_book_outlined,
                                    color: palette.chevron, size: 26),
                              ),
                            )
                          : Image.memory(
                              _cover!,
                              fit: BoxFit.cover,
                              gaplessPlayback: true,
                              errorBuilder: (context, error, stack) => Container(
                                color: palette.pageBg,
                                alignment: Alignment.center,
                                child: Icon(Icons.broken_image_outlined,
                                    color: palette.chevron, size: 22),
                              ),
                            ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          album.name.isEmpty ? 'JM' + album.id : album.name,
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
                          [
                            if (album.author.isNotEmpty) album.author,
                            'JM' + album.id,
                            '禁漫',
                          ].join(' · '),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(color: palette.hint, fontSize: 12),
                        ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: [
                            StatusPill(album.pageText, color: palette.warning),
                            if (album.multiChapter)
                              StatusPill('多章本子', color: palette.accent),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!,
                    style: TextStyle(
                        color: palette.danger, fontSize: 12, height: 1.4)),
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
                  onPressed: _busy ? null : _preview,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: palette.accent,
                    side: BorderSide(color: palette.divider),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8)),
                  ),
                  child: const Text('预览', style: TextStyle(fontSize: 14)),
                ),
              ),
            ),
            const SizedBox(width: 8),
            if (_done)
              Expanded(
                child: SizedBox(
                  height: 44,
                  child: OutlinedButton(
                    onPressed: widget.onOpenLibrary,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: palette.accent,
                      side: BorderSide(color: palette.divider),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                    child: const Text('去书架', style: TextStyle(fontSize: 14)),
                  ),
                ),
              ),
            if (_done) const SizedBox(width: 8),
            Expanded(
              flex: 2,
              child: SizedBox(
                height: 44,
                child: FilledButton(
                  onPressed: _busy ? null : _download,
                  style: FilledButton.styleFrom(
                    backgroundColor: palette.accent,
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8)),
                  ),
                  child: Text(
                    _busy ? '下载中（见任务页）' : (_done ? '再下一份' : '下载到书架'),
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
