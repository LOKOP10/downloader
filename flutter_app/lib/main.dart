import 'package:flutter/material.dart';
import 'package:vdl/app.dart';
import 'package:vdl/features/settings/data/account_store.dart';
import 'package:vdl/shared/net/proxy_config.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 先读持久化的 Cookie：解析与下载在请求时直接带上
  await AccountStore.instance.load();
  // 把代理应用到 HTTP 层：手动填的优先，其次读系统 WiFi 代理。
  // Dart 的 HttpClient / SecureSocket 默认 DIRECT，不读系统代理，不设就一直直连。
  await ProxyConfig.apply();
  runApp(const VdlApp());
}
