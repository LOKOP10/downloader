import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/features/download/data/download_location.dart';

/// 保存位置对话框：可编辑路径、校验可写、恢复默认。
///
/// Android 分区存储下不是所有目录都能写，所以保存前一定先试写一次，
/// 失败就把具体原因告诉用户（而不是存下来之后下载时才炸）。
///
/// ⚠️ **保存到哪由调用方决定**（[save] / [defaultPath]）：
/// 视频目录与漫画压缩包目录是两套配置（`download.dir` / `manga.dir`），
/// 以前这里把 `DownloadLocation.save` 写死，于是设置页点「漫画保存位置」
/// 实际改的是**视频下载目录**，漫画目录一直是默认值 —— 结果视频被存进了
/// 「漫画」目录，用户按自己设置的路径死活找不到。
class SaveLocationDialog extends StatefulWidget {
  const SaveLocationDialog({
    super.key,
    required this.initialPath,
    required this.save,
    required this.defaultPath,
    this.title = '保存位置',
    this.validator,
    this.directoryPicker,
  });

  final String initialPath;

  /// 真正落盘的配置写入函数（视频用 `DownloadLocation.save`，漫画用 `MangaLocation.save`）。
  final Future<void> Function(String path) save;

  /// 「用默认目录」按钮要取的默认值。
  final Future<String> Function() defaultPath;

  final String title;

  /// 可写的校验函数（默认 `DownloadLocation.validate`）。
  /// 抽成参数只是为了组件测试能在不碰真实文件系统的前提下验证「写到哪」。
  final Future<String?> Function(String path)? validator;

  /// 「从文件管理器选择」用的目录选择器（默认走 Android 的 SAF 目录选择）。
  /// 抽成参数同样是为了测试能注入固定返回值。
  final Future<String?> Function()? directoryPicker;

  @override
  State<SaveLocationDialog> createState() => _SaveLocationDialogState();
}

class _SaveLocationDialogState extends State<SaveLocationDialog> {
  late final TextEditingController _controller;
  String? _message;
  bool _error = false;
  bool _busy = false;
  bool _needPermission = false;
  String _defaultPath = '';

  /// 已经确认保存成功的路径（空表示本次没保存过）。
  String _savedPath = '';

