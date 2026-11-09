import 'package:flutter/material.dart';

import '../design/palette.dart';
import '../design/widgets.dart';

/// 功能尚未实现的页面：统一用页头 + 待办清单，避免空白页看不出进度。
class PlaceholderPage extends StatelessWidget {
  const PlaceholderPage({
    super.key,
    required this.title,
    this.subtitle,
    required this.todo,
  });

  final String title;
  final String? subtitle;
  final List<String> todo;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(
          Space.pageH, Space.xl, Space.pageH, Space.xl),
      children: <Widget>[
        PageHeading(title: title, subtitle: subtitle),
        const SizedBox(height: Space.xl),
        TodoPanel(items: todo),
      ],
    );
  }
}
