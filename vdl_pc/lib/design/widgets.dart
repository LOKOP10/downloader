import 'package:flutter/material.dart';

import 'palette.dart';

/// 页头：标题 + 副标题，所有页面统一用它开头。
class PageHeading extends StatelessWidget {
  const PageHeading({super.key, required this.title, this.subtitle});

  final String title;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(title,
            style: const TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w600,
              color: Palette.text,
            )),
        if (subtitle != null) ...<Widget>[
          const SizedBox(height: Space.xs),
          Text(subtitle!,
              style: const TextStyle(fontSize: 13, color: Palette.textDim)),
        ],
      ],
    );
  }
}

/// 内容卡片。
class Panel extends StatelessWidget {
  const Panel({super.key, required this.child, this.padding});

  final Widget child;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding ?? const EdgeInsets.all(Space.lg),
      decoration: BoxDecoration(
        color: Palette.surface,
        borderRadius: BorderRadius.circular(Space.radius),
        border: Border.all(color: Palette.border),
      ),
      child: child,
    );
  }
}

/// 尚未实现的功能占位。
class TodoPanel extends StatelessWidget {
  const TodoPanel({super.key, required this.items});

  final List<String> items;

  @override
  Widget build(BuildContext context) {
    return Panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: const <Widget>[
              Icon(Icons.construction, size: 16, color: Palette.warning),
              SizedBox(width: Space.sm),
              Text('本页待实现',
                  style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: Palette.warning)),
            ],
          ),
          const SizedBox(height: Space.md),
          ...items.map((String line) => Padding(
                padding: const EdgeInsets.only(bottom: Space.xs),
                child: Text('· $line',
                    style: const TextStyle(
                        fontSize: 13, color: Palette.textDim, height: 1.5)),
              )),
        ],
      ),
    );
  }
}
