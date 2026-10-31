import 'package:shared_preferences/shared_preferences.dart';

/// 下载相关设置（目前只有并发连接数）。
///
/// 为什么单独存：并发数决定「一个文件切几段下」。Iwara 这类 CDN 对单连接
/// 限速明显（实测单连接约 0.3 MB/s），多开几段能成倍提速；但站点限流强度
/// 不同，所以做成可调、持久化，默认 8。
abstract final class DownloadSettings {
  static const _key = 'download.connections';

  /// 可选的并发档位。
  static const options = <int>[4, 8, 12, 16];

  static const defaultConnections = 8;

  static Future<int> loadConnections() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final value = prefs.getInt(_key);
      if (value != null && options.contains(value)) return value;
    } catch (_) {}
    return defaultConnections;
  }

  static Future<void> saveConnections(int value) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(_key, value);
    } catch (_) {}
  }

  /// 夹到合法范围（1~16），供引擎兜底。
  static int clampConnections(int value) {
    if (value < 1) return 1;
    if (value > 16) return 16;
    return value;
  }
}
