import 'package:flutter/material.dart';

import 'app/shell.dart';
import 'design/theme.dart';

void main() {
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
