import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/shared/app_log.dart';

/// 诊断日志页：查看运行日志，一键复制发给开发者。
class LogPage extends StatelessWidget {
  const LogPage({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      backgroundColor: palette.pageBg,
      appBar: AppTopBar(
        title: '诊断日志',
        leading: IconButton(
          icon: Icon(Icons.arrow_back, color: palette.labelText),
          onPressed: () => Navigator.of(context).pop(),
        ),
        actions: [
          ValueListenableBuilder<int>(
            valueListenable: AppLog.instance.revision,
            builder: (context, _, __) => TextButton(
              onPressed: () async {
                if (AppLog.instance.count == 0) {
                  _toast(context, '还没有日志');
                  return;
                }
                await Clipboard.setData(
                    ClipboardData(text: AppLog.instance.export()));
                if (context.mounted) _toast(context, '已复制全部日志，可直接粘贴发送');
              },
              child: Text('复制全部',
                  style: TextStyle(color: palette.accent, fontSize: 13)),
            ),
          ),
          TextButton(
            onPressed: () {
              AppLog.instance.clear();
              _toast(context, '已清空');
            },
            child: Text('清空',
                style: TextStyle(color: palette.danger, fontSize: 13)),
          ),
        ],
      ),
      body: ValueListenableBuilder<int>(
        valueListenable: AppLog.instance.revision,
        builder: (context, _, __) {
          final entries = AppLog.instance.entries;
          if (entries.isEmpty) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text(
                  '还没有日志。\n复现问题后回到这里看，点右上角「复制全部」发给我。',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: palette.hint, fontSize: 13, height: 1.6),
                ),
              ),
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
            itemCount: entries.length,
            itemBuilder: (context, i) {
              final e = entries[entries.length - 1 - i];
              final isError = e.message.contains('失败') ||
                  e.message.contains('错误') ||
                  e.message.contains('异常');
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: SelectableText(
                  e.line,
                  style: TextStyle(
                    color: isError ? palette.danger : palette.titleText,
                    fontSize: 11.5,
                    height: 1.45,
                    fontFamily: 'monospace',
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }

  static void _toast(BuildContext context, String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(message),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 2),
      ));
  }
}
