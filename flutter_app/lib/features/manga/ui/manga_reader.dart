import 'dart:async';

import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/manga/data/manga_book.dart';
import 'package:vdl/features/manga/data/manga_library.dart';
import 'package:vdl/shared/media_saver.dart';

/// 本地漫画阅读器：左到右翻页，翻页即存进度。
///
/// 两处细节都是真机踩出来的：
///  · **打开就停在正确的页**：以前用 `jumpToPage` + 首帧回调，PageView 还没挂上
///    控制器时会被静默跳过；现在直接用 `PageController(initialPage: …)` 重建；
///  · **进度防抖**：每翻一页都写一次书架 JSON 太重，改成停止翻页 400ms 后再写，
///    退出阅读器时立刻补写一次，保证「读到哪下次就回到哪」。
///  · **底部「保存到相册」**：把当前这一页写进系统相册（MediaStore 登记，
///    图库与系统搜索都能看到），用来单独收藏/分享某一页，不需要整本导出。
class MangaReaderPage extends StatefulWidget {
  const MangaReaderPage({super.key, required this.book});

  final MangaBook book;

  @override
  State<MangaReaderPage> createState() => _MangaReaderPageState();
}

class _MangaReaderPageState extends State<MangaReaderPage> {
  PageController? _controller;
  List<MangaPageData>? _pages;
  String? _error;
  int _index = 0;
  int _lastSavedPage = -1;
  Timer? _saveDebounce;

  /// 正在保存到相册（防连点重复插入）。
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _saveDebounce?.cancel();
    // 退出时把最后停留的页补写一次：防抖还没触发就退出的话，不补写就等于没记住
    final pages = _pages;
    if (pages != null && _index != _lastSavedPage) {
      _lastSavedPage = _index;
      MangaLibrary.instance.markRead(widget.book, _index, pages.length);
    }
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final pages = await MangaLibrary.instance.openPages(widget.book);
      if (!mounted) return;
      if (pages.isEmpty) {
        setState(() => _error = '这本书里没有可显示的图片');
        return;
      }
      final start = widget.book.lastPage.clamp(0, pages.length - 1);
      setState(() {
        _pages = pages;
        _index = start;
        _lastSavedPage = start;
        // 直接以「上次读到的页」为初始页，不依赖首帧跳转
        _controller?.dispose();
        _controller = PageController(initialPage: start);
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '打开失败：' + error.toString());
    }
  }

  void _onPageChanged(int index) {
    if (index == _index) return;
    setState(() => _index = index);
    final pages = _pages;
    if (pages == null || index == _lastSavedPage) return;
    // 连续翻页时只在停下来的那一页写盘（400ms 内继续翻就重置计时）
    _saveDebounce?.cancel();
    _saveDebounce = Timer(const Duration(milliseconds: 400), () {
      _lastSavedPage = index;
      MangaLibrary.instance.markRead(widget.book, index, pages.length);
    });
  }

  /// 把当前页保存到系统相册。
  Future<void> _saveToGallery(int index) async {
    final pages = _pages;
    if (pages == null || index < 0 || index >= pages.length) return;
    if (_saving) return;
    setState(() => _saving = true);
    final page = pages[index];
    try {
      final name = savedPageFileName(widget.book.title, index, page.name);
      final saved = await MediaSaver.saveImageToGallery(
        bytes: page.bytes,
        displayName: name,
      );
      if (!mounted) return;
      _toast('已保存到相册：' + saved);
    } catch (error) {
      if (!mounted) return;
      _toast('保存失败：' + error.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _toast(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final pages = _pages;
    final controller = _controller;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppTopBar(
        title: widget.book.title,
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: () => Navigator.of(context).pop(),
        ),
        actions: [
          Center(
            child: Text(
              pages == null
                  ? ''
                  : (_index + 1).toString() + ' / ' + pages.length.toString(),
              style: TextStyle(color: palette.labelText, fontSize: 13),
            ),
          ),
          const SizedBox(width: 12),
        ],
      ),
      // 底部「保存到相册」：只存当前这一页，不整本导出
      bottomNavigationBar: pages == null
          ? null
          : SafeArea(
              top: false,
              child: Container(
                color: Colors.black,
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: SizedBox(
                  height: 44,
                  child: FilledButton.icon(
                    onPressed: _saving ? null : () => _saveToGallery(_index),
                    icon: const Icon(Icons.save_alt, size: 18),
                    label: Text(
                      _saving ? '保存中…' : '保存到相册',
                      style: const TextStyle(fontSize: 15),
                    ),
                    style: FilledButton.styleFrom(
                      backgroundColor: palette.accent,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                  ),
                ),
              ),
            ),
      body: _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(_error!, style: TextStyle(color: palette.danger)),
              ),
            )
          : pages == null || controller == null
              ? const Center(child: CircularProgressIndicator())
              : PageView.builder(
                  controller: controller,
                  itemCount: pages.length,
                  onPageChanged: _onPageChanged,
                  itemBuilder: (context, index) => InteractiveViewer(
                    minScale: 1,
                    maxScale: 4,
                    child: Center(
                      child: Image.memory(
                        pages[index].bytes,
                        fit: BoxFit.contain,
                        gaplessPlayback: true,
                        errorBuilder: (context, error, stack) => Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(
                            '这一页打不开（' + pages[index].name + '）',
                            style: TextStyle(color: palette.danger, fontSize: 12),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
    );
  }
}

/// 长按存图的文件名：`<书名>_001.<原扩展名>`。
///
/// 与视频保存路径的命名习惯一致（`TaskStore.safeName` 清掉非法字符），
/// 序号从 1 开始补到 3 位，扩展名尽量沿用原页（.webp 就存 .webp，不假装成 .jpg）。
String savedPageFileName(String title, int index, String pageName) {
  final base = TaskStore.safeName(title);
  final seq = (index + 1).toString().padLeft(3, '0');
  return base + '_' + seq + pageExtOf(pageName);
}

/// 取页面文件的扩展名（不认识就按 .jpg 兜底）。
String pageExtOf(String pageName) {
  final lower = pageName.toLowerCase();
  for (final ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']) {
    if (lower.endsWith(ext)) return ext;
  }
  return '.jpg';
}
