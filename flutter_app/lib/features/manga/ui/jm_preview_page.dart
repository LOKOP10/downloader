import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_downloader.dart';

/// 禁漫预览：整本连续下滑（和 JM 网站一样一条长条往下翻），滚到哪加载到哪。
///
/// 以前只抓前 5 页、左右翻页 —— 用户根本判断不了这本值不值得下。现在：
///  · 页清单与「下载」共用 JmDownloader.allPages，页数一定和最终压缩包一致；
///  · 懒加载：只下当前视口附近的页（3 路并发），已看过的进 LRU 缓存（24 页）；
///  · 点任意一页进入单页全屏，可双指缩放。
class JmPreviewPage extends StatefulWidget {
  const JmPreviewPage({super.key, required this.album});

  final JmAlbum album;

  @override
  State<JmPreviewPage> createState() => _JmPreviewPageState();
}

class _JmPreviewPageState extends State<JmPreviewPage> {
  /// 同时下载几页（别把 CDN 打爆）。
  static const _maxParallel = 3;

  /// 内存里最多留几页（一页约 150~500KB，24 页约 10MB 以内）。
  static const _cacheMax = 24;

  final ScrollController _scroll = ScrollController();
  List<JmPageRef>? _pages;
  String? _error;

  final Map<int, Uint8List> _cache = <int, Uint8List>{};
  final Set<int> _loading = <int>{};
  final Set<int> _failed = <int>{};
  final List<int> _queue = <int>[];
  int _active = 0;

  /// 最近构建到的页（用于顶部页码，ListView 只会构建视口附近）
  int _lastBuilt = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final pages = await JmDownloader.instance.allPages(widget.album);
      if (!mounted) return;
      setState(() {
        _pages = pages;
        if (pages.isEmpty) _error = '这本没有取到任何页（可能章节被限制）';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error.toString());
    }
  }

  /// 安排加载某一页（超过并发就先排队）。
  void _ensure(int index) {
    final pages = _pages;
    if (pages == null || index < 0 || index >= pages.length) return;
    if (_cache.containsKey(index) ||
        _loading.contains(index) ||
        _failed.contains(index)) {
      return;
    }
    if (_active >= _maxParallel) {
      if (!_queue.contains(index)) _queue.add(index);
      return;
    }
    _startLoad(index);
  }

  Future<void> _startLoad(int index) async {
    final pages = _pages;
    if (pages == null) return;
    _loading.add(index);
    _active++;
    try {
      final bytes =
          await JmDownloader.instance.pageBytes(pages[index], index: index);
      if (!mounted) return;
      setState(() {
        _cache[index] = bytes;
        _evict();
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _failed.add(index));
    } finally {
      _loading.remove(index);
      _active--;
      _pump();
    }
  }

  void _pump() {
    while (_active < _maxParallel && _queue.isNotEmpty) {
      final next = _queue.removeAt(0);
      _ensure(next);
    }
  }

  /// 淘汰离当前页最远的缓存（来回翻不用重下）。
  void _evict() {
    if (_cache.length <= _cacheMax) return;
    final keys = _cache.keys.toList()
      ..sort((a, b) =>
          (b - _lastBuilt).abs().compareTo((a - _lastBuilt).abs()));
    while (_cache.length > _cacheMax && keys.isNotEmpty) {
      _cache.remove(keys.removeAt(0));
    }
  }

  void _retry(int index) {
    setState(() => _failed.remove(index));
    _ensure(index);
  }

  void _openFullscreen(int index) {
    final pages = _pages;
    if (pages == null) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => _SinglePageView(
        title: widget.album.name.isEmpty
            ? 'JM' + widget.album.id
            : widget.album.name,
        index: index,
        total: pages.length,
        bytes: _cache[index],
        loader: () =>
            JmDownloader.instance.pageBytes(pages[index], index: index),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final pages = _pages;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppTopBar(
        title: widget.album.name.isEmpty
            ? 'JM' + widget.album.id
            : widget.album.name,
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: () => Navigator.of(context).pop(),
        ),
        actions: [
          Center(
            child: Text(
              pages == null
                  ? ''
                  : (_lastBuilt + 1).toString() +
                      ' / ' +
                      pages.length.toString(),
              style: TextStyle(color: palette.labelText, fontSize: 13),
            ),
          ),
          const SizedBox(width: 12),
        ],
      ),
      body: _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(_error!,
                    style: TextStyle(color: palette.danger, fontSize: 13)),
              ),
            )
          : pages == null
              ? const Center(child: CircularProgressIndicator())
              : ListView.builder(
                  controller: _scroll,
                  // 预取前后一屏多一点，滑起来不会一片空白
                  cacheExtent: 2400,
                  itemCount: pages.length,
                  itemBuilder: (context, index) {
                    if (index > _lastBuilt) _lastBuilt = index;
                    _ensure(index);
                    return _buildPage(context, index, palette);
                  },
                ),
    );
  }

  Widget _buildPage(BuildContext context, int index, AppPalette palette) {
    final bytes = _cache[index];
    if (bytes != null) {
      return GestureDetector(
        onTap: () => _openFullscreen(index),
        child: Image.memory(
          bytes,
          fit: BoxFit.fitWidth,
          width: double.infinity,
          gaplessPlayback: true,
        ),
      );
    }
    if (_failed.contains(index)) {
      return SizedBox(
        height: 220,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('第 ' + (index + 1).toString() + ' 页加载失败',
                  style: TextStyle(color: palette.danger, fontSize: 13)),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: () => _retry(index),
                child: const Text('重试'),
              ),
            ],
          ),
        ),
      );
    }
    return SizedBox(
      height: 220,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            const SizedBox(height: 8),
            Text('第 ' + (index + 1).toString() + ' 页加载中…',
                style: const TextStyle(color: Colors.white54, fontSize: 12)),
          ],
        ),
      ),
    );
  }
}

/// 单页全屏：双指缩放 / 拖动（预览里点一下某页进来）。
class _SinglePageView extends StatefulWidget {
  const _SinglePageView({
    required this.title,
    required this.index,
    required this.total,
    required this.bytes,
    required this.loader,
  });

  final String title;
  final int index;
  final int total;
  final Uint8List? bytes;
  final Future<Uint8List> Function() loader;

  @override
  State<_SinglePageView> createState() => _SinglePageViewState();
}

class _SinglePageViewState extends State<_SinglePageView> {
  Uint8List? _bytes;
  String? _error;

  @override
  void initState() {
    super.initState();
    _bytes = widget.bytes;
    if (_bytes == null) _load();
  }

  Future<void> _load() async {
    try {
      final bytes = await widget.loader();
      if (!mounted) return;
      setState(() => _bytes = bytes);
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppTopBar(
        title: widget.title,
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: () => Navigator.of(context).pop(),
        ),
        actions: [
          Center(
            child: Text(
              (widget.index + 1).toString() + ' / ' + widget.total.toString(),
              style: TextStyle(color: palette.labelText, fontSize: 13),
            ),
          ),
          const SizedBox(width: 12),
        ],
      ),
      body: _error != null
          ? Center(
              child: Text(_error!,
                  style: TextStyle(color: palette.danger, fontSize: 13)),
            )
          : _bytes == null
              ? const Center(child: CircularProgressIndicator())
              : InteractiveViewer(
                  minScale: 1,
                  maxScale: 5,
                  child: Center(
                    child: Image.memory(_bytes!, fit: BoxFit.contain),
                  ),
                ),
    );
  }
}
