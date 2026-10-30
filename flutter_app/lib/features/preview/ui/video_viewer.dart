import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

/// 视频预览：流式播放远程地址（不落盘）。
///
/// 跨平台复用：只依赖「播放地址 + 站点」。抖音 CDN 会校验 UA / Referer，
/// 所以请求头按站点补；以后 B站 / Iwara 等给出播放地址也能直接用。
class VideoViewer extends StatefulWidget {
  const VideoViewer({
    super.key,
    required this.url,
    this.title = '视频预览',
    this.platform,
    this.referer = '',
  });

  final String url;
  final String title;
  final SitePlatform? platform;

  /// 播放时的 Referer 覆盖（Pornhub 的 CDN 直链只认作品页）。
  final String referer;

  @override
  State<VideoViewer> createState() => _VideoViewerState();
}

class _VideoViewerState extends State<VideoViewer> {
  VideoPlayerController? _controller;
  String? _error;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      final controller = VideoPlayerController.networkUrl(
        Uri.parse(widget.url),
        httpHeaders: AppHttp.headers(
          platform: widget.platform,
          ua: AppHttp.uaMobile,
          extra: widget.referer.isEmpty ? null : {'Referer': widget.referer},
        ),
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      await controller.play();
      setState(() {
        _controller = controller;
        _ready = true;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '播放失败：$error');
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 4, 12, 4),
              child: Row(
                children: [
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close, color: Colors.white),
                    tooltip: '关闭',
                  ),
                  Expanded(
                    child: Text(
                      widget.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white70, fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: Center(
                child: _error != null
                    ? Padding(
                        padding: const EdgeInsets.all(24),
                        child: Text(
                          _error!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.white70, fontSize: 13),
                        ),
                      )
                    : (_ready && controller != null
                        ? AspectRatio(
                            aspectRatio: controller.value.aspectRatio,
                            child: Stack(
                              alignment: Alignment.center,
                              children: [
                                VideoPlayer(controller),
                                // 双击暂停/继续；单击不打断播放（避免误触）
                                Positioned.fill(
                                  child: GestureDetector(
                                    behavior: HitTestBehavior.opaque,
                                    onDoubleTap: _togglePlay,
                                    child: Container(color: Colors.transparent),
                                  ),
                                ),
                              ],
                            ),
                          )
                        : const SizedBox(
                            width: 28,
                            height: 28,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white70),
                          )),
              ),
            ),
            if (_ready && controller != null) _buildControls(controller),
          ],
        ),
      ),
    );
  }

  Widget _buildControls(VideoPlayerController controller) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
      child: Row(
        children: [
          IconButton(
            onPressed: _togglePlay,
            icon: Icon(
              controller.value.isPlaying ? Icons.pause : Icons.play_arrow,
              color: Colors.white,
            ),
          ),
          Expanded(
            child: VideoProgressIndicator(
              controller,
              allowScrubbing: true,
              colors: const VideoProgressColors(
                playedColor: Colors.white,
                bufferedColor: Colors.white30,
                backgroundColor: Colors.white12,
              ),
            ),
          ),
          const SizedBox(width: 8),
          ValueListenableBuilder<VideoPlayerValue>(
            valueListenable: controller,
            builder: (context, value, _) => Text(
              '${_fmt(value.position)} / ${_fmt(value.duration)}',
              style: const TextStyle(color: Colors.white70, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  void _togglePlay() {
    final controller = _controller;
    if (controller == null) return;
    if (controller.value.isPlaying) {
      controller.pause();
    } else {
      controller.play();
    }
    setState(() {});
  }

  static String _fmt(Duration d) {
    final total = d.inSeconds;
    final m = (total ~/ 60).toString().padLeft(2, '0');
    final s = (total % 60).toString().padLeft(2, '0');
    return '$m:$s';
  }
}
