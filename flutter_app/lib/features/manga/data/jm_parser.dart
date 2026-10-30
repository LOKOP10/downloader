import 'package:vdl/features/manga/data/jm_client.dart';

/// 禁漫链接识别与解析：/album/<id> 或 /photo/<id>，也认纯车号（350234 / JM350234）。
abstract final class JmParser {
  static final _albumRe = RegExp(r'/album/(\d+)', caseSensitive: false);
  static final _photoRe = RegExp(r'/photo/(\d+)', caseSensitive: false);

  /// 整段输入只有一个车号（允许 JM 前缀与空格）。
  static final _bareIdRe =
      RegExp(r'^(?:jm)?\s*(\d{3,8})$', caseSensitive: false);

  /// 是否像禁漫输入（首页据此走专用解析）。
  static bool looksLikeJm(String text) =>
      _albumRe.hasMatch(text) ||
      _photoRe.hasMatch(text) ||
      bareIdOf(text) != null;

  /// 整段输入只是一个车号时返回它；否则 null。
  ///
  /// 禁漫用户习惯直接报车号而不是贴链接，所以首页必须认这种输入。
  static String? bareIdOf(String text) =>
      _bareIdRe.firstMatch(text.trim())?.group(1);

  /// 车号补成一条可展示的链接（解析本身只用 id）。
  static String linkFor(String id) => 'https://18comic.vip/album/' + id;

  /// 从链接或车号取 id。
  static String? idOf(String url) =>
      _albumRe.firstMatch(url)?.group(1) ??
      _photoRe.firstMatch(url)?.group(1) ??
      bareIdOf(url);

  /// 解析一本：单话链接会自动变成「只有一章」的本子。
  static Future<JmAlbum> parse(String url) async {
    final id = idOf(url);
    if (id == null) {
      throw JmException('没识别到禁漫车号：可以直接填车号（如 350234），'
          '或用 /album/数字、/photo/数字 形式的链接');
    }
    return JmClient.instance.album(id);
  }
}
