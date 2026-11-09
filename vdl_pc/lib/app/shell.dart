import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../design/motion.dart';
import '../design/palette.dart';
import 'sections.dart';

/// 主壳层：左侧固定导航 + 右侧内容画布 + 底部状态条。
class Shell extends StatefulWidget {
  const Shell({super.key});

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(
        children: <Widget>[
          Expanded(
            child: Row(
              children: <Widget>[
                _Sidebar(
                  index: _index,
                  onSelect: (int i) {
                    if (i != _index) setState(() => _index = i);
                  },
                ),
                const VerticalDivider(width: 1, thickness: 1),
                Expanded(
                  child: ColoredBox(
                    color: Palette.bg,
                    child: _AnimatedPages(
                      index: _index,
                      children: kSections
                          .map((Section s) => Builder(builder: s.builder))
                          .toList(),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const _StatusBar(),
        ],
      ),
    );
  }
}

/// 带动画的 IndexedStack：切页时淡入上移，但不重建子树，各页状态与滚动位置都保留。
class _AnimatedPages extends StatefulWidget {
  const _AnimatedPages({required this.index, required this.children});

  final int index;
  final List<Widget> children;

  @override
  State<_AnimatedPages> createState() => _AnimatedPagesState();
}

class _AnimatedPagesState extends State<_AnimatedPages>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: Motion.normal,
    value: 1,
  );

  @override
  void didUpdateWidget(_AnimatedPages oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.index != widget.index) {
      _controller.forward(from: 0);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) {
        final double t = Motion.curve.transform(_controller.value);
        return Opacity(
          opacity: t,
          child: Transform.translate(
            offset: Offset(0, (1 - t) * 12),
            child: child,
          ),
        );
      },
      child: IndexedStack(index: widget.index, children: widget.children),
    );
  }
}

class _Sidebar extends StatelessWidget {
  const _Sidebar({required this.index, required this.onSelect});

  final int index;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 220,
      color: Palette.sidebar,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          const Padding(
            padding: EdgeInsets.fromLTRB(Space.lg, Space.xl, Space.lg, Space.lg),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('视频下载器',
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: Palette.text,
                    )),
                SizedBox(height: 2),
                Text('v1.8.19.4',
                    style: TextStyle(fontSize: 11, color: Palette.textFaint)),
              ],
            ),
          ),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(horizontal: Space.sm),
              itemCount: kSections.length,
              itemBuilder: (BuildContext context, int i) => _NavItem(
                section: kSections[i],
                active: i == index,
                onTap: () => onSelect(i),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 单个导航项：悬停微亮，选中时背景、图标与文字颜色平滑过渡。
class _NavItem extends StatefulWidget {
  const _NavItem({
    required this.section,
    required this.active,
    required this.onTap,
  });

  final Section section;
  final bool active;
  final VoidCallback onTap;

  @override
  State<_NavItem> createState() => _NavItemState();
}

class _NavItemState extends State<_NavItem> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final bool active = widget.active;
    final Color bg = active
        ? Palette.surfaceHover
        : (_hover ? Palette.surface : Colors.transparent);
    final Color fg = active || _hover ? Palette.text : Palette.textDim;
    final Color iconColor = active ? Palette.accent : fg;

    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hover = true),
        onExit: (_) => setState(() => _hover = false),
        child: GestureDetector(
          onTap: widget.onTap,
          behavior: HitTestBehavior.opaque,
          child: AnimatedContainer(
            duration: Motion.fast,
            curve: Motion.curve,
            padding: const EdgeInsets.symmetric(
                horizontal: Space.md, vertical: 10),
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(Space.sm),
            ),
            child: Row(
              children: <Widget>[
                AnimatedSwitcher(
                  duration: Motion.fast,
                  child: Icon(
                    widget.section.icon,
                    key: ValueKey<String>('$iconColor'),
                    size: 18,
                    color: iconColor,
                  ),
                ),
                const SizedBox(width: Space.md),
                Expanded(
                  child: AnimatedDefaultTextStyle(
                    duration: Motion.fast,
                    curve: Motion.curve,
                    style: TextStyle(
                      fontSize: 13.5,
                      color: fg,
                      fontWeight:
                          active ? FontWeight.w600 : FontWeight.w400,
                    ),
                    child: Text(widget.section.title),
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

class _StatusBar extends StatelessWidget {
  const _StatusBar();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 30,
      padding: const EdgeInsets.symmetric(horizontal: Space.md),
      decoration: const BoxDecoration(
        color: Palette.sidebar,
        border: Border(top: BorderSide(color: Palette.border)),
      ),
      child: Row(
        children: <Widget>[
          Text('空闲',
              style: const TextStyle(fontSize: 11.5, color: Palette.textDim)),
          const SizedBox(width: Space.lg),
          Icon(
            AppState.cookies.ok ? Icons.key : Icons.key_off,
            size: 12,
            color: AppState.cookies.ok ? Palette.success : Palette.textFaint,
          ),
          const SizedBox(width: Space.xs),
          Text(
            AppState.cookies.ok
                ? '登录态：${AppState.cookies.loaded.length} 个站点'
                : '登录态：未载入',
            style: const TextStyle(fontSize: 11.5, color: Palette.textDim),
          ),
          const Spacer(),
          Text('保存到 movie · v1.8.19.4',
              style: const TextStyle(fontSize: 11.5, color: Palette.textDim)),
        ],
      ),
    );
  }
}
