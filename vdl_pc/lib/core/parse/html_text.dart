/// 一点点 HTML 文本处理（Dart 没有内置的 html.unescape）。
///
/// 只用在这三个站点的解析里：og:title / 上传者昵称这些字段里会出现
/// `&amp;` `&#39;` 这类实体，不还原的话文件名里会留着乱码。
abstract final class HtmlText {
  static const _named = <String, String>{
    'amp': '&',
    'lt': '<',
    'gt': '>',
    'quot': '"',
    'apos': "'",
    'nbsp': ' ',
    'ensp': ' ',
    'emsp': ' ',
    'middot': '·',
    'hellip': '…',
    'mdash': '—',
    'ndash': '–',
    'ldquo': '“',
    'rdquo': '”',
    'lsquo': '‘',
    'rsquo': '’',
    'copy': '©',
  };

  static final _entityRe = RegExp(r'&(#x?[0-9a-fA-F]+|[a-zA-Z]+);');

  /// 还原命名实体与数字实体（`&#39;` / `&#x27;`）。
  static String unescape(String text) {
    if (text.isEmpty || !text.contains('&')) return text;
    return text.replaceAllMapped(_entityRe, (match) {
      final token = match.group(1)!;
      if (token.startsWith('#')) {
        final hex = token.length > 1 && (token[1] == 'x' || token[1] == 'X');
        final code = int.tryParse(
          hex ? token.substring(2) : token.substring(1),
          radix: hex ? 16 : 10,
        );
        if (code == null || code <= 0) return match.group(0)!;
        return String.fromCharCode(code);
      }
      return _named[token.toLowerCase()] ?? match.group(0)!;
    });
  }

  /// 去掉标签（详情块里标题/作者常包着 `<a>` `<span>`），再还原实体。
  static String stripTags(String html) =>
      unescape(html.replaceAll(RegExp(r'<[^>]*>'), ' '))
          .replaceAll(RegExp(r'\s+'), ' ')
          .trim();
}
