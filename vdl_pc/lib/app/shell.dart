import 'package:flutter/material.dart';

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
                  onSelect: (int i) => setState(() => _index = i),
                ),
                const VerticalDivider(width: 1, thickness: 1),
                Expanded(
                  child: ColoredBox(
                    color: Palette.bg,
                    // IndexedStack 保留各页滚动位置与输入内容，切页不丢状态。
                    child: IndexedStack(
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
              itemBuilder: (BuildContext context, int i) {
                final Section s = kSections[i];
                final bool active = i == index;
                return Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(Space.sm),
                      onTap: () => onSelect(i),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: Space.md, vertical: 10),
                        decoration: BoxDecoration(
                          color: active ? Palette.surfaceHover : null,
                          borderRadius: BorderRadius.circular(Space.sm),
                        ),
                        child: Row(
                          children: <Widget>[
                            Icon(s.icon,
                                size: 18,
                                color: active
                                    ? Palette.accent
                                    : Palette.textDim),
                            const SizedBox(width: Space.md),
                            Text(s.title,
                                style: TextStyle(
                                  fontSize: 13.5,
                                  color: active
                                      ? Palette.text
                                      : Palette.textDim,
                                  fontWeight: active
                                      ? FontWeight.w600
                                      : FontWeight.w400,
                                )),
                          ],
                        ),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
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
        children: const <Widget>[
          Text('空闲', style: TextStyle(fontSize: 11.5, color: Palette.textDim)),
          Spacer(),
          Text('保存到 movie · v1.8.19.4',
              style: TextStyle(fontSize: 11.5, color: Palette.textDim)),
        ],
      ),
    );
  }
}
