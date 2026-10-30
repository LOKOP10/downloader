import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';

/// 顶栏：白底 + 居中标题 + 底部 1px 分隔线（参考项目的 AppBar 规范）。
class AppTopBar extends StatelessWidget implements PreferredSizeWidget {
  const AppTopBar({super.key, required this.title, this.actions, this.bottom, this.leading});

  final String title;
  final List<Widget>? actions;
  final Widget? leading;
  final Widget? bottom;

  @override
  Size get preferredSize =>
      Size.fromHeight(AppSize.appBarH + (bottom == null ? 0 : 48) + 1);

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return AppBar(
      toolbarHeight: AppSize.appBarH,
      title: Text(title),
      centerTitle: true,
      leading: leading,
      actions: actions,
      bottom: PreferredSize(
        preferredSize: Size.fromHeight((bottom == null ? 0 : 48) + 1),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (bottom != null) bottom!,
            Container(height: 1, color: palette.divider),
          ],
        ),
      ),
    );
  }
}

/// 底部页签单项。
class AppTabItem {
  const AppTabItem(this.icon, this.activeIcon, this.label);

  final IconData icon;
  final IconData activeIcon;
  final String label;
}

/// 底栏：白底、顶部 1px 线、56dp 高、图标 + 10sp 文字。
///
/// 刻意不用 NavigationBar：它的水波纹以整条栏为画布铺开，默认高度也只有 80。
class AppTabBar extends StatelessWidget {
  const AppTabBar({
    super.key,
    required this.items,
    required this.currentIndex,
    required this.onTap,
  });

  final List<AppTabItem> items;
  final int currentIndex;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Material(
      color: palette.surface,
      child: Container(
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: palette.divider)),
        ),
        child: SafeArea(
          top: false,
          child: SizedBox(
            height: AppSize.tabBarH,
            child: Row(
              children: [
                for (var i = 0; i < items.length; i++)
                  Expanded(
                    child: _TabButton(
                      item: items[i],
                      selected: i == currentIndex,
                      onTap: () => onTap(i),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TabButton extends StatelessWidget {
  const _TabButton({required this.item, required this.selected, required this.onTap});

  final AppTabItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = selected ? palette.accent : palette.tabInactive;
    return Semantics(
      button: true,
      selected: selected,
      label: item.label,
      excludeSemantics: true,
      child: InkWell(
        onTap: onTap,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(selected ? item.activeIcon : item.icon, color: color, size: 22),
            const SizedBox(height: 2),
            Text(item.label, style: TextStyle(color: color, fontSize: 10)),
          ],
        ),
      ),
    );
  }
}
