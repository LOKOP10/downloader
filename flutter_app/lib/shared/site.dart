/// 站点枚举。**纯 Dart，不依赖 Flutter** —— 解析器、下载器、命令行实测
/// 都要用它在无 UI 环境下跑通。
enum SitePlatform {
  douyin('抖音', '抖'),
  xhs('小红书', '红'),
  bili('B站', 'B'),
  x('X', 'X'),
  iwara('Iwara', 'I'),
  pornhub('Pornhub', 'P'),
  hanime1('hanime1', 'H'),
  jm('禁漫', '禁');

  const SitePlatform(this.label, this.glyph);

  final String label;
  final String glyph;
}
