import 'package:flutter/material.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/theme_controller.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/download/ui/tasks_page.dart';
import 'package:vdl/features/home/ui/home_page.dart';
import 'package:vdl/features/library/ui/library_page.dart';
import 'package:vdl/features/settings/ui/settings_page.dart';

/// 应用根：主题 + 四页签外壳。
class VdlApp extends StatelessWidget {
  const VdlApp({super.key, this.fontFamily});

  /// 仅测试 / golden 用：指定字体族，运行时为 null（跟随系统）。
  final String? fontFamily;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: themeModeNotifier,
      builder: (context, mode, _) => MaterialApp(
        title: '视频下载器',
        debugShowCheckedModeBanner: false,
        theme: appLightTheme(fontFamily: fontFamily),
        darkTheme: appDarkTheme(fontFamily: fontFamily),
        themeMode: mode,
        home: const RootShell(),
      ),
    );
  }
}

/// 底部四页签（首页 / 任务 / 书架 / 设置）。
class RootShell extends StatefulWidget {
  const RootShell({super.key});

  @override
  State<RootShell> createState() => _RootShellState();
}

class _RootShellState extends State<RootShell> {
  int _index = 0;

  static const _tabs = <AppTabItem>[
    AppTabItem(Icons.home_outlined, Icons.home, '首页'),
    AppTabItem(Icons.download_outlined, Icons.download, '任务'),
    AppTabItem(Icons.collections_outlined, Icons.collections, '书架'),
    AppTabItem(Icons.settings_outlined, Icons.settings, '设置'),
  ];

  @override
  void initState() {
    super.initState();
    tabRequest.addListener(_onTabRequest);
  }

  @override
  void dispose() {
    tabRequest.removeListener(_onTabRequest);
    super.dispose();
  }

  void _onTabRequest() {
    final target = tabRequest.value;
    if (target == null) return;
    tabRequest.value = null;
    if (mounted) setState(() => _index = target);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      // IndexedStack 保留各页滚动位置与输入内容（切页签不丢状态）
      body: IndexedStack(
        index: _index,
        children: const [HomePage(), TasksPage(), LibraryPage(), SettingsPage()],
      ),
      bottomNavigationBar: AppTabBar(
        items: _tabs,
        currentIndex: _index,
        onTap: (i) => setState(() => _index = i),
      ),
    );
  }
}
