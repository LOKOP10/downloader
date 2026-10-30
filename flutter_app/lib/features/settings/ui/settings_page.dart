import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/theme_controller.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/app_list_tile.dart';
import 'package:vdl/design/widgets/platform_style.dart';
import 'package:vdl/features/settings/data/account_store.dart';
import 'package:vdl/features/settings/ui/widgets/login_dialog.dart';
import 'package:vdl/features/download/data/download_location.dart';
import 'package:vdl/features/manga/data/jm_preview_cache.dart';
import 'package:vdl/features/manga/data/manga_location.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/settings/ui/log_page.dart';
import 'package:vdl/features/settings/ui/widgets/platform_grid.dart';
import 'package:vdl/features/settings/ui/widgets/save_location_dialog.dart';
import 'package:vdl/features/parse/parser_registry.dart';
import 'package:vdl/shared/app_log.dart';
import 'package:vdl/shared/site.dart';

/// 设置页：下载 / 账号 / 外观 / 关于。分组卡片 + 52dp 列表行。
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  bool _downloadCover = false;
  String _naming = '唯一 ID';

  /// 当前生效的两个目录（列表里直接显示，避免「以为改了、其实没改」）。
  String _videoDir = '';
  String _mangaDir = '';

  /// 禁漫预览磁盘缓存占用（空 = 还在统计）。
  String _previewCacheText = '';

  @override
  void initState() {
    super.initState();
    _refreshDirs();
    _refreshPreviewCache();
  }

  Future<void> _refreshPreviewCache() async {
    final bytes = await JmPreviewCache.sizeBytes();
    if (!mounted) return;
    setState(() => _previewCacheText = _sizeText(bytes));
  }

  Future<void> _clearPreviewCache() async {
    final removed = await JmPreviewCache.clear();
    await _refreshPreviewCache();
    if (!mounted) return;
    _toast('已清理预览缓存（' + removed.toString() + ' 个文件）');
  }

  static String _sizeText(int bytes) {
    if (bytes <= 0) return '暂无缓存';
    if (bytes < 1024 * 1024) return (bytes / 1024).toStringAsFixed(0) + ' KB';
    if (bytes < 1024 * 1024 * 1024) {
      return (bytes / 1024 / 1024).toStringAsFixed(1) + ' MB';
    }
    return (bytes / 1024 / 1024 / 1024).toStringAsFixed(2) + ' GB';
  }

  Future<void> _refreshDirs() async {
    final video = await DownloadLocation.current();
    final manga = await MangaLocation.current();
    if (!mounted) return;
    setState(() {
      _videoDir = video;
      _mangaDir = manga;
    });
  }


  void _toast(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppTopBar(title: '设置'),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
            AppSize.screenH, AppSize.screenTop, AppSize.screenH, 24),
        children: [
          const AppSectionLabel('下载'),
          AppCardGroup(children: [
            AppListTile(
              icon: Icons.folder_outlined,
              title: '视频 / 图片保存位置',
              subtitle: _videoDir.isEmpty ? '读取中…' : _videoDir,
              trailingText: '修改',
              onTap: _editSavePath,
            ),
            AppListTile(
              icon: Icons.collections_bookmark_outlined,
              title: '漫画保存位置',
              subtitle: _mangaDir.isEmpty ? '读取中…' : _mangaDir,
              trailingText: '修改',
              onTap: _editMangaPath,
            ),
            AppListTile(
              icon: Icons.tune,
              title: '同时下载数',
              trailingText: '3',
              onTap: () => _toast('调整并发数（待接入）'),
            ),
            AppListTile(
              icon: Icons.drive_file_rename_outline,
              title: '命名规则',
              trailingText: _naming,
              onTap: _pickNaming,
            ),
            _SwitchRow(
              icon: Icons.image_outlined,
              title: '同时下载封面',
              value: _downloadCover,
              showDivider: true,
              onChanged: (v) => setState(() => _downloadCover = v),
            ),
            AppListTile(
              icon: Icons.cleaning_services_outlined,
              title: '清理禁漫预览缓存',
              subtitle: _previewCacheText.isEmpty ? '统计中…' : _previewCacheText,
              trailingText: '清理',
              showDivider: false,
              onTap: _clearPreviewCache,
            ),
          ]),
          const SizedBox(height: AppSize.cardGap + 4),
          const AppSectionLabel('账号'),
          ListenableBuilder(
            listenable: AccountStore.instance,
            builder: (context, _) => AppCardGroup(children: [
              for (var i = 0; i < SitePlatform.values.length; i++)
                AppListTile(
                  title: SitePlatform.values[i].label,
                  leadingWidget: _PlatformBadge(platform: SitePlatform.values[i]),
                  trailingText:
                      AccountStore.instance.loggedIn(SitePlatform.values[i])
                          ? '已登录'
                          : '未登录',
                  showDivider: i < SitePlatform.values.length - 1,
                  onTap: () => _openLogin(SitePlatform.values[i]),
                ),
            ]),
          ),
          const SizedBox(height: AppSize.cardGap + 4),
          const AppSectionLabel('外观'),
          AppCardGroup(children: [
            ValueListenableBuilder<ThemeMode>(
              valueListenable: themeModeNotifier,
              builder: (context, current, _) => Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (var i = 0; i < 3; i++)
                    _OptionRow(
                      label: const ['跟随系统', '浅色', '深色'][i],
                      selected: current ==
                          [ThemeMode.system, ThemeMode.light, ThemeMode.dark][i],
                      showDivider: i < 2,
                      onTap: () => themeModeNotifier.value =
                          [ThemeMode.system, ThemeMode.light, ThemeMode.dark][i],
                    ),
                ],
              ),
            ),
          ]),
          const SizedBox(height: AppSize.cardGap + 4),
          const AppSectionLabel('诊断'),
          AppCardGroup(children: [
            ValueListenableBuilder<int>(
              valueListenable: AppLog.instance.revision,
              builder: (context, _, __) => AppListTile(
                icon: Icons.receipt_long_outlined,
                title: '运行日志',
                trailingText: AppLog.instance.count == 0
                    ? '暂无'
                    : AppLog.instance.count.toString() + ' 条',
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const LogPage()),
                ),
              ),
            ),
          ]),
          const SizedBox(height: AppSize.cardGap + 4),
          const AppSectionLabel('支持的平台'),
          PlatformGrid(
            onTap: (p) => _toast(ParserRegistry.supports(p)
                ? '${p.label} 已支持：把作品链接粘到首页输入框'
                : '${p.label} 解析还没接入'),
          ),
          const SizedBox(height: AppSize.cardGap + 4),
          const AppSectionLabel('关于'),
          AppCardGroup(children: [
            AppListTile(
              icon: Icons.info_outline,
              title: '版本',
              trailingText: '1.0.0 (Flutter)',
              showChevron: false,
              onTap: () => _toast('视频下载器 1.0.0 · Flutter 重做版'),
            ),
            AppListTile(
              icon: Icons.description_outlined,
              title: '开源许可',
              showDivider: false,
              onTap: () => _toast('第三方许可（待接入）'),
            ),
          ]),
        ],
      ),
    );
  }

  /// 视频 / 图片的下载目录（`download.dir`）。
  ///
  /// ⚠️ 这里必须传 `DownloadLocation.save`：以前对话框内部写死它，
  /// 导致下面「漫画保存位置」也改的是这一项，视频于是被存进了漫画目录。
  Future<void> _editSavePath() async {
    final current = await DownloadLocation.current();
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (_) => SaveLocationDialog(
        title: '视频 / 图片保存位置',
        initialPath: current,
        save: DownloadLocation.save,
        defaultPath: DownloadLocation.defaultDir,
      ),
    );
    final latest = await DownloadLocation.current();
    // 让下载队列重新解析目录（下一次下载就生效）
    TaskStore.instance.invalidateDir();
    await _refreshDirs();
    if (!mounted) return;
    // 显示**完整路径**：只报最后一段的话，用户根本看不出到底生效在哪
    _toast('视频 / 图片保存位置：$latest');
  }

  /// 漫画压缩包目录（`manga.dir`），与视频目录完全独立。
  Future<void> _editMangaPath() async {
    final current = await MangaLocation.current();
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (_) => SaveLocationDialog(
        title: '漫画保存位置',
        initialPath: current,
        save: MangaLocation.save,
        defaultPath: MangaLocation.defaultDir,
      ),
    );
    final latest = await MangaLocation.current();
    await _refreshDirs();
    if (!mounted) return;
    _toast('漫画保存位置：$latest');
  }

  Future<void> _openLogin(SitePlatform platform) async {
    await showDialog<void>(
      context: context,
      builder: (_) => LoginDialog(platform: platform),
    );
  }

  Future<void> _pickNaming() async {
    final palette = AppPalette.of(context);
    const options = ['唯一 ID', '时间戳', '标题', '作者 - 标题'];
    final picked = await showModalBottomSheet<String>(
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
              child: Text('命名规则',
                  style: TextStyle(
                      color: palette.titleText,
                      fontSize: 15,
                      fontWeight: FontWeight.w600)),
            ),
            for (final option in options)
              ListTile(
                title:
                    Text(option, style: TextStyle(color: palette.titleText, fontSize: 14)),
                trailing: option == _naming
                    ? Icon(Icons.check, color: palette.accent, size: 20)
                    : null,
                onTap: () => Navigator.of(sheetContext).pop(option),
              ),
          ],
        ),
      ),
    );
    if (picked != null && mounted) setState(() => _naming = picked);
  }
}

