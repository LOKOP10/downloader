import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/preview/preview.dart';
import 'package:vdl/features/parse/media_info.dart';

/// 图集选择：网格列出全部图片，可逐张勾选、可全选，也可点开预览。
///
/// 下载后存到「下载目录/作品标题/001.jpg、002.jpg …」。
class GalleryPicker extends StatefulWidget {
  const GalleryPicker({super.key, required this.media});

  final MediaInfo media;

  @override
  State<GalleryPicker> createState() => _GalleryPickerState();
}

class _GalleryPickerState extends State<GalleryPicker> {
  late final Set<int> _selected = {
    for (var i = 0; i < widget.media.images.length; i++) i
  };
  bool _busy = false;

  bool get _allSelected => _selected.length == widget.media.images.length;

  void _toggle(int index) {
    setState(() {
      if (_selected.contains(index)) {
        _selected.remove(index);
      } else {
        _selected.add(index);
      }
    });
  }

  void _toggleAll() {
    setState(() {
      if (_allSelected) {
        _selected.clear();
      } else {
        _selected.addAll(
            [for (var i = 0; i < widget.media.images.length; i++) i]);
      }
    });
  }

  Future<void> _download() async {
    if (_selected.isEmpty) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('先选至少一张')));
      return;
    }
    setState(() => _busy = true);
    final queued = await TaskStore.instance.startGallery(
      urls: widget.media.images,
      selected: _selected,
      title: widget.media.title,
      platform: widget.media.platform,
      referer: widget.media.referer,
    );
    if (!mounted) return;
    setState(() => _busy = false);
    Navigator.of(context).pop(queued);
  }

  void _preview(int index) {
    Preview.openImages(context, widget.media.images, initialIndex: index);
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final total = widget.media.images.length;
    return Scaffold(
      appBar: AppTopBar(
        title: '选择图片 · 共 $total 张',
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: () => Navigator.of(context).pop(),
        ),
        actions: [
          TextButton(
            onPressed: _toggleAll,
            child: Text(_allSelected ? '取消全选' : '全选',
                style: TextStyle(color: palette.accent, fontSize: 13)),
          ),
        ],
      ),
      body: GridView.builder(
        padding: const EdgeInsets.fromLTRB(
            AppSize.screenH, AppSize.screenTop, AppSize.screenH, 96),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 3,
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
          childAspectRatio: 0.75,
        ),
        itemCount: total,
        itemBuilder: (context, index) {
          final picked = _selected.contains(index);
          return GestureDetector(
            onTap: () => _toggle(index),
            child: Stack(
              fit: StackFit.expand,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(
                    widget.media.images[index],
                    fit: BoxFit.cover,
                    loadingBuilder: (context, child, progress) => progress == null
                        ? child
                        : Container(
                            color: palette.pageBg,
                            alignment: Alignment.center,
                            child: SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: palette.accent),
                            ),
                          ),
                    errorBuilder: (context, error, stack) => Container(
                      color: palette.pageBg,
                      alignment: Alignment.center,
                      child: Icon(Icons.broken_image_outlined,
                          color: palette.chevron, size: 22),
                    ),
                  ),
                ),
                // 选中态：描边 + 右上角勾
                if (picked)
                  Positioned.fill(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: palette.accent, width: 2),
                        color: palette.accent.withValues(alpha: 0.08),
                      ),
                    ),
                  ),
                Positioned(
                  top: 4,
                  right: 4,
                  child: Container(
                    width: 20,
                    height: 20,
                    decoration: BoxDecoration(
                      color: picked ? palette.accent : Colors.black45,
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white70, width: 1.2),
                    ),
                    child: picked
                        ? const Icon(Icons.check, size: 14, color: Colors.white)
                        : null,
                  ),
                ),
                // 预览按钮
                Positioned(
                  left: 4,
                  bottom: 4,
                  child: GestureDetector(
                    onTap: () => _preview(index),
                    child: Container(
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(
                        color: Colors.black45,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: const Icon(Icons.zoom_out_map,
                          size: 14, color: Colors.white),
                    ),
                  ),
                ),
                Positioned(
                  left: 6,
                  top: 6,
                  child: Text(
                    '${index + 1}',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 11,
                      shadows: [Shadow(blurRadius: 4, color: Colors.black)],
                    ),
                  ),
                ),
              ],
            ),
          );
        },
      ),
      bottomNavigationBar: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: SizedBox(
            height: 46,
            child: FilledButton(
              onPressed: _busy || _selected.isEmpty ? null : _download,
              style: FilledButton.styleFrom(
                backgroundColor: palette.accent,
                foregroundColor: Colors.white,
                shape:
                    RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              ),
              child: Text(
                _busy ? '正在入队…' : '下载选中的 ${_selected.length} 张',
                style: const TextStyle(fontSize: 15),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
