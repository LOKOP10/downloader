import 'package:flutter/material.dart';

import 'colors.dart';

/// 尺寸与排版规范，全部来自参考项目的组件源码，改这里就能全局调。
abstract final class AppSize {
  /// 页面左右留白
  static const screenH = 16.0;

  /// 页面顶部留白（AppBar 下方第一块内容）
  static const screenTop = 8.0;

  /// 卡片之间的竖直间距
  static const cardGap = 10.0;

  /// 卡片圆角
  static const cardRadius = 12.0;

  /// 列表行高度
  static const tileH = 52.0;

  /// 列表行图标框
  static const tileIconBox = 24.0;

  /// 列表行图标与文字间距
  static const tileIconGap = 12.0;

  /// 分隔线缩进（与文字左对齐：16 + 24 + 12）
  static const dividerIndent = 52.0;

  /// 底栏高度
  static const tabBarH = 56.0;

  /// 顶栏高度
  static const appBarH = 48.0;
}

/// 浅色主题。
ThemeData appLightTheme({String? fontFamily}) =>
    _build(Brightness.light, fontFamily);

/// 深色主题。
ThemeData appDarkTheme({String? fontFamily}) =>
    _build(Brightness.dark, fontFamily);

ThemeData _build(Brightness brightness, String? fontFamily) {
  final p = AppPalette.forBrightness(brightness);
  final base = ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: ColorScheme.fromSeed(
      seedColor: p.accent,
      brightness: brightness,
    ).copyWith(
      // 不用 seed 推导出的近似色，直接用设计稿的 accent。
      primary: p.accent,
      surface: p.surface,
      error: p.danger,
    ),
    scaffoldBackgroundColor: p.pageBg,
    dividerColor: p.divider,
  );
  return base.copyWith(
    // 仅测试环境指定字体（headless 下默认字体不含中文，会渲染成方块）
    primaryTextTheme:
        fontFamily == null ? base.primaryTextTheme : base.primaryTextTheme.apply(fontFamily: fontFamily),
    appBarTheme: AppBarTheme(
      backgroundColor: p.surface,
      surfaceTintColor: Colors.transparent,
      foregroundColor: p.titleText,
      centerTitle: true,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: TextStyle(
        color: p.titleText,
        fontSize: 17,
        fontWeight: FontWeight.w600,
      ),
    ),
    dividerTheme: DividerThemeData(color: p.rowDivider, thickness: 1, space: 1),
    // 文本层级：17/600 标题、14/500 正文、12 提示，与参考项目一致。
    textTheme: base.textTheme.apply(fontFamily: fontFamily).copyWith(
      titleMedium: TextStyle(
        color: p.titleText,
        fontSize: 17,
        fontWeight: FontWeight.w600,
      ),
      bodyMedium: TextStyle(
        color: p.titleText,
        fontSize: 14,
        fontWeight: FontWeight.w500,
      ),
      bodySmall: TextStyle(color: p.hint, fontSize: 12),
    ),
    extensions: <ThemeExtension<dynamic>>[p],
  );
}
