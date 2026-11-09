import 'package:flutter/material.dart';

import 'app/shell.dart';
import 'core/app_state.dart';
import 'design/theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AppState.init();
  runApp(const VdlApp());
}

class VdlApp extends StatelessWidget {
  const VdlApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '视频下载器',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(),
      home: const Shell(),
    );
  }
}
