import 'download/task_store.dart';

/// 全应用共享的状态。桌面端进程常驻、单窗口，用全局单例最简单直接。
class AppState {
  AppState._();

  /// 下载任务队列（ChangeNotifier，页面用它刷新）。
  static final TaskStore tasks = TaskStore.instance;

  static Future<void> init() async {
    await tasks.ensureSettings();
  }
}
