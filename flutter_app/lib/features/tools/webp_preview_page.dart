import 'dart:io';

import 'package:flutter/material.dart';
import 'package:vdl/features/tools/webp_converter.dart';

/// 全屏动图预览：左右滑动切换上一个/下一个，双指或双击放大查看细节。
/// 用黑色背景，避免白底把浅色动图的边缘糊掉。
class WebpPreviewPage extends StatefulWidget {
  const WebpPreviewPage({
    super.key,
    required this.sources,
    required this.initialIndex,
  });

  final List<WebpSourceInfo> sources;
  final int initialIndex;

  @override
  State<WebpPreviewPage> createState() => _WebpPreviewPageState();
}

class _WebpPreviewPageState extends State<WebpPreviewPage> {
  late final PageController _controller;
  late int _index;

  @override
  void initState() {
    super.initState();
    final last = widget.sources.length - 1;
    _index = widget.initialIndex < 0
        ? 0
        : (widget.initialIndex > last ? last : widget.initialIndex);
    _controller = PageController(initialPage: _index);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final source = widget.sources[_index];
    // 放大后要清晰，解码上限按屏幕物理宽度给，避免原图过大占内存。
    final cacheWidth =
        (media.size.width * media.devicePixelRatio).round().clamp(1, 2400);
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        foregroundColor: Colors.white,
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: () => Navigator.of(context).pop(),
        ),
        titleSpacing: 0,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            Text(
              '${_index + 1} / ${widget.sources.length}',
              style: const TextStyle(fontSize: 15, color: Colors.white),
            ),
            Text(
              source.fileName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 11, color: Colors.white70),
            ),
          ],
        ),
      ),
      body: Column(
        children: <Widget>[
          Expanded(
            child: PageView.builder(
              controller: _controller,
              itemCount: widget.sources.length,
              onPageChanged: (int i) => setState(() => _index = i),
              itemBuilder: (BuildContext context, int i) {
                final item = widget.sources[i];
                return _ZoomableWebp(
                  key: ValueKey<String>(item.uid),
                  path: item.path,
                  cacheWidth: cacheWidth,
                );
              },
            ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
              child: Column(
                children: <Widget>[
                  Text(
                    '${source.width}×${source.height} · ${source.frameCount} 帧 · '
                    '${source.durationEstimated ? '约 ' : ''}'
                    '${(source.durationMs / 1000).toStringAsFixed(1)} 秒',
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    widget.sources.length > 1
                        ? '左右滑动切换 · 双指或双击放大'
                        : '双指或双击放大',
                    style: const TextStyle(color: Colors.white54, fontSize: 11),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 单张可缩放动图。未放大时把 panEnabled 关掉，横滑手势才能交给外层 PageView 翻页。
class _ZoomableWebp extends StatefulWidget {
  const _ZoomableWebp({
    super.key,
    required this.path,
    required this.cacheWidth,
  });

  final String path;
  final int cacheWidth;

  @override
  State<_ZoomableWebp> createState() => _ZoomableWebpState();
}

class _ZoomableWebpState extends State<_ZoomableWebp> {
  final TransformationController _transform = TransformationController();
  bool _zoomed = false;

  @override
  void initState() {
    super.initState();
    _transform.addListener(_onTransformChanged);
  }

  @override
  void dispose() {
    _transform.removeListener(_onTransformChanged);
    _transform.dispose();
    super.dispose();
  }

  void _onTransformChanged() {
    final zoomed = _transform.value.getMaxScaleOnAxis() > 1.01;
    if (zoomed != _zoomed) {
      setState(() => _zoomed = zoomed);
    }
  }

  void _toggleDoubleTapZoom() {
    _transform.value = _zoomed
        ? Matrix4.identity()
        : Matrix4.diagonal3Values(2.5, 2.5, 1.0);
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onDoubleTap: _toggleDoubleTapZoom,
      child: InteractiveViewer(
        transformationController: _transform,
        minScale: 1,
        maxScale: 5,
        panEnabled: _zoomed,
        scaleEnabled: true,
        child: Center(
          child: Image.file(
            File(widget.path),
            cacheWidth: widget.cacheWidth,
            fit: BoxFit.contain,
            gaplessPlayback: true,
            errorBuilder: (BuildContext context, Object error, StackTrace? stack) {
              return const Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  '无法读取该文件',
                  style: TextStyle(color: Colors.white70, fontSize: 13),
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}
