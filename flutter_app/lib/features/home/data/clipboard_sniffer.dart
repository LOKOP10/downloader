import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:vdl/features/parse/parser_registry.dart';
import 'package:vdl/shared/links.dart';
import 'package:vdl/shared/site.dart';

/// 剪贴板嗅探。
///
/// 与旧 Kotlin 端行为一致：**回到前台时**读一次剪贴板，识别到链接就回填输入框。
/// Flutter 没有跨平台的剪贴板变化监听，这里用「生命周期 resumed + 首次进入」
/// 两个时机覆盖绝大多数场景（从抖音复制 → 切回本 App 即触发）。
/// Android 10+ 在后台读剪贴板会被系统拦截，所以只在恢复前台时读。
class ClipboardSniffer with WidgetsBindingObserver {
  ClipboardSniffer({required this.onDetected});

  /// 识别到**本站支持的站点**的链接时回调（url 已补全协议头）。
  ///
  /// 只在这 8 个平台（抖音 / 小红书 / B站 / X / Iwara / Pornhub / hanime1 / 禁漫）
  /// 命中时才回调；别的网站（文档、代码仓库、新闻…）一律静默，不当噪声。
  final void Function(String url, SitePlatform platform) onDetected;

  String _lastText = '';
  bool _started = false;

  void start() {
    if (_started) return;
    _started = true;
    WidgetsBinding.instance.addObserver(this);
    // 首次进入也检查一次：用户常常「先复制，再打开 App」
    WidgetsBinding.instance.addPostFrameCallback((_) => checkNow());
  }

  void stop() {
    if (!_started) return;
    _started = false;
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) checkNow();
  }

  /// 读剪贴板原文（空则 null）。
  Future<String?> read() async {
    try {
      final data = await Clipboard.getData(Clipboard.kTextPlain);
      final text = data?.text?.trim() ?? '';
      return text.isEmpty ? null : text;
    } catch (_) {
      return null;
    }
  }

  /// 主动检查一次：同一条文本只处理一次。
  ///
  /// 关键：**先认站点、再决定要不要提示**。`LinkDetect.extract` 对任何长得像
  /// 域名的片段都会返回结果（它只是「抠 URL」，不保证是本 App 能下），以前拿它
  /// 当判据，结果剪贴板里随便一条网页链接都会弹「已识别到链接」——
  /// 现在只认 `ParserRegistry.supports` 里的站点。
  Future<void> checkNow() async {
    final text = await read();
    if (text == null || text == _lastText) return;
    _lastText = text;
    final url = LinkDetect.extract(text);
    if (url == null) return;
    final platform = LinkDetect.platformOf(url);
    if (platform == null || !ParserRegistry.supports(platform)) return;
    onDetected(url, platform);
  }

  /// 被手动粘贴处理过的文本，不再触发自动回填提示。
  void markHandled(String text) => _lastText = text;

  /// 输入框正在被编辑时不要硬覆盖（会把用户打到一半的字冲掉），
  /// 其余情况（含已有上一次的链接）都允许用新链接替换。
  ///
  /// 之前的实现要求「输入框为空或内容等于上次自动填入的值」，结果你手动粘贴
  /// 过一条视频链接之后，再复制图集链接回来就被拦掉了 —— 这里改成以「是否在输入」为准。
  static bool canReplace({required bool inputFocused, required String current}) {
    if (!inputFocused) return true;
    return current.trim().isEmpty;
  }
}
