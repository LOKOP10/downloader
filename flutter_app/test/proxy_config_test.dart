import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/shared/net/proxy_config.dart';

void main() {
  group('代理地址规范化', () {
    test('标准 host:port 原样保留', () {
      expect(ProxyConfig.normalize('127.0.0.1:7890'), '127.0.0.1:7890');
      expect(ProxyConfig.normalize('192.168.1.5:1080'), '192.168.1.5:1080');
    });

    test('带 scheme / 末尾斜杠也能救回来', () {
      expect(ProxyConfig.normalize('http://127.0.0.1:7890'), '127.0.0.1:7890');
      expect(ProxyConfig.normalize('https://127.0.0.1:7890/'), '127.0.0.1:7890');
      expect(ProxyConfig.normalize('HTTP://10.0.0.2:8080/'), '10.0.0.2:8080');
    });

    test('只填端口号时补成 127.0.0.1（Clash 界面就只显示端口）', () {
      expect(ProxyConfig.normalize('1234'), '127.0.0.1:1234');
      expect(ProxyConfig.normalize(' 7890 '), '127.0.0.1:7890');
      expect(ProxyConfig.normalize('0'), isNull);
      expect(ProxyConfig.normalize('70000'), isNull);
    });

    test('前后空白忽略', () {
      expect(ProxyConfig.normalize('  127.0.0.1:7890  '), '127.0.0.1:7890');
    });

    test('空值与非法值返回 null（= 直连）', () {
      expect(ProxyConfig.normalize(null), isNull);
      expect(ProxyConfig.normalize(''), isNull);
      expect(ProxyConfig.normalize('   '), isNull);
      expect(ProxyConfig.normalize('127.0.0.1'), isNull);
      expect(ProxyConfig.normalize('host:abc'), isNull);
      expect(ProxyConfig.normalize('host:0'), isNull);
      expect(ProxyConfig.normalize('host:70000'), isNull);
      expect(ProxyConfig.normalize(':7890'), isNull);
    });
  });
}
