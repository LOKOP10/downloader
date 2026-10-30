import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vdl/features/settings/data/account_store.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppHttp.cookies.remove(SitePlatform.douyin);
  });

  test('整理粘贴进来的 Cookie（去前缀、换行拼成一行）', () {
    const raw = 'Cookie: sessionid=abc; ttwid=xyz\npassport_csrf_token=t1';
    final v = AccountStore.normalize(raw);
    expect(v, 'sessionid=abc; ttwid=xyz; passport_csrf_token=t1');
  });

  test('识别登录凭证', () {
    expect(AccountStore.looksLoggedIn(SitePlatform.douyin, 'ttwid=1; sessionid=abc'),
        isTrue);
    expect(AccountStore.looksLoggedIn(SitePlatform.douyin, 'ttwid=1'), isFalse);
    expect(AccountStore.authHits(SitePlatform.douyin, 'sid_guard=x; sessionid_ss=y'),
        ['sessionid_ss', 'sid_guard']);
  });

  test('保存后写进 HTTP 层与磁盘，清除后同步移除', () async {
    final store = AccountStore.instance;
    await store.save(SitePlatform.douyin, 'Cookie: sessionid=abc; ttwid=1');
    expect(store.loggedIn(SitePlatform.douyin), isTrue);
    expect(AppHttp.cookies[SitePlatform.douyin], contains('sessionid=abc'));

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('cookie.douyin'), contains('sessionid=abc'));

    await store.clear(SitePlatform.douyin);
    expect(store.loggedIn(SitePlatform.douyin), isFalse);
    expect(AppHttp.cookies.containsKey(SitePlatform.douyin), isFalse);
    expect(prefs.getString('cookie.douyin'), isNull);
  });

  test('启动时能从磁盘恢复', () async {
    SharedPreferences.setMockInitialValues({
      'cookie.douyin': 'sessionid=restored; ttwid=1',
    });
    await AccountStore.instance.load();
    expect(AccountStore.instance.loggedIn(SitePlatform.douyin), isTrue);
    expect(AppHttp.cookies[SitePlatform.douyin], contains('sessionid=restored'));
  });
}
