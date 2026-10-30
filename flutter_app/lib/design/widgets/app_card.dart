import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';

/// 圆角卡片容器：白底、12dp 圆角、**无阴影**。
///
/// 层次靠「页面底灰 + 卡片白」的色差拉出来，不用 elevation —— 深色模式下
/// 阴影几乎不可见，浅色下又显得浮（参考项目也是这么做的）。
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.onTap,
    this.padding = const EdgeInsets.all(16),
    this.margin = EdgeInsets.zero,
  });

  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final shape = BorderRadius.circular(AppSize.cardRadius);
    return Padding(
      padding: margin,
      child: Material(
        color: palette.surface,
        borderRadius: shape,
        clipBehavior: Clip.antiAlias,
        child: onTap == null
            ? Padding(padding: padding, child: child)
            : InkWell(
                onTap: onTap,
                child: Padding(padding: padding, child: child),
              ),
      ),
    );
  }
}

/// 只画底色与圆角、内部自己排布（用于列表分组），内边距由子项负责。
class AppCardGroup extends StatelessWidget {
  const AppCardGroup({super.key, required this.children, this.margin = EdgeInsets.zero});

  final List<Widget> children;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Padding(
      padding: margin,
      child: Material(
        color: palette.surface,
        borderRadius: BorderRadius.circular(AppSize.cardRadius),
        clipBehavior: Clip.antiAlias,
        child: Column(mainAxisSize: MainAxisSize.min, children: children),
      ),
    );
  }
}
