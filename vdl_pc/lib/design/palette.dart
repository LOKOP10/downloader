import 'package:flutter/material.dart';

/// 桌面端配色：深色为主，强调色用于选中态与主按钮。
class Palette {
  Palette._();

  static const Color bg = Color(0xFF14161A);
  static const Color sidebar = Color(0xFF1A1D23);
  static const Color surface = Color(0xFF1F232B);
  static const Color surfaceHover = Color(0xFF272C36);
  static const Color border = Color(0xFF2A2F39);
  static const Color text = Color(0xFFF5F7FB);
  static const Color textDim = Color(0xFFC2CAD8);
  static const Color textFaint = Color(0xFF949EB0);
  static const Color accent = Color(0xFF4F8CFF);
  static const Color success = Color(0xFF3FB950);
  static const Color danger = Color(0xFFE5534B);
  static const Color warning = Color(0xFFD29922);
}

/// 间距与圆角，界面里不写魔法数字。
class Space {
  Space._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double pageH = 28;
  static const double radius = 8;
}
