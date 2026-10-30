import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';

/// 状态胶囊：下载中 / 已完成 / 失败之类的小标签。
class StatusPill extends StatelessWidget {
  const StatusPill(this.text, {super.key, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        text,
        style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w500),
      ),
    );
  }
}

/// 语义色三态。
enum SemanticKind { success, warning, danger, info }

Color semanticColor(BuildContext context, SemanticKind kind) {
  final palette = AppPalette.of(context);
  return switch (kind) {
    SemanticKind.success => palette.success,
    SemanticKind.warning => palette.warning,
    SemanticKind.danger => palette.danger,
    SemanticKind.info => palette.accent,
  };
}