  /// 目录名疑似写错时的实时提示（如 Picture → Pictures），不阻断保存。
  String? _dirHint;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialPath);
    _dirHint = DownloadLocation.suggestStandardDir(widget.initialPath);
    widget.defaultPath().then((value) {
      if (mounted) setState(() => _defaultPath = value);
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    // 用户手输常见漏 "/" 或多斜杠，这里先规范化再校验
    final path = DownloadLocation.normalizePath(_controller.text);
    setState(() {
      _busy = true;
      _message = null;
      _needPermission = false;
      _controller.text = path;
    });
    final problem = await (widget.validator ?? DownloadLocation.validate)(path);
    if (!mounted) return;
    if (problem != null) {
      setState(() {
        _busy = false;
        _message = problem;
        _error = true;
        _needPermission = problem.contains('所有文件访问');
      });
      return;
    }
    await widget.save(path);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = false;
      _savedPath = path;
      _message = '已保存：$path';
    });
  }

  /// 申请「所有文件访问」，授权回来后自动再存一次。
  Future<void> _grant() async {
    setState(() {
      _busy = true;
      _message = '正在打开系统设置，请允许「所有文件访问」后返回';
      _error = false;
    });
    final ok = await DownloadLocation.requestAllFilesAccess();
    if (!mounted) return;
    setState(() => _busy = false);
    if (!ok) {
      setState(() {
        _message = '未获得权限。可改用「用默认目录」，或到系统设置手动开启';
        _error = true;
      });
      return;
    }
    await _save();
  }

  /// 打开系统文件管理器选一个文件夹，选完直接保存。
  ///
  /// Android 上走 SAF（`ACTION_OPEN_DOCUMENT_TREE`），返回的是真实路径
  /// （如 `/storage/emulated/0/Pictures/视频下载器`）；选到云盘 / 需要另一套
  /// 授权的目录时拿不到可写路径，这里会明确说不支持，而不是存一个用不了的地址。
  Future<void> _pickDir() async {
    setState(() {
      _busy = true;
      _message = null;
      _error = false;
      _needPermission = false;
    });
    String? picked;
    try {
      picked = await (widget.directoryPicker ?? defaultDirectoryPicker)();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = true;
        _message = '打开文件管理器失败：$error';
      });
      return;
    }
    if (!mounted) return;
    if (picked == null || picked.trim().isEmpty) {
      setState(() {
        _busy = false;
        _error = true;
        _message = '没有选到可写目录（云盘 / 部分系统目录拿不到文件路径），换一个文件夹试试';
      });
      return;
    }
    final path = DownloadLocation.normalizePath(picked);
    setState(() {
      _controller.text = path;
      _dirHint = DownloadLocation.suggestStandardDir(path);
    });
    // 选完直接按这个目录保存，省掉再点一次「保存」
    await _save();
  }

  /// 默认实现：系统文件管理器的目录选择器。
  static Future<String?> defaultDirectoryPicker() =>
      FilePicker.getDirectoryPath(dialogTitle: '选择保存文件夹');

  Future<void> _useDefault() async {
    final path =
        _defaultPath.isEmpty ? await widget.defaultPath() : _defaultPath;
    if (!mounted) return;
    setState(() {
      _controller.text = path;
      _message = '已填入默认目录，点「保存」生效';
      _error = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return AlertDialog(
      backgroundColor: palette.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      title: Text(widget.title,
          style: TextStyle(color: palette.titleText, fontSize: 16)),
      content: SizedBox(
        width: 460,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '点「选择文件夹」用系统文件管理器挑一个目录（推荐）；也可以直接填绝对路径。\n'
              '写公共目录需要「所有文件访问」权限，保存时会试写校验并提示去授权。',
              style: TextStyle(color: palette.hint, fontSize: 12, height: 1.5),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _controller,
              onChanged: (value) {
                final hint = DownloadLocation.suggestStandardDir(value);
                if (hint != _dirHint) setState(() => _dirHint = hint);
              },
              style: TextStyle(color: palette.titleText, fontSize: 13),
              decoration: InputDecoration(
                hintText: '/storage/emulated/0/Android/data/.../files/Downloads',
                hintStyle: TextStyle(color: palette.hint, fontSize: 12),
                filled: true,
                fillColor: palette.pageBg,
                isDense: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: palette.divider),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: palette.divider),
                ),
              ),
            ),
            if (_dirHint != null) ...[
              const SizedBox(height: 6),
              Text(
                _dirHint!,
                style: TextStyle(color: palette.warning, fontSize: 11, height: 1.4),
              ),
            ],
            const SizedBox(height: 6),
            Text(
              '默认：${_defaultPath.isEmpty ? '（读取中…）' : _defaultPath}',
              style:
                  TextStyle(color: palette.tabInactive, fontSize: 11, height: 1.4),
            ),
            const SizedBox(height: 4),
            Row(
              children: [
                TextButton.icon(
                  onPressed: _busy ? null : _pickDir,
                  icon: Icon(Icons.folder_open, size: 16, color: palette.accent),
                  label: Text('选择文件夹',
                      style: TextStyle(color: palette.accent, fontSize: 13)),
                ),
                const SizedBox(width: 4),
                TextButton(
                  onPressed: _busy ? null : _useDefault,
                  child: Text('用默认目录',
                      style: TextStyle(color: palette.hint, fontSize: 13)),
                ),
              ],
            ),
            if (_needPermission)
              TextButton.icon(
                onPressed: _busy ? null : _grant,
                icon: Icon(Icons.lock_open, size: 16, color: palette.warning),
                label: Text('去授权「所有文件访问」',
                    style: TextStyle(color: palette.warning, fontSize: 13)),
              ),
            if (_message != null)
              Text(
                _message!,
                style: TextStyle(
                  color: _error ? palette.danger : palette.success,
                  fontSize: 12,
                  height: 1.4,
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          // 「关闭」不返回输入框内容：以前它把**未保存**的文本当结果返回，
          // 调用方虽然忽略返回值，但语义上很容易让人以为「已经改好了」
          onPressed: () => Navigator.of(context).pop(_savedPath.isEmpty
              ? null
              : _savedPath),
          child: Text('关闭', style: TextStyle(color: palette.labelText)),
        ),
        FilledButton(
          onPressed: _busy ? null : _save,
          style: FilledButton.styleFrom(
            backgroundColor: palette.accent,
            foregroundColor: Colors.white,
          ),
          child: Text(_busy ? '校验中…' : '保存'),
        ),
      ],
    );
  }
}
