import 'dart:io';

import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vdl_pc/core/shared/net/browser_http.dart';
import 'package:vdl_pc/core/shared/net/http.dart';

/// 代理配置：**手动填的优先，其次读 Android 系统（WiFi）代理**。
///
/// 为什么必须有这一步：Dart 的 `HttpClient` 和裸 `SecureSocket` 都不读系统代理，
/// 默认一律 DIRECT。用户开着 Clash 只设了 WiFi 代理时，App 里所有请求仍是直连，
/// 表现为 hanime1 / Pornhub 的 TLS 握手 30 秒超时（曾真实发生）。
/// Clash 的 VPN / TUN 全局模式不需要这里配置（TUN 在 IP 层接管）。
abstract final class ProxyConfig {
  static const MethodChannel _channel = MethodChannel('vdl/net');
  static const String _prefsKey = 'net.proxy';

  /// 手动配置值（null / 空 = 跟随系统）。
  static String? manual;

  /// 当前实际生效的代理（null = 直连），设置页用它显示状态。
  static String? active;

  /// 扒掉 scheme 与空白，统一成 `host:port`；非法值返回 null。
  static String? normalize(String? raw) {
    if (raw == null) return null;
    var value = raw.trim();
    if (value.isEmpty) return null;
    value = value
        .replaceFirst(RegExp('^https?://', caseSensitive: false), '')
        .replaceAll('/', '')
        .trim();
    if (value.isEmpty) return null;
    // 只填端口号（如 1234）也要能用：补成本机回环地址。
    // 用户看到 Clash 写着「HTTP 端口 1234」，很容易只填 1234；老实现把它判成非法，
    // 于是静默退回「跟随系统」，表现出来就是「保存了却没生效」。
    if (RegExp(r'^\d+$').hasMatch(value)) {
      final portOnly = int.tryParse(value);
      if (portOnly == null || portOnly <= 0 || portOnly > 65535) return null;
      return '127.0.0.1:' + portOnly.toString();
    }
    final parts = value.split(':');
    if (parts.length != 2) return null;
    final host = parts[0].trim();
    final port = int.tryParse(parts[1].trim());
    if (host.isEmpty || port == null || port <= 0 || port > 65535) return null;
    return host + ':' + port.toString();
  }

  /// 读设置并把代理应用到 [AppHttp] / [BrowserHttp]。返回实际生效值。
  static Future<String?> apply() async {
    String? stored;
    try {
      final prefs = await SharedPreferences.getInstance();
      stored = prefs.getString(_prefsKey);
    } catch (_) {}
    manual = normalize(stored);

    String? value = manual;
    if (value == null) {
      // 跟随系统：只有 Android 有这条路，其它平台直接直连
      if (Platform.isAndroid) {
        try {
          value = normalize(await _channel.invokeMethod<String>('systemProxy'));
        } catch (_) {
          value = null;
        }
      }
    }
    active = value;
    AppHttp.proxy = value;
    BrowserHttp.proxy = value;
    return value;
  }

  /// 保存手动代理（传 null / 空字符串 = 改回跟随系统）。
  static Future<String?> saveManual(String? raw) async {
    final value = normalize(raw);
    try {
      final prefs = await SharedPreferences.getInstance();
      if (value == null) {
        await prefs.remove(_prefsKey);
      } else {
        await prefs.setString(_prefsKey, value);
      }
    } catch (_) {}
    return apply();
  }
}
