import 'package:flutter/material.dart';

import 'palette.dart';

/// 桌面端字体：拉丁字母走 Segoe UI，中文回退微软雅黑，避免默认 fallback 发虚。
const String kFontFamily = 'Segoe UI';
const List<String> kFontFallback = <String>[
  'Microsoft YaHei UI',
  'Microsoft YaHei',
  'PingFang SC',
  'Noto Sans CJK SC',
];

ThemeData buildTheme() {
  final base = ThemeData.dark(useMaterial3: true);
  final TextTheme text = base.textTheme
      .apply(
        fontFamily: kFontFamily,
        fontFamilyFallback: kFontFallback,
        bodyColor: Palette.text,
        displayColor: Palette.text,
      )
      .copyWith(
        // 桌面屏距近、字号偏小，整体提一档字重与零点几个字距更好读。
        bodyLarge: const TextStyle(
          fontSize: 14,
          height: 1.45,
          letterSpacing: 0.1,
          color: Palette.text,
        ),
        bodyMedium: const TextStyle(
          fontSize: 13,
          height: 1.45,
          letterSpacing: 0.1,
          fontWeight: FontWeight.w500,
          color: Palette.text,
        ),
        titleLarge: const TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.2,
          color: Palette.text,
        ),
      );

  return base.copyWith(
    scaffoldBackgroundColor: Palette.bg,
    colorScheme: base.colorScheme.copyWith(
      primary: Palette.accent,
      surface: Palette.surface,
      error: Palette.danger,
    ),
    textTheme: text,
    primaryTextTheme: text,
    dividerColor: Palette.border,
    tooltipTheme: const TooltipThemeData(
      waitDuration: Duration(milliseconds: 400),
    ),
    visualDensity: VisualDensity.standard,
  );
}
