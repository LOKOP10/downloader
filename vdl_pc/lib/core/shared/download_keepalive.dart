/// 桌面端不需要后台保活：进程活着就在下载，最小化照常跑。
abstract final class DownloadKeepAlive {
  static bool get running => false;

  static Future<void> start(String text, {int? percent}) async {}

  static Future<void> update(String text, {int? percent}) async {}

  static Future<void> stop() async {}
}
