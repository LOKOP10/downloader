import 'package:flutter/material.dart';

import 'motion.dart';
import 'palette.dart';

/// 主按钮：强调色实心，禁用时自动变灰。
class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final bool enabled = onTap != null;
    return MouseRegion(
      cursor: enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: Motion.fast,
          curve: Motion.curve,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
          decoration: BoxDecoration(
            color: enabled ? Palette.accent : Palette.surfaceHover,
            borderRadius: BorderRadius.circular(Space.sm),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(icon,
                  size: 16,
                  color: enabled ? Colors.white : Palette.textFaint),
              const SizedBox(width: 6),
              Text(label,
                  style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                      color: enabled ? Colors.white : Palette.textFaint)),
            ],
          ),
        ),
      ),
    );
  }
}

/// 次要按钮：描边 + 深色底。
class GhostButton extends StatelessWidget {
  const GhostButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final bool enabled = onTap != null;
    return MouseRegion(
      cursor: enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
          decoration: BoxDecoration(
            color: Palette.surfaceHover,
            borderRadius: BorderRadius.circular(Space.sm),
            border: Border.all(color: Palette.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(icon,
                  size: 15,
                  color: enabled ? Palette.text : Palette.textFaint),
              const SizedBox(width: 6),
              Text(label,
                  style: TextStyle(
                      fontSize: 12.5,
                      color: enabled ? Palette.text : Palette.textFaint)),
            ],
          ),
        ),
      ),
    );
  }
}
