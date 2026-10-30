import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

/// 各站点 Cookie 的持久化与读取。
///
/// 抖音在游客态就能解析分享页；登录后能拿到更完整的作品信息（关注/私密等），
/// 所以这里做成可选增强：有 Cookie 就带上，没有也照常工作。
class AccountStore extends ChangeNotifier {
  AccountStore._();

  static final AccountStore instance = AccountStore._();

  static const _prefix = 'cookie.';

  final Map<SitePlatform, String> _cookies = {};
  bool _loaded = false;

  bool get loaded => _loaded;

  /// 启动时读一次：把持久化的 Cookie 灌进 HTTP 层。
  Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      for (final platform in SitePlatform.values) {
        final value = prefs.getString('$_prefix${platform.name}') ?? '';
        if (value.isEmpty) continue;
        _cookies[platform] = value;
        AppHttp.cookies[platform] = value;
      }
    } catch (error) {
      debugPrint('读取登录状态失败：$error');
    }
    _loaded = true;
    notifyListeners();
  }

  String? cookieOf(SitePlatform platform) => _cookies[platform];

  /// 是否已登录：有 Cookie 且含登录态关键字段。
  bool loggedIn(SitePlatform platform) {
    final cookie = _cookies[platform];
    return cookie != null && cookie.isNotEmpty && looksLoggedIn(platform, cookie);
  }

  /// 保存 Cookie（同时写进 HTTP 层与磁盘）。
  Future<void> save(SitePlatform platform, String rawCookie) async {
    final cookie = normalize(rawCookie);
    if (cookie.isEmpty) {
      await clear(platform);
      return;
    }
    _cookies[platform] = cookie;
    AppHttp.cookies[platform] = cookie;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('$_prefix${platform.name}', cookie);
  }

  Future<void> clear(SitePlatform platform) async {
    _cookies.remove(platform);
    AppHttp.cookies.remove(platform);
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('$_prefix${platform.name}');
  }

  /// 把粘贴进来的文本整理成一行 Cookie：
  /// 去掉 "Cookie:" 前缀、换行拼成 "; "、去掉多余空格。
  static String normalize(String raw) {
    var text = raw.trim();
    if (text.toLowerCase().startsWith('cookie:')) {
      text = text.substring(7).trim();
    }
    text = text.replaceAll(RegExp(r'\r?\n+'), '; ');
    text = text.replaceAll(RegExp(r';\s*;'), ';');
    text = text.replaceAll(RegExp(r'\s{2,}'), ' ');
    return text.trim();
  }

  /// 各站点的登录态关键字段（有一个即认为已登录）。
  static const _authKeys = <SitePlatform, List<String>>{
    SitePlatform.douyin: ['sessionid', 'sessionid_ss', 'sid_guard', 'passport_csrf_token'],
    SitePlatform.xhs: ['web_session', 'a1', 'gid'],
    SitePlatform.bili: ['SESSDATA', 'bili_jct'],
    SitePlatform.x: ['auth_token', 'ct0'],
    SitePlatform.iwara: ['token', 'session'],
    SitePlatform.pornhub: ['il', 'ss'],
    SitePlatform.hanime1: ['cf_clearance', 'session'],
    SitePlatform.jm: ['AVS', 'session'],
  };

  /// 判断 Cookie 里是否带登录凭证（只是启发式，不做真实校验）。
  static bool looksLoggedIn(SitePlatform platform, String cookie) {
    final keys = _authKeys[platform] ?? const <String>[];
    final lower = cookie.toLowerCase();
    return keys.any((k) => lower.contains('${k.toLowerCase()}='));
  }

  /// 该站点要求的登录字段名（登录对话框的提示文案用）。
  static List<String> authKeyNames(SitePlatform platform) =>
      List<String>.from(_authKeys[platform] ?? const <String>[]);

  /// 从 Cookie 里挑出命中的登录字段名，用于保存后的提示。
  static List<String> authHits(SitePlatform platform, String cookie) {
    final keys = _authKeys[platform] ?? const <String>[];
    final lower = cookie.toLowerCase();
    return keys.where((k) => lower.contains('${k.toLowerCase()}=')).toList();
  }
}
