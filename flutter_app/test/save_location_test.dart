import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vdl/features/download/data/download_location.dart';
import 'package:vdl/features/manga/data/manga_location.dart';
import 'package:vdl/features/settings/ui/widgets/save_location_dialog.dart';

/// 保存位置的两套配置必须互不影响。
///
/// 真机回归：设置页点「漫画保存位置」时，对话框内部写死的是
/// `DownloadLocation.save`，于是**视频下载目录**被改成了用户填的漫画路径、
/// 而漫画目录一直是默认值 —— 视频被存进「漫画」文件夹，用户按自己设的
/// `/storage/emulated/0/Picture` 怎么都找不到。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues(<String, Object>{}));

  test('写视频目录不会动漫画目录', () async {
    await DownloadLocation.save('/storage/emulated/0/Picture');
    expect(await DownloadLocation.current(), '/storage/emulated/0/Picture');

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('download.dir'), '/storage/emulated/0/Picture');
    expect(prefs.getString('manga.dir'), isNull,
        reason: '漫画目录没被设置，应保持「未自定义」');
    // 漫画的默认值跟随视频目录（这是设计如此），但配置键本身没被写脏
    expect(await MangaLocation.isCustom(), isFalse);
    expect((await MangaLocation.current()).endsWith('漫画'), isTrue);
  });

  test('写漫画目录不会动视频目录', () async {
    await DownloadLocation.save('/storage/emulated/0/Picture');
    await MangaLocation.save('/storage/emulated/0/漫画');

    expect(await DownloadLocation.current(), '/storage/emulated/0/Picture');
    expect(await MangaLocation.current(), '/storage/emulated/0/漫画');

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('download.dir'), '/storage/emulated/0/Picture');
    expect(prefs.getString('manga.dir'), '/storage/emulated/0/漫画');
  });

  test('漫画目录保存前会规范化（漏斜杠 / 结尾斜杠）', () async {
    await MangaLocation.save('storage/emulated/0/漫画/');
    expect(await MangaLocation.current(), '/storage/emulated/0/漫画');
  });

  group('标准目录名提示', () {
    test('Picture（少个 s）会提示改成 Pictures', () {
      final hint = DownloadLocation.suggestStandardDir('/storage/emulated/0/Picture');
      expect(hint, isNotNull);
      expect(hint, contains('Pictures'));
    });

    test('大小写不对也提示（download → Download）', () {
      expect(
        DownloadLocation.suggestStandardDir('/storage/emulated/0/download/视频'),
        contains('Download'),
      );
    });

    test('已经写对就不啰嗦', () {
      expect(DownloadLocation.suggestStandardDir('/storage/emulated/0/Pictures'), isNull);
      expect(
          DownloadLocation.suggestStandardDir('/storage/emulated/0/Pictures/视频下载器'),
          isNull);
    });

    test('自定义目录名不提示（用户自己的文件夹）', () {
      expect(DownloadLocation.suggestStandardDir('/storage/emulated/0/我的视频'), isNull);
      expect(DownloadLocation.suggestStandardDir('/storage/emulated/0/漫画'), isNull);
    });

    test('应用私有目录与 Windows 路径不提示', () {
      expect(
          DownloadLocation.suggestStandardDir(
              '/storage/emulated/0/Android/data/com.xd.vdl.flutter/files/Downloads'),
          isNull);
      expect(DownloadLocation.suggestStandardDir('C:\\Users\\x\\Downloads'), isNull);
    });
  });

  test('清空设置就回到默认（键被删掉）', () async {
    await DownloadLocation.save('/storage/emulated/0/Picture');
    await DownloadLocation.save('');
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('download.dir'), isNull);
  });

  testWidgets('对话框写入的是调用方指定的配置，绝不自己动视频目录', (tester) async {
    // 用户点「漫画保存位置」时，保存必须落到 manga.dir。
    // 之前对话框内部写死 DownloadLocation.save，等于把视频目录改成了漫画路径。
    // 注意：这里全程不碰真实文件系统（路径只是字符串、validator 注入为「可写」），
    // 因为 testWidgets 跑在 fake async 里，真实 IO 的 Future 永远不会完成。
    const dirPath = '/storage/emulated/0/漫画';
    final saved = <String>[];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => ElevatedButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => SaveLocationDialog(
                title: '漫画保存位置',
                initialPath: dirPath,
                save: (path) async => saved.add(path),
                defaultPath: () async => dirPath,
                // 注入校验器：不碰真实文件系统，测试稳定
                validator: (_) async => null,
              ),
            ),
            child: const Text('打开'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(saved.length, 1, reason: '点一次保存只该写一次');
    expect(saved.single, dirPath);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('download.dir'), isNull,
        reason: '对话框不能顺手改视频下载目录');
  });

  testWidgets('点「选择文件夹」会把选中的目录直接保存下来', (tester) async {
    const picked = '/storage/emulated/0/Pictures/视频下载器';
    final saved = <String>[];
    await tester.pumpWidget(_dialogHost(
      saved: saved,
      initialPath: '/旧路径',
      defaultPath: '/默认',
      directoryPicker: () async => picked,
    ));
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('选择文件夹'));
    await tester.pumpAndSettle();
    expect(saved.single, picked, reason: '选完直接按选中目录保存');
  });

  testWidgets('选择器返回 null（云盘 / 无法转路径）时不保存，并说明原因', (tester) async {
    final saved = <String>[];
    await tester.pumpWidget(_dialogHost(
      saved: saved,
      initialPath: '/旧路径',
      defaultPath: '/默认',
      directoryPicker: () async => null,
    ));
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('选择文件夹'));
    await tester.pumpAndSettle();
    expect(saved, isEmpty);
    expect(find.textContaining('没有选到可写目录'), findsOneWidget);
  });
}

/// 把对话框挂在一个按钮后面（和真实调用方式一致）。
Widget _dialogHost({
  required List<String> saved,
  required String initialPath,
  required String defaultPath,
  Future<String?> Function()? directoryPicker,
}) {
  return MaterialApp(
    home: Scaffold(
      body: Builder(
        builder: (context) => ElevatedButton(
          onPressed: () => showDialog<void>(
            context: context,
            builder: (_) => SaveLocationDialog(
              initialPath: initialPath,
              save: (path) async => saved.add(path),
              defaultPath: () async => defaultPath,
              validator: (_) async => null,
              directoryPicker: directoryPicker,
            ),
          ),
          child: const Text('打开'),
        ),
      ),
    ),
  );
}
