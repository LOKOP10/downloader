import 'dart:convert';
import 'dart:io';

import 'shared/net/http.dart';
import 'shared/site.dart';

/// 原桌面端 config.json 里的 cookie 字段 → 站点。
/// Instagram 的 ins_cookie 暂时没有对应站点（Flutter 端未接入 Instagram）。
const Map<String, SitePlatform> kCookieFields = <String, SitePlatform>{
  'cookie': SitePlatform.douyin,
  'x_cookie': SitePlatform.x,
  'bili_cookie': SitePlatform.bili,
  'xhs_cookie': SitePlatform.xhs,
  'jm_cookie': SitePlatform.jm,
};

/// 从原桌面端读取 cookie 的结果。
class CookieLoadResult {
  const CookieLoadResult({
    this.configPath,
    this.loaded = const <SitePlatform, int>{},
    this.error,
  });

  /// 实际使用的 config.json 路径，null 表示没找到。
  final String? configPath;

  /// 各站点注入的 cookie 长度（空 cookie 不计入）。
  final Map<SitePlatform, int> loaded;

  final String? error;

  bool get ok => loaded.isNotEmpty;

  String get summary {
    if (error != null) return '读取失败：$error';
    if (configPath == null) return '没找到原桌面端的 config.json';
    if (loaded.isEmpty) return '读到了 $configPath，但里面没有已填写的 Cookie';
    final parts = loaded.entries
        .map((MapEntry<SitePlatform, int> e) => '${e.key.label}(${e.value})')
        .join('、');
    return '已载入：$parts';
  }
}

/// 把原桌面端的登录态搬过来：读 config.json，写进 AppHttp.cookies。
abstract final class CookieBridge {
  /// 环境变量优先，方便调试时指向任意一份 config。
  static const String _envKey = 'VDL_PC_CONFIG';

  static CookieLoadResult load({String? explicitPath}) {
    final String? path = explicitPath ?? _findConfig();
    if (path == null) {
      return const CookieLoadResult();
    }
    try {
      final text = File(path).readAsStringSync();
      final decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        return CookieLoadResult(configPath: path, error: '格式不是 JSON 对象');
      }
      final loaded = <SitePlatform, int>{};
      kCookieFields.forEach((String field, SitePlatform platform) {
        final value = decoded[field];
        if (value is String && value.trim().isNotEmpty) {
          AppHttp.cookies[platform] = value.trim();
          loaded[platform] = value.trim().length;
        }
      });
      return CookieLoadResult(configPath: path, loaded: loaded);
    } catch (error) {
      return CookieLoadResult(configPath: path, error: '$error');
    }
  }

  /// 找 config.json：环境变量 → 程序目录 → 向上逐级找 douyin-downloader/dist/*/config.json。
  static String? _findConfig() {
    final env = Platform.environment[_envKey];
    if (env != null && env.isNotEmpty && File(env).existsSync()) return env;

    Directory dir = File(Platform.resolvedExecutable).parent;
    for (var depth = 0; depth < 7; depth++) {
      final direct = File('${dir.path}${Platform.pathSeparator}config.json');
      if (direct.existsSync()) return direct.path;

      final distDir =
          Directory('${dir.path}${Platform.pathSeparator}douyin-downloader${Platform.pathSeparator}dist');
      if (distDir.existsSync()) {
        final found = _newestConfigIn(distDir);
        if (found != null) return found;
      }

      final parent = dir.parent;
      if (parent.path == dir.path) break;
      dir = parent;
    }
    return null;
  }

  /// 版本目录按名字倒序，取最新的那一份 config.json。
  static String? _newestConfigIn(Directory distDir) {
    final candidates = <File>[];
    for (final entry in distDir.listSync()) {
      if (entry is! Directory) continue;
      final cfg =
          File('${entry.path}${Platform.pathSeparator}config.json');
      if (cfg.existsSync()) candidates.add(cfg);
    }
    if (candidates.isEmpty) return null;
    candidates.sort((File a, File b) =>
        b.parent.path.compareTo(a.parent.path));
    return candidates.first.path;
  }
}
