import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/app_list_tile.dart';
import 'package:vdl/features/download/data/download_location.dart';
import 'package:vdl/features/tools/webp_converter.dart';
import 'package:vdl/shared/media_saver.dart';

/// WebP 转 MP4：选一个或多个 WebP（含动图），逐帧转成 mp4 并登记到媒体库。
class Webp2Mp4Page extends StatefulWidget {
  const Webp2Mp4Page({super.key});

  @override
  State<Webp2Mp4Page> createState() => _Webp2Mp4PageState();
}

class _Webp2Mp4PageState extends State<Webp2Mp4Page> {
  final List<WebpSourceInfo> _sources = <WebpSourceInfo>[];
  bool _busy = false;
  bool _cancelRequested = false;
  int _done = 0;
  int _total = 0;
  String _stage = '';
  String _pendingOutput = '';
  String? _resultPath;
  String? _error;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      backgroundColor: palette.pageBg,
      appBar: AppTopBar(
        title: 'WebP 转 MP4',
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: _busy ? null : () => Navigator.of(context).pop(),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
            AppSize.screenH, AppSize.screenTop, AppSize.screenH, 32),
        children: [
          const AppSectionLabel('输入'),
          AppCardGroup(children: [
            AppListTile(
              icon: Icons.add_photo_alternate_outlined,
              title: '选择 WebP 文件',
              subtitle: _sources.isEmpty
                  ? '支持动图，可多选；多张按选择顺序拼成一段视频'
                  : '已选 ${_sources.length} 个，共 ${_totalInputFrames()} 帧',
              trailingText: _busy ? '转换中' : '选择',
              onTap: _busy ? null : _pickFiles,
            ),
            for (final source in _sources)
              AppListTile(
                icon: source.isAnimated ? Icons.gif_box_outlined : Icons.image_outlined,
                title: source.fileName,
                subtitle: '${source.width}×${source.height} · ${source.frameCount} 帧 · '
                    '${(source.durationMs / 1000).toStringAsFixed(1)} 秒',
                trailingText: _busy ? null : '移除',
                onTap: _busy ? null : () => _remove(source),
              ),
          ]),
          if (_sources.isNotEmpty) ...[
            const AppSectionLabel('输出'),
            AppCardGroup(children: [
              AppListTile(
                icon: Icons.movie_outlined,
                title: '保存为',
                subtitle: _pendingOutput.isEmpty ? '读取保存目录…' : _pendingOutput,
              ),
            ]),
          ],
          if (_error != null) ...[
            const SizedBox(height: AppSize.cardGap),
            _MessageCard(
              icon: Icons.error_outline,
              color: palette.danger,
              title: '转换失败',
              lines: <String>[_error!],
            ),
          ],
          if (_resultPath != null) ...[
            const SizedBox(height: AppSize.cardGap),
            _MessageCard(
              icon: Icons.check_circle_outline,
              color: palette.accent,
              title: '转换完成',
              lines: <String>[
                _resultPath!,
                '已登记到系统媒体库，可在相册或文件管理器里找到',
              ],
            ),
          ],
          if (_busy) ...[
            const SizedBox(height: AppSize.cardGap + 4),
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: LinearProgressIndicator(
                value: _total > 0 ? (_done / _total).clamp(0.0, 1.0) : null,
                minHeight: 6,
                backgroundColor: palette.divider,
                valueColor: AlwaysStoppedAnimation<Color>(palette.accent),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              _stage,
              style: TextStyle(color: palette.hint, fontSize: 12),
            ),
          ],
          const SizedBox(height: AppSize.cardGap + 6),
          SizedBox(
            height: 46,
            child: FilledButton(
              onPressed: _sources.isEmpty ? null : (_busy ? _cancel : _convert),
              style: FilledButton.styleFrom(
                backgroundColor: _busy ? palette.danger : palette.accent,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(AppSize.cardRadius)),
              ),
              child: Text(
                _busy ? '取消转换' : '开始转换',
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
              ),
            ),
          ),
        ],
      ),
    );
  }

  int _totalInputFrames() {
    var total = 0;
    for (final source in _sources) {
      total += source.frameCount;
    }
    return total;
  }

  Future<void> _pickFiles() async {
    try {
      final picked = await FilePicker.pickFiles(
        dialogTitle: '选择 WebP 文件',
        type: FileType.custom,
        allowedExtensions: const <String>['webp'],
      );
      final paths = picked.map((file) => file.path).whereType<String>().toList();
      if (paths.isEmpty) return;
      setState(() {
        _error = null;
        _resultPath = null;
      });
      for (final path in paths) {
        try {
          final info = await WebpToMp4.probe(path);
          if (!mounted) return;
          setState(() => _sources.add(info));
        } on WebpConversionException catch (error) {
          if (!mounted) return;
          setState(() => _error = error.message);
        }
      }
      await _refreshOutputPath();
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '选择文件失败：$error');
    }
  }

  Future<void> _refreshOutputPath() async {
    if (_sources.isEmpty) {
      if (mounted) setState(() => _pendingOutput = '');
      return;
    }
    final dir = await DownloadLocation.current();
    final base = _sources.first.fileName;
    final dot = base.lastIndexOf('.');
    final stem = dot > 0 ? base.substring(0, dot) : base;
    final suffix = _sources.length > 1 ? '_共${_sources.length}张' : '';
    if (!mounted) return;
    setState(() => _pendingOutput = '$dir/$stem$suffix.mp4');
  }

  void _remove(WebpSourceInfo source) {
    setState(() {
      _sources.remove(source);
      _resultPath = null;
      _error = null;
    });
    _refreshOutputPath();
  }

  void _cancel() {
    setState(() {
      _cancelRequested = true;
      _stage = '正在取消…';
    });
    WebpToMp4.cancel();
  }

  Future<void> _convert() async {
    if (_sources.isEmpty) return;
    final output = _pendingOutput;
    if (output.isEmpty) {
      setState(() => _error = '还没准备好输出路径，请重新选择文件');
      return;
    }
    setState(() {
      _busy = true;
      _cancelRequested = false;
      _error = null;
      _resultPath = null;
      _done = 0;
      _total = 0;
      _stage = '正在准备…';
    });
    try {
      final result = await WebpToMp4.convert(
        sources: _sources.map((source) => source.path).toList(),
        outputPath: output,
        onProgress: (done, total, message) {
          if (!mounted) return;
          setState(() {
            _done = done;
            _total = total;
            _stage = message;
          });
        },
        isCancelled: () => _cancelRequested,
      );
      await MediaSaver.scanToGallery(result.path);
      if (!mounted) return;
      setState(() {
        _busy = false;
        _resultPath = result.path;
        _stage = '';
      });
    } on WebpConversionException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _stage = '';
        _error = error.message;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _stage = '';
        _error = '转换失败：$error';
      });
    }
  }
}

class _MessageCard extends StatelessWidget {
  const _MessageCard({
    required this.icon,
    required this.color,
    required this.title,
    required this.lines,
  });

  final IconData icon;
  final Color color;
  final String title;
  final List<String> lines;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: palette.surface,
        borderRadius: BorderRadius.circular(AppSize.cardRadius),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, size: 20, color: color),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: TextStyle(
                    color: palette.titleText,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                for (final line in lines)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 2),
                    child: Text(
                      line,
                      style: TextStyle(
                          color: palette.hint, fontSize: 12),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
