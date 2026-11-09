import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vdl_pc/app/sections.dart';
import 'package:vdl_pc/app/shell.dart';
import 'package:vdl_pc/design/theme.dart';

void main() {
  testWidgets('壳层能构建，侧栏列出全部导航项', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(theme: buildTheme(), home: const Shell()),
    );
    await tester.pumpAndSettle();

    expect(find.text('视频下载器'), findsOneWidget);
    expect(kSections.length, 9);
    for (final Section section in kSections) {
      expect(
        find.text(section.title),
        findsWidgets,
        reason: '侧栏缺少导航项：${section.title}',
      );
    }
  });

  testWidgets('点击导航项能切换到对应页面', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(theme: buildTheme(), home: const Shell()),
    );
    await tester.pumpAndSettle();

    // 默认在「视频解析」页
    expect(find.text('粘贴分享链接或分享文案，选画质，下载。解析结果显示在下方，不用切页。'), findsOneWidget);

    // 切到「本地视频」
    await tester.tap(find.text('本地视频').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('只收下载目录这一层'), findsOneWidget);
  });
}
