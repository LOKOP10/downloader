import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:vdl_pc/core/cookie_bridge.dart';
import 'package:vdl_pc/core/shared/net/http.dart';
import 'package:vdl_pc/core/shared/site.dart';

void main() {
  test('能从原桌面端的 config.json 解析出各站点 Cookie', () {
    // 测试跑在 vdl_pc/ 下，原桌面端在同级目录。
    final config = File(
      '../douyin-downloader/dist/1.8.19.4/config.json',
    );
    if (!config.existsSync()) {
      markTestSkipped('本机没有原桌面端的 config.json，跳过');
      return;
    }

    final result = CookieBridge.load(explicitPath: config.path);
    expect(result.error, isNull, reason: result.summary);
    expect(result.configPath, config.path);
    expect(result.ok, isTrue, reason: result.summary);

    // 抖音 / X / B站 / 小红书 四家在这份 config 里都有值。
    expect(result.loaded.containsKey(SitePlatform.douyin), isTrue);
    expect(result.loaded.containsKey(SitePlatform.x), isTrue);
    expect(result.loaded.containsKey(SitePlatform.bili), isTrue);
    expect(result.loaded.containsKey(SitePlatform.xhs), isTrue);

    // 注入到全局 Http 出口后，解析器就能直接用。
    expect(AppHttp.cookies[SitePlatform.bili], isNotNull);
    expect(AppHttp.cookies[SitePlatform.bili]!.isNotEmpty, isTrue);
  });

  test('config.json 不存在时返回未找到，不抛异常', () {
    final result = CookieBridge.load(explicitPath: '/no/such/config.json');
    expect(result.ok, isFalse);
    expect(result.loaded.isEmpty, isTrue);
  });
}
