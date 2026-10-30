import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/features/home/data/clipboard_sniffer.dart';
import 'package:vdl/features/home/ui/widgets/result_card.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/shared/site.dart';
import 'package:vdl/design/theme_controller.dart';
import 'package:vdl/app.dart';
import 'package:vdl/design/widgets/app_bars.dart';

/// 点底栏页签。不能用 find.text 直接找：页面标题与页签文字同名
/// （任务页标题也叫「任务」），会命中两个。
Future<void> tapTab(WidgetTester tester, String label) async {
  await tester.tap(find.descendant(
    of: find.byType(AppTabBar),
    matching: find.text(label),
  ));
  await tester.pumpAndSettle();
}

/// 把剪贴板内容替换成指定文本（测试里没有真实剪贴板）。
void mockClipboard(WidgetTester tester, String text) {
  final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  messenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
    if (call.method == 'Clipboard.getData') return {'text': text};
    return null;
  });
  addTearDown(() => messenger.setMockMethodCallHandler(SystemChannels.platform, null));
}

void main() {
  testWidgets('图集结果卡不崩（qualities 为空）', (tester) async {
    // 真机回归：图集时 qualities 为空，曾按索引取档导致越界、整卡渲染成灰块
    await tester.pumpWidget(MaterialApp(
      theme: appLightTheme(),
      home: Scaffold(
        body: HomeResultCard(
          media: const MediaInfo(
            platform: SitePlatform.douyin,
            title: '测试图集',
            author: '作者',
            images: ['https://example.com/1.jpg', 'https://example.com/2.jpg'],
          ),
          qualityIndex: 0,
          onPickQuality: () {},
          onDownload: () {},
          onPreview: () {},
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('测试图集'), findsOneWidget);
    expect(find.textContaining('选择图片下载'), findsOneWidget);
    expect(find.text('图集 2 张'), findsOneWidget);
  });

  group('剪贴板嗅探', () {
    test('不在输入时总是可以替换（含已有旧链接的情况）', () {
      // 关键回归：手动粘贴过一条链接后，复制新链接回来也要能被替换
      expect(
          ClipboardSniffer.canReplace(
              inputFocused: false, current: 'https://旧链接'),
          isTrue);
      expect(ClipboardSniffer.canReplace(inputFocused: false, current: ''), isTrue);
    });

    test('正在输入框里打字时不硬覆盖', () {
      expect(
          ClipboardSniffer.canReplace(
              inputFocused: true, current: '半截文字'),
          isFalse);
      expect(ClipboardSniffer.canReplace(inputFocused: true, current: ''), isTrue);
    });

    testWidgets('进前台嗅探到链接会自动回填', (tester) async {
      tester.view.physicalSize = const Size(1080, 4200);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);
      mockClipboard(tester, 'v.douyin.com/jx6dSylJqFU/ gbA:/ 12/12 :1pm d@N.jp');

      await tester.pumpWidget(const VdlApp());
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(find.byType(TextField).first);
      expect(field.controller?.text, 'https://v.douyin.com/jx6dSylJqFU/');
      expect(find.textContaining('已从剪贴板识别到抖音'), findsOneWidget);
    });

    testWidgets('粘贴按钮从剪贴板取链接填入', (tester) async {
      tester.view.physicalSize = const Size(1080, 4200);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);
      mockClipboard(tester, '看看这个 https://v.douyin.com/abcXyz/ 复制打开抖音');

      await tester.pumpWidget(const VdlApp());
      await tester.pumpAndSettle();
      // 清掉嗅探自动填的内容，模拟用户清空后手动点粘贴
      await tester.enterText(find.byType(TextField).first, '');
      await tester.pumpAndSettle();

      await tester.tap(find.text('粘贴'));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(find.byType(TextField).first);
      expect(field.controller?.text, 'https://v.douyin.com/abcXyz/');
    });

    testWidgets('别的网站的链接不嗅探（只认这 8 个平台）', (tester) async {
      // 真机反馈：剪贴板里随手复制的网页链接也会弹「已识别到链接」
      mockClipboard(tester, 'https://example.com/some/docs/page');
      String? got;
      // 直接调 checkNow：start() 依赖 postFrameCallback，没有 widget 树时不会触发
      final sniffer = ClipboardSniffer(onDetected: (url, platform) => got = url);
      await sniffer.checkNow();
      expect(got, isNull);
    });

    testWidgets('恰好是整段文字、但没有本站链接时也不嗅探', (tester) async {
      mockClipboard(tester, '明天 10 点开会，记得带电脑（不是链接）');
      String? got;
      final sniffer = ClipboardSniffer(onDetected: (url, platform) => got = url);
      await sniffer.checkNow();
      expect(got, isNull);
    });

    testWidgets('Iwara / Pornhub / hanime1 的链接能嗅探', (tester) async {
      const cases = <String, SitePlatform>{
        'https://www.iwara.tv/video/qR94gLGO9VGEqL': SitePlatform.iwara,
        'https://www.pornhub.com/view_video.php?viewkey=6a46ec3e259f3':
            SitePlatform.pornhub,
        'https://hanime1.me/watch?v=408885': SitePlatform.hanime1,
      };
      for (final entry in cases.entries) {
        mockClipboard(tester, entry.key);
        String? got;
        SitePlatform? gotPlatform;
        final sniffer = ClipboardSniffer(onDetected: (url, platform) {
          got = url;
          gotPlatform = platform;
        });
        await sniffer.checkNow();
        expect(got, entry.key, reason: entry.key);
        expect(gotPlatform, entry.value, reason: entry.key);
      }
    });
  });

  testWidgets('四个页签可切换且内容正确', (tester) async {
    // 高屏：设置页现在有 9 个账号行，一屏放得下才能直接断言「外观」
    tester.view.physicalSize = const Size(1080, 4200);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const VdlApp());
    await tester.pumpAndSettle();

    expect(find.text('开始解析'), findsOneWidget);

    await tapTab(tester, '任务');
    expect(find.text('还没有任务'), findsOneWidget);

    await tapTab(tester, '书架');
    expect(find.text('本地漫画'), findsOneWidget);

    await tapTab(tester, '设置');
    expect(find.text('同时下载封面'), findsOneWidget);
    expect(find.text('外观'), findsOneWidget);
    expect(find.text('支持的平台'), findsOneWidget);
  });

  testWidgets('深色主题可切换并立即生效', (tester) async {
    // 同上一项：设置页变长，用高屏视口让「深色」直接可见
    tester.view.physicalSize = const Size(1080, 4200);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const VdlApp());
    await tester.pumpAndSettle();
    await tapTab(tester, '设置');
    addTearDown(() => themeModeNotifier.value = ThemeMode.system);
    await tester.tap(find.text('深色'));
    await tester.pumpAndSettle();

    final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.themeMode, ThemeMode.dark);
  });

  testWidgets('粘贴媒体直链后能解析出结果', (tester) async {
    // 高屏：一屏能放下输入卡 + 平台宫格 + 结果卡，避免断言依赖滚动位置
    tester.view.physicalSize = const Size(1080, 4200);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const VdlApp());
    await tester.pumpAndSettle();

    // 空输入时先给提示，不假装解析
    await tester.tap(find.text('开始解析'));
    await tester.pumpAndSettle();
    expect(find.text('先粘贴一个作品链接'), findsOneWidget);

    await tester.enterText(
        find.byType(TextField).first, 'https://example.com/demo.mp4');
    await tester.tap(find.text('开始解析'));
    await tester.pumpAndSettle(const Duration(seconds: 1));

    expect(find.text('解析结果'), findsOneWidget);
    expect(find.text('原始文件（不分档）'), findsOneWidget);
    expect(find.text('开始下载'), findsOneWidget);
  });
}
