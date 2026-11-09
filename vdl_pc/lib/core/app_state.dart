import 'cookie_bridge.dart';
import 'download/task_store.dart';

/// 全应用共享的状态。桌面端进程常驻、单窗口，用全局单例最简单直接。
class AppState {
  AppState._();

  /// 下载任务队列（ChangeNotifier，页面用它刷新）。
  static final TaskStore tasks = TaskStore.instance;

  /// 从原桌面端搬过来的登录态。
  static CookieLoadResult cookies = const CookieLoadResult();

  static Future<void> init() async {
    cookies = CookieBridge.load();
    await tasks.ensureSettings();
  }
}
