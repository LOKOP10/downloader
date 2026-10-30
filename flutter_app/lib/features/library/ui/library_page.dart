import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/features/manga/data/manga_book.dart';
import 'package:vdl/features/manga/data/manga_library.dart';
import 'package:vdl/features/manga/data/manga_location.dart';
import 'package:vdl/features/manga/ui/manga_reader.dart';
import 'package:vdl/features/settings/ui/widgets/save_location_dialog.dart';

/// 书架页：扫描「漫画保存位置」里的 .zip 与图片文件夹，点开即可离线阅读。
class LibraryPage extends StatefulWidget {
  const LibraryPage({super.key});

  @override
  State<LibraryPage> createState() => _LibraryPageState();
}

class _LibraryPageState extends State<LibraryPage> {
  bool _searching = false;
  final _query = TextEditingController();
  MangaSort _sort = MangaSort.recent;
  bool _loaded = false;
  String _dir = '';

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final dir = await MangaLocation.current();
      await MangaLibrary.instance.load();
      if (!mounted) return;
      setState(() {
        _loaded = true;
        _dir = dir;
      });
    } catch (error) {
      // 书架加载失败不该把异常抛到界面层（否则整个页面报红）
      debugPrint('书架加载失败：' + error.toString());
      if (!mounted) return;
      setState(() => _loaded = true);
    }
  }

  void _toast(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _open(MangaBook book) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => MangaReaderPage(book: book),
    ));
    if (mounted) setState(() {});
  }

  /// 清理「这一本」的内存页面缓存（大本子占内存，看完可以手动放掉）。
  ///
  /// 只清内存里的解压结果，不动磁盘上的 zip/图片：下次打开会重新解包。
  void _clearCache(MangaBook book) {
    if (!MangaLibrary.hasPageCache(book.path)) {
      _toast('这本还没加载过，本来就没缓存');
      return;
    }
    MangaLibrary.invalidatePages(book.path);
    _toast('已清理《' + book.title + '》的页面缓存');
  }

  /// 移除：先问「只移出书架 / 连文件一起删」，删文件还要再确认一次。
  Future<void> _remove(MangaBook book) async {
    final palette = AppPalette.of(context);
    final choice = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: palette.surface,
        title: Text('移除《' + book.title + '》',
            style: TextStyle(color: palette.titleText, fontSize: 15)),
        content: Text(
          '移除后这本书不会再出现在书架里。\n'
          '「仅移除书架」不会动磁盘上的文件，以后还能从漫画目录找回；\n'
          '「移除并删文件」是永久删除，无法恢复。',
          style: TextStyle(color: palette.hint, fontSize: 12, height: 1.5),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop('cancel'),
            child: Text('取消', style: TextStyle(color: palette.labelText)),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop('delete'),
            child: Text('移除并删文件', style: TextStyle(color: palette.danger)),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop('shelf'),
            style: FilledButton.styleFrom(
              backgroundColor: palette.accent,
              foregroundColor: Colors.white,
            ),
            child: const Text('仅移除书架'),
          ),
        ],
      ),
    );
    if (choice == null || choice == 'cancel') return;
    if (choice == 'delete') {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          backgroundColor: palette.surface,
          title: Text('确认删除文件？',
              style: TextStyle(color: palette.titleText, fontSize: 15)),
          content: Text(
            '将永久删除这本书在磁盘上的文件，无法恢复。\n'
            '漫画目录里的其它内容不会被删除。',
            style: TextStyle(color: palette.hint, fontSize: 12, height: 1.5),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text('返回', style: TextStyle(color: palette.labelText)),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              style: FilledButton.styleFrom(
                backgroundColor: palette.danger,
                foregroundColor: Colors.white,
              ),
              child: const Text('永久删除'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    final message = await MangaLibrary.instance
        .remove(book, deleteFile: choice == 'delete');
    if (!mounted) return;
    setState(() {});
    _toast(message);
  }

  Future<void> _pickSort() async {
    final palette = AppPalette.of(context);
    final picked = await showModalBottomSheet<MangaSort>(
      context: context,
      backgroundColor: palette.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(14)),
      ),
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Text('排序方式',
                  style: TextStyle(
                      color: palette.titleText,
                      fontSize: 15,
                      fontWeight: FontWeight.w600)),
            ),
            for (final option in MangaSort.values)
              ListTile(
                title: Text(option.label,
                    style: TextStyle(color: palette.titleText, fontSize: 14)),
                trailing: option == _sort
                    ? Icon(Icons.check, color: palette.accent, size: 20)
                    : null,
                onTap: () => Navigator.of(sheetContext).pop(option),
              ),
          ],
        ),
      ),
    );
    if (picked != null && mounted) setState(() => _sort = picked);
  }

  Future<void> _editLocation() async {
    final current = await MangaLocation.current();
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (_) => SaveLocationDialog(
        title: '漫画保存位置',
        initialPath: current,
        // 这里必须写 `manga.dir`：写死 DownloadLocation.save 会改到视频目录
        save: MangaLocation.save,
        defaultPath: MangaLocation.defaultDir,
      ),
    );
    if (!mounted) return;
    await _refresh();
    _toast('漫画保存位置：' + await MangaLocation.current());
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      appBar: AppTopBar(
        title: '本地漫画',
        actions: [
          IconButton(
            tooltip: '搜索',
            icon: Icon(_searching ? Icons.close : Icons.search,
                color: palette.labelText),
            onPressed: () => setState(() {
              _searching = !_searching;
              if (!_searching) _query.clear();
            }),
          ),
          IconButton(
            tooltip: '排序',
            icon: Icon(Icons.sort, color: palette.labelText),
            onPressed: _pickSort,
          ),
          IconButton(
            tooltip: '漫画保存位置',
            icon: Icon(Icons.folder_outlined, color: palette.labelText),
            onPressed: _editLocation,
          ),
        ],
      ),
      // 监听书架单例：下载完成后（JmDownloader 里 upsert）列表会立刻多出这一本，
      // 不用手动下拉刷新，也不用重启应用。
      body: ListenableBuilder(
        listenable: MangaLibrary.instance,
        builder: (context, _) => _body(palette),
      ),
    );
  }

  Widget _body(AppPalette palette) {
    final keyword = _query.text.trim();
    final books = MangaLibrary.instance.sorted(_sort, keyword);
    return Column(
      children: [
        if (_searching)
          Padding(
            padding: const EdgeInsets.fromLTRB(
                AppSize.screenH, AppSize.screenTop, AppSize.screenH, 0),
            child: AppCard(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              child: TextField(
                controller: _query,
                autofocus: true,
                onChanged: (_) => setState(() {}),
                style: TextStyle(color: palette.titleText, fontSize: 14),
                decoration: InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                  contentPadding: EdgeInsets.zero,
                  hintText: '搜索书名',
                  hintStyle: TextStyle(color: palette.hint, fontSize: 14),
                ),
              ),
            ),
          ),
        Expanded(
          child: books.isEmpty
              ? _empty(palette)
              : RefreshIndicator(
                  onRefresh: _refresh,
                  child: GridView.builder(
                    padding: const EdgeInsets.fromLTRB(AppSize.screenH,
                        AppSize.screenTop, AppSize.screenH, 24),
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 2,
                      mainAxisSpacing: 12,
                      crossAxisSpacing: 12,
                      childAspectRatio: 0.62,
                    ),
                    itemCount: books.length,
                    itemBuilder: (context, i) => _BookCell(
                      // ⚠️ 必须带 key：没有 key 时 GridView 会把同一个 State 复用到
                      // 另一本书上，而封面只在 initState 加载 → 两本书显示同一张封面
                      key: ValueKey<String>(books[i].path),
                      book: books[i],
                      onTap: () => _open(books[i]),
                      onRemove: () => _remove(books[i]),
                      onClearCache: () => _clearCache(books[i]),
                    ),
                  ),
                ),
        ),
      ],
    );
  }

  Widget _empty(AppPalette palette) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.collections_bookmark_outlined,
                color: palette.chevron, size: 40),
            const SizedBox(height: 10),
            Text(_loaded ? '还没有本地漫画' : '正在扫描…',
                style: TextStyle(color: palette.hint, fontSize: 13)),
            const SizedBox(height: 4),
            Text('从首页粘贴禁漫链接下载，或把 .zip / 图片文件夹放进漫画目录',
                style: TextStyle(color: palette.tabInactive, fontSize: 12)),
            if (_dir.isNotEmpty) ...[
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 28),
                child: Text(
                  _dir,
                  textAlign: TextAlign.center,
                  style: TextStyle(color: palette.tabInactive, fontSize: 10.5),
                ),
              ),
            ],
          ],
        ),
      );
}