/// 带开关的列表行（52dp，右侧自适应 Switch）。
class _SwitchRow extends StatelessWidget {
  const _SwitchRow({
    required this.icon,
    required this.title,
    required this.value,
    required this.onChanged,
    required this.showDivider,
  });

  final IconData icon;
  final String title;
  final bool value;
  final ValueChanged<bool> onChanged;
  final bool showDivider;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Column(
      children: [
        SizedBox(
          height: AppSize.tileH,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSize.screenH),
            child: Row(
              children: [
                SizedBox(
                  width: AppSize.tileIconBox,
                  child: Center(child: Icon(icon, size: 20, color: palette.labelText)),
                ),
                const SizedBox(width: AppSize.tileIconGap),
                Expanded(
                  child: Text(title,
                      style: TextStyle(
                          color: palette.titleText,
                          fontSize: 14,
                          fontWeight: FontWeight.w500)),
                ),
                Switch.adaptive(
                  value: value,
                  onChanged: onChanged,
                  activeTrackColor: palette.accent,
                ),
              ],
            ),
          ),
        ),
        if (showDivider)
          Divider(
              height: 1,
              thickness: 1,
              indent: AppSize.dividerIndent,
              color: palette.rowDivider),
      ],
    );
  }
}

/// 单选行：文字 + 选中对勾。
class _OptionRow extends StatelessWidget {
  const _OptionRow({
    required this.label,
    required this.selected,
    required this.showDivider,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final bool showDivider;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Column(
      children: [
        InkWell(
          onTap: onTap,
          child: SizedBox(
            height: AppSize.tileH,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSize.screenH),
              child: Row(
                children: [
                  Expanded(
                    child: Text(label,
                        style: TextStyle(
                            color: palette.titleText,
                            fontSize: 14,
                            fontWeight: FontWeight.w500)),
                  ),
                  if (selected) Icon(Icons.check, color: palette.accent, size: 20),
                ],
              ),
            ),
          ),
        ),
        if (showDivider)
          Divider(
              height: 1,
              thickness: 1,
              indent: AppSize.screenH,
              color: palette.rowDivider),
      ],
    );
  }
}

/// 账号行左侧的平台色块。
class _PlatformBadge extends StatelessWidget {
  const _PlatformBadge({required this.platform});

  final SitePlatform platform;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 24,
      height: 24,
      decoration: BoxDecoration(
        color: platformColor(platform),
        borderRadius: BorderRadius.circular(6),
      ),
      alignment: Alignment.center,
      child: Text(
        platform.glyph,
        style: const TextStyle(
            color: Colors.white, fontSize: 11, fontWeight: FontWeight.w600),
      ),
    );
  }
}
