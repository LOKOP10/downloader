import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';

/// 分组小标题（列表上方那行灰字），比标题更弱。
class AppSectionLabel extends StatelessWidget {
  const AppSectionLabel(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 4, 4, 6),
      child: Row(
        children: [
          Expanded(
            child: Text(text, style: TextStyle(color: palette.hint, fontSize: 13)),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

/// 标准列表行：左图标 + 标题 + 可选右侧状态文字 + 箭头，行高 52dp。
///
/// 规格取自参考项目 ProfileListTile：图标框 24、图标 20、间距 12、
/// 标题 14sp/w500、分隔线缩进 52（与文字左对齐）。
class AppListTile extends StatelessWidget {
  const AppListTile({
    super.key,
    required this.title,
    this.icon,
    this.iconColor,
    this.trailingText,
    this.trailing,
    this.showDivider = true,
    this.showChevron = true,
    this.onTap,
    this.height = AppSize.tileH,
    this.leadingWidget,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final IconData? icon;
  final Color? iconColor;
  final String? trailingText;
  final Widget? trailing;
  final bool showDivider;
  final bool showChevron;
  final VoidCallback? onTap;
  final double height;
  final Widget? leadingWidget;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final row = SizedBox(
      height: height,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppSize.screenH),
        child: Row(
          children: [
            if (leadingWidget != null)
              leadingWidget!
            else if (icon != null)
              SizedBox(
                width: AppSize.tileIconBox,
                child: Center(
                  child: Icon(icon, size: 20, color: iconColor ?? palette.labelText),
                ),
              ),
            if (leadingWidget != null || icon != null)
              const SizedBox(width: AppSize.tileIconGap),
            Expanded(
              flex: 3,
              child: subtitle == null
                  ? Text(
                      title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: palette.titleText,
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                      ),
                    )
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(
                          title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: palette.titleText,
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          subtitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(color: palette.hint, fontSize: 12),
                        ),
                      ],
                    ),
            ),
            if (trailingText != null) ...[
              Flexible(
                flex: 2,
                child: Text(
                  trailingText!,
                  textAlign: TextAlign.right,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: palette.hint, fontSize: 13),
                ),
              ),
              const SizedBox(width: 4),
            ],
            if (trailing != null) trailing!,
            if (showChevron) Icon(Icons.chevron_right, color: palette.chevron, size: 20),
          ],
        ),
      ),
    );
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (onTap == null) row else InkWell(onTap: onTap, child: row),
        if (showDivider)
          Divider(
            height: 1,
            thickness: 1,
            indent: (icon == null && leadingWidget == null)
                ? AppSize.screenH
                : AppSize.dividerIndent,
            color: palette.rowDivider,
          ),
      ],
    );
  }
}
