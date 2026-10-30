import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/features/settings/data/account_store.dart';
import 'package:vdl/shared/site.dart';

/// 登录对话框：粘贴 Cookie 即完成登录（与旧 Kotlin 端的做法一致）。
///
/// 不做站内账号密码登录 —— 那要维护各站点的登录协议，容易失效；
/// 从浏览器复制 Cookie 一次可用很久，也便于随时清除。
class LoginDialog extends StatefulWidget {
  const LoginDialog({super.key, required this.platform});

  final SitePlatform platform;

  @override
  State<LoginDialog> createState() => _LoginDialogState();
}

class _LoginDialogState extends State<LoginDialog> {
  late final TextEditingController _controller;
  String? _message;
  bool _messageIsError = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(
        text: AccountStore.instance.cookieOf(widget.platform) ?? '');
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (text.isEmpty) {
      setState(() {
        _message = '剪贴板里没有文字';
        _messageIsError = true;
      });
      return;
    }
    setState(() {
      _controller.text = text;
      _message = '已从剪贴板粘贴，点「保存」生效';
      _messageIsError = false;
    });
  }

  Future<void> _save() async {
    final text = _controller.text.trim();
    if (text.isEmpty) {
      setState(() {
        _message = '先粘贴 Cookie 再保存';
        _messageIsError = true;
      });
      return;
    }
    await AccountStore.instance.save(widget.platform, text);
    if (!mounted) return;
    final hits = AccountStore.authHits(widget.platform, text);
    setState(() {
      _messageIsError = false;
      _message = hits.isEmpty
          ? '已保存，但没找到登录凭证（${widget.platform.label} 需要 '
              '${AccountStore.authKeyNames(widget.platform).join(' 或 ')} 里的任意一项），可能仍是未登录'
          : '已登录：${widget.platform.label}（命中 ${hits.join('、')}）';
    });
  }

  /// 各站点要填哪几个键，直接给一张「照着抄」的示例。
  static String _cookieHint(SitePlatform platform) => switch (platform) {
        SitePlatform.x => 'auth_token=…; ct0=…（两项都要）',
        SitePlatform.douyin => 'sessionid=…; ttwid=…',
        SitePlatform.xhs => 'web_session=…; a1=…',
        SitePlatform.bili => 'SESSDATA=…; bili_jct=…',
        _ => '把浏览器里的整行 Cookie 粘进来',
      };

  Future<void> _clear() async {
    await AccountStore.instance.clear(widget.platform);
    if (!mounted) return;
    setState(() {
      _controller.clear();
      _message = '已清除登录状态';
      _messageIsError = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return AlertDialog(
      backgroundColor: palette.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      title: Text('登录 ${widget.platform.label}',
          style: TextStyle(color: palette.titleText, fontSize: 16)),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '从浏览器打开 ${widget.platform.label} 并登录，按 F12 → Network → 任一请求 → '
              '复制 Request Headers 里的 Cookie，粘到下面即可。',
              style: TextStyle(color: palette.hint, fontSize: 12, height: 1.5),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _controller,
              maxLines: 6,
              minLines: 3,
              style: TextStyle(color: palette.titleText, fontSize: 12),
              decoration: InputDecoration(
                hintText: _cookieHint(widget.platform),
                hintStyle: TextStyle(color: palette.hint, fontSize: 12),
                filled: true,
                fillColor: palette.pageBg,
                isDense: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: palette.divider),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: palette.divider),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                TextButton.icon(
                  onPressed: _paste,
                  icon: Icon(Icons.content_paste, size: 16, color: palette.accent),
                  label: Text('从剪贴板粘贴',
                      style: TextStyle(color: palette.accent, fontSize: 13)),
                ),
                const Spacer(),
                TextButton(
                  onPressed: _clear,
                  child: Text('清除',
                      style: TextStyle(color: palette.danger, fontSize: 13)),
                ),
              ],
            ),
            if (_message != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  _message!,
                  style: TextStyle(
                    color: _messageIsError ? palette.danger : palette.success,
                    fontSize: 12,
                    height: 1.4,
                  ),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text('关闭', style: TextStyle(color: palette.labelText)),
        ),
        FilledButton(
          onPressed: _save,
          style: FilledButton.styleFrom(
            backgroundColor: palette.accent,
            foregroundColor: Colors.white,
          ),
          child: const Text('保存'),
        ),
      ],
    );
  }
}
