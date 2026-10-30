import 'package:flutter/material.dart';
import 'package:vdl/app.dart';
import 'package:vdl/features/settings/data/account_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 先读持久化的 Cookie：解析与下载在请求时直接带上
  await AccountStore.instance.load();
  runApp(const VdlApp());
}
