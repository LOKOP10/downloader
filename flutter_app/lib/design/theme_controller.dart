import 'package:flutter/material.dart';

/// 主题模式（跟随系统 / 浅色 / 深色）。设置页改它，根部件监听后立即生效。
///
/// 首版是内存态；接入设置持久化后从 SharedPreferences 读初值并回写。
final ValueNotifier<ThemeMode> themeModeNotifier =
    ValueNotifier<ThemeMode>(ThemeMode.system);
