import 'package:flutter/material.dart';

/// 配色取自参考项目 nuist-sta-app 的设计规范（其 design/home.op 设计稿）。
///
/// 与参考项目一致：**固定品牌蓝**，不跟随壁纸动态取色 —— 动态取色会把
/// accent 变成壁纸的近似色，整套观感就不再是这套设计了。
abstract final class AppColors {
  static const pageBg = Color(0xFFF5F6F8);
  static const surface = Color(0xFFFFFFFF);
  static const titleText = Color(0xFF171A1F);
  static const labelText = Color(0xFF4A4F66);
  static const hint = Color(0xFF9AA0B4);
  static const rowDivider = Color(0xFFF0F1F3);
  static const accent = Color(0xFF0082EF);
  static const tabInactive = Color(0xFF8A8F99);
  static const divider = Color(0xFFE5E6EB);
  static const chevron = Color(0xFFC9CDD4);

  static const success = Color(0xFF00B42A);
  static const warning = Color(0xFFFF7D00);
  static const danger = Color(0xFFF53F3F);
}

/// 深色色值，与 [AppColors] 一一对应。
abstract final class AppColorsDark {
  static const pageBg = Color(0xFF121316);
  static const surface = Color(0xFF1C1D21);
  static const titleText = Color(0xFFF2F3F5);
  static const labelText = Color(0xFFC8CBD2);
  static const hint = Color(0xFF8A8F99);
  static const rowDivider = Color(0xFF24262B);
  static const accent = Color(0xFF4DA3F5);
  static const tabInactive = Color(0xFF8A8F99);
  static const divider = Color(0xFF2A2C32);
  static const chevron = Color(0xFF6E7480);

  static const success = Color(0xFF23C343);
  static const warning = Color(0xFFFF9A2E);
  static const danger = Color(0xFFF76965);
}

/// 随明暗切换的色板，挂在 ThemeData.extensions 上，用 [AppPalette.of] 取。
@immutable
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.brightness,
    required this.pageBg,
    required this.surface,
    required this.titleText,
    required this.labelText,
    required this.hint,
    required this.rowDivider,
    required this.accent,
    required this.tabInactive,
    required this.divider,
    required this.chevron,
    required this.success,
    required this.warning,
    required this.danger,
  });

  final Brightness brightness;
  final Color pageBg;
  final Color surface;
  final Color titleText;
  final Color labelText;
  final Color hint;
  final Color rowDivider;
  final Color accent;
  final Color tabInactive;
  final Color divider;
  final Color chevron;
  final Color success;
  final Color warning;
  final Color danger;

  bool get isDark => brightness == Brightness.dark;

  static const light = AppPalette(
    brightness: Brightness.light,
    pageBg: AppColors.pageBg,
    surface: AppColors.surface,
    titleText: AppColors.titleText,
    labelText: AppColors.labelText,
    hint: AppColors.hint,
    rowDivider: AppColors.rowDivider,
    accent: AppColors.accent,
    tabInactive: AppColors.tabInactive,
    divider: AppColors.divider,
    chevron: AppColors.chevron,
    success: AppColors.success,
    warning: AppColors.warning,
    danger: AppColors.danger,
  );

  static const dark = AppPalette(
    brightness: Brightness.dark,
    pageBg: AppColorsDark.pageBg,
    surface: AppColorsDark.surface,
    titleText: AppColorsDark.titleText,
    labelText: AppColorsDark.labelText,
    hint: AppColorsDark.hint,
    rowDivider: AppColorsDark.rowDivider,
    accent: AppColorsDark.accent,
    tabInactive: AppColorsDark.tabInactive,
    divider: AppColorsDark.divider,
    chevron: AppColorsDark.chevron,
    success: AppColorsDark.success,
    warning: AppColorsDark.warning,
    danger: AppColorsDark.danger,
  );

  static AppPalette of(BuildContext context) =>
      Theme.of(context).extension<AppPalette>() ?? light;

  static AppPalette forBrightness(Brightness brightness) =>
      brightness == Brightness.dark ? dark : light;

  @override
  AppPalette copyWith({
    Brightness? brightness,
    Color? pageBg,
    Color? surface,
    Color? titleText,
    Color? labelText,
    Color? hint,
    Color? rowDivider,
    Color? accent,
    Color? tabInactive,
    Color? divider,
    Color? chevron,
    Color? success,
    Color? warning,
    Color? danger,
  }) {
    return AppPalette(
      brightness: brightness ?? this.brightness,
      pageBg: pageBg ?? this.pageBg,
      surface: surface ?? this.surface,
      titleText: titleText ?? this.titleText,
      labelText: labelText ?? this.labelText,
      hint: hint ?? this.hint,
      rowDivider: rowDivider ?? this.rowDivider,
      accent: accent ?? this.accent,
      tabInactive: tabInactive ?? this.tabInactive,
      divider: divider ?? this.divider,
      chevron: chevron ?? this.chevron,
      success: success ?? this.success,
      warning: warning ?? this.warning,
      danger: danger ?? this.danger,
    );
  }

  @override
  AppPalette lerp(AppPalette? other, double t) {
    if (other == null) return this;
    return AppPalette(
      brightness: t < 0.5 ? brightness : other.brightness,
      pageBg: Color.lerp(pageBg, other.pageBg, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      titleText: Color.lerp(titleText, other.titleText, t)!,
      labelText: Color.lerp(labelText, other.labelText, t)!,
      hint: Color.lerp(hint, other.hint, t)!,
      rowDivider: Color.lerp(rowDivider, other.rowDivider, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      tabInactive: Color.lerp(tabInactive, other.tabInactive, t)!,
      divider: Color.lerp(divider, other.divider, t)!,
      chevron: Color.lerp(chevron, other.chevron, t)!,
      success: Color.lerp(success, other.success, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      danger: Color.lerp(danger, other.danger, t)!,
    );
  }
}