class _BookCell extends StatefulWidget {
  const _BookCell({
    super.key,
    required this.book,
    required this.onTap,
    required this.onRemove,
    required this.onClearCache,
  });

  final MangaBook book;
  final VoidCallback onTap;
  final VoidCallback onRemove;
  final VoidCallback onClearCache;

  @override
  State<_BookCell> createState() => _BookCellState();
}

class _BookCellState extends State<_BookCell> {
  Uint8List? _cover;

  @override
  void initState() {
    super.initState();
    _loadCover();
  }

  @override
  void didUpdateWidget(covariant _BookCell oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 兜底：即使外层没给 key，换了书也要把封面重载，绝不沿用上一本的图
    if (oldWidget.book.path != widget.book.path) {
      setState(() => _cover = null);
      _loadCover();
    }
  }

  Future<void> _loadCover() async {
    final bytes = await MangaLibrary.instance.cover(widget.book);
    if (!mounted || bytes == null) return;
    setState(() => _cover = bytes);
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final book = widget.book;
    final percent = (book.progress * 100).round();
    return GestureDetector(
      onTap: widget.onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: _cover == null
                      ? Container(
                          decoration: BoxDecoration(
                            color: palette.pageBg,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: palette.divider),
                          ),
                          alignment: Alignment.center,
                          child: Icon(
                            book.isZip
                                ? Icons.folder_zip_outlined
                                : Icons.folder_outlined,
                            color: palette.chevron,
                            size: 34,
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
                                color: palette.chevron, size: 24),
                          ),
                        ),
                ),
                Positioned(
                  right: 4,
                  top: 4,
                  child: Row(
                    children: [
                      // 单本「清理缓存」键：大本子解压后很占内存，看完可以手动放掉
                      GestureDetector(
                        onTap: widget.onClearCache,
                        child: Container(
                          margin: const EdgeInsets.only(right: 4),
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(
                            color: Colors.black45,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: const Icon(Icons.cleaning_services_outlined,
                              size: 16, color: Colors.white),
                        ),
                      ),
                      GestureDetector(
                        onTap: widget.onRemove,
                        child: Container(
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(
                            color: Colors.black45,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: const Icon(Icons.delete_outline,
                              size: 16, color: Colors.white),
                        ),
                      ),
                    ],
                  ),
                ),
                if (book.pages > 0)
                  Positioned(
                    left: 6,
                    bottom: 6,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: Colors.black54,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(percent.toString() + '%',
                          style: const TextStyle(
                              color: Colors.white, fontSize: 10)),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          // 标题固定占两行的高度：标题 1 行 / 2 行的卡片，封面区高度才会一致。
          // 以前让它跟着文字行数走，同一行两张封面一高一低（真机上看起来就是「没对齐」）。
          SizedBox(
            height: 33, // 12.5sp × 行高 1.3 × 2 行
            width: double.infinity,
            child: Text(
              book.title,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  color: palette.titleText, fontSize: 12.5, height: 1.3),
            ),
          ),
          const SizedBox(height: 2),
          SizedBox(
            height: 16,
            width: double.infinity,
            child: Text(
              book.pages > 0
                  ? book.pages.toString() + ' 页'
                  : '页数待统计（打开后自动识别）',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: palette.hint, fontSize: 11),
            ),
          ),
        ],
      ),
    );
  }
}
