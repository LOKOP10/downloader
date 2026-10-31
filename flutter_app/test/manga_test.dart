import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vdl/features/manga/ui/manga_reader.dart';
import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_downloader.dart';
import 'package:vdl/features/manga/data/jm_parser.dart';
import 'package:vdl/features/manga/data/jm_scramble.dart';
import 'package:vdl/features/manga/data/manga_book.dart';
import 'package:vdl/features/manga/data/manga_library.dart';

/// 漫画链路的离线回归：协议解密、切片算法、自然排序、书架 JSON。
void main() {
  group('JmClient.decryptBody', () {
    test('按禁漫协议解密（AES-256-ECB + PKCS#7，密钥由时间戳派生）', () {
      // 用同一算法在外部生成的向量：ts=1700000000、secret=185Hcomic3PAPP7R
      const base64Text =
          'dr3s6xKGHzANnth5YmOIVwZY5h5cAL2J8xjB/Yhj1Y87JmSmUH9YMMFzkQ3kuuiE'
          '9q55zmdkL/Tr3JHfdYh0Qg==';
      final plain =
          JmClient.decryptBody(base64Text, 1700000000, '185Hcomic3PAPP7R');
      expect(plain, '{"id":"350234","name":"测试本子","total_photos":50}');
    });
  });

  group('JmScramble.segmentationNum', () {
    test('实测车号的切片数（与 jmcomic 的 get_num 一致）', () {
      // md5('35023400001') 末位 d(100) → (100 % 10)*2+2 = 2
      expect(JmScramble.segmentationNum('220980', '350234', '00001.webp'), 2);
      // md5('35023500001') 末位 6(54) → (54 % 10)*2+2 = 10
      expect(JmScramble.segmentationNum('220980', '350235', '00001.webp'), 10);
    });

    test('老图（aid 小于 scramble_id）不切片', () {
      expect(JmScramble.segmentationNum('421926', '350234', '00001.webp'), 0);
    });

    test('文件名带目录时按 basename 计算（不能带扩展名参与散列）', () {
      expect(
        JmScramble.segmentationNum('220980', '350234', 'photos/00001.webp'),
        2,
      );
    });
  });

  group('JmScramble.plan', () {
    test('块数正确且不越界、不重叠', () {
      const height = 1000;
      const num = 10;
      final slices = JmScramble.plan(height, num);
      expect(slices.length, num);
      var covered = 0;
      for (final slice in slices) {
        expect(slice.sourceTop >= 0, isTrue);
        expect(slice.sourceTop + slice.height <= height, isTrue);
        expect(slice.targetTop >= 0, isTrue);
        expect(slice.targetTop + slice.height <= height, isTrue);
        covered += slice.height;
      }
      // 高度能被整除时，各块应正好拼满整张图
      expect(covered, height);
    });

    test('高度不能被整除时，首块补足余数', () {
      final slices = JmScramble.plan(1003, 10);
      expect(slices.length, 10);
      final first = slices.first;
      final others = slices.skip(1);
      expect(first.height, 103);
      for (final slice in others) {
        expect(slice.height, 100);
      }
    });

    test('num 为 0 或超过高度时不切', () {
      expect(JmScramble.plan(1000, 0), isEmpty);
      expect(JmScramble.plan(5, 10), isEmpty);
    });
  });

  group('naturalCompare', () {
    test('页码按数字比较，不是字典序', () {
      final names = ['10.jpg', '2.jpg', '1.jpg', '00002.jpg', '00010.jpg'];
      names.sort(naturalCompare);
      expect(names.first, '1.jpg');
      expect(names.indexOf('2.jpg') < names.indexOf('10.jpg'), isTrue);
      expect(names.indexOf('00002.jpg') < names.indexOf('00010.jpg'), isTrue);
    });
  });

  group('MangaBook', () {
    test('JSON 往返保留进度与来源', () {
      final book = MangaBook(
        path: '/storage/emulated/0/Download/漫画/董卓.zip',
        title: '董卓 上+下',
        pages: 50,
        lastPage: 12,
        lastRead: '2026-10-29T12:00:00.000',
        addedAt: '2026-10-28T12:00:00.000',
        sourceId: '350234',
      );
      final restored = MangaBook.fromJson(book.toJson());
      expect(restored, isNotNull);
      expect(restored!.title, '董卓 上+下');
      expect(restored.pages, 50);
      expect(restored.lastPage, 12);
      expect(restored.sourceId, '350234');
      expect(restored.progress, closeTo(0.24, 0.001));
      expect(restored.isZip, isTrue);
    });

    test('缺 path 的脏数据被丢弃', () {
      expect(MangaBook.fromJson({'title': '没有路径'}), isNull);
    });
  });

  group('safeFileName', () {
    test('去掉路径分隔符等非法字符', () {
      expect(safeFileName('董卓 上+下'), '董卓 上+下');
      expect(safeFileName('a/b:c*d'), 'a b c d');
    });

    test('空标题给兜底名', () {
      expect(safeFileName('///').startsWith('a b'), isFalse);
      expect(safeFileName('   ').isNotEmpty, isTrue);
    });
  });

  group('Zip 打包与解包', () {
    test('打包 3 页再读回：条目数、顺序与内容都不变', () {
      final archive = Archive();
      for (var i = 0; i < 3; i++) {
        final bytes = Uint8List.fromList(List<int>.filled(16, i + 1));
        archive.addFile(
            ArchiveFile((i + 1).toString().padLeft(5, '0') + '.jpg', bytes.length, bytes));
      }
      final encoded = ZipEncoder().encode(archive);
      expect(encoded, isNotNull);
      expect(encoded.isNotEmpty, isTrue);

      final decoded = ZipDecoder().decodeBytes(encoded);
      expect(decoded.length, 3);
      final names = [for (final file in decoded) file.name]..sort(naturalCompare);
      expect(names, ['00001.jpg', '00002.jpg', '00003.jpg']);
      final first = decoded.first.content as List<int>;
      expect(first.length, 16);
      expect(first.first, 1);
    });
  });

  group('JmParser 车号识别', () {
    test('纯数字 / JM 前缀 / 带空格都认', () {
      expect(JmParser.bareIdOf('350234'), '350234');
      expect(JmParser.bareIdOf('JM350234'), '350234');
      expect(JmParser.bareIdOf('jm 350234'), '350234');
      expect(JmParser.bareIdOf(' 350234 '), '350234');
    });

    test('不是车号的输入不认（避免把普通数字当作品）', () {
      expect(JmParser.bareIdOf('12'), isNull);
      expect(JmParser.bareIdOf('v.douyin.com/abc'), isNull);
      expect(JmParser.bareIdOf('350234 复制这段'), isNull);
      expect(JmParser.bareIdOf(''), isNull);
    });

    test('链接与车号都能取到 id', () {
      expect(JmParser.idOf('https://18comic.vip/album/350234'), '350234');
      expect(JmParser.idOf('https://18comic.vip/photo/350235'), '350235');
      expect(JmParser.idOf('350234'), '350234');
    });
  });

  group('禁漫缺页提示', () {
    test('列出页号（1 基）并说明已中止', () {
      final text = missingPagesMessage([2, 0, 4]);
      expect(text, contains('有 3 页'));
      expect(text, contains('第 1、3、5 页'));
      expect(text, contains('未生成残缺压缩包'));
    });

    test('页数太多时只列前 20 个并加「等」', () {
      final text = missingPagesMessage(List<int>.generate(44, (i) => i * 2));
      expect(text, contains('有 44 页'));
      expect(text, contains(' 等'));
      // 前 20 个页号是 1、3、…、39（末尾接「等」）；第 21 个（第 41 页）不该出现
      expect(text, contains('39 等'));
      expect(text, isNot(contains('41')));
    });
  });

  group('阅读进度写回', () {
    MangaBook book(String path, {int pages = 10, int lastPage = 0}) =>
        MangaBook(path: path, title: '书', pages: pages, lastPage: lastPage);

    test('按 path 原地更新（保持实例引用，阅读器才不会失联）', () {
      final a = book('/x/a.zip');
      final b = book('/x/b.zip');
      final books = [a, b];
      final ok = applyProgress(books, '/x/a.zip', 7, '2026-10-29T12:00:00');
      expect(ok, isTrue);
      expect(books[0], same(a), reason: '必须是原地改，不能换实例');
      expect(books[0].lastPage, 7);
      expect(books[0].lastRead, '2026-10-29T12:00:00');
      expect(books[1].lastPage, 0, reason: '不能误改其它书');
    });

    test('连续多次翻页，最后落盘的是最新页（真机 bug：永远停在第 2 页）', () {
      final target = book('/x/a.zip');
      final books = [target];
      for (final page in [1, 2, 3, 9]) {
        applyProgress(books, target.path, page, 't');
      }
      expect(books.single.lastPage, 9);
      // JSON 往返后仍然是 9
      final restored = MangaBook.fromJson(books.single.toJson());
      expect(restored!.lastPage, 9);
    });

    test('找不到 path 返回 false（不新增、不报错）', () {
      final books = [book('/x/a.zip')];
      expect(applyProgress(books, '/x/none.zip', 3, 't'), isFalse);
      expect(books.length, 1);
    });
  });

  group('长按存图命名', () {
    test('书名_序号.原扩展名', () {
      expect(savedPageFileName('某本子', 0, '00001.webp'), '某本子_001.webp');
    });

    test('序号补到 3 位', () {
      expect(savedPageFileName('A', 9, 'p10.png'), 'A_010.png');
      expect(savedPageFileName('A', 102, 'p103.jpg'), 'A_103.jpg');
    });

    test('扩展名不认识时兜底 .jpg', () {
      expect(savedPageFileName('A', 0, 'page'), 'A_001.jpg');
      expect(pageExtOf('X.JPEG'), '.jpeg');
    });

    test('书名里的非法字符会被清掉（不会写出子目录）', () {
      final name = savedPageFileName('a/b:c*d', 0, '1.jpg');
      expect(name.contains('/'), isFalse);
      expect(name.contains(':'), isFalse);
      expect(name, contains('_001.jpg'));
    });
  });

  group('禁漫镜像测速排序', () {
    const all = ['a.cc', 'b.cc', 'c.net', 'd.net'];

    test('测到的按延迟升序在前', () {
      final ranked = rankDomainsByLatency(all, {
        'c.net': 120,
        'a.cc': 800,
        'b.cc': 300,
      });
      expect(ranked, ['c.net', 'b.cc', 'a.cc', 'd.net']);
    });

    test('一个都没测到时保持原顺序', () {
      expect(rankDomainsByLatency(all, {}), all);
    });

    test('测速结果里出现列表外的域名时忽略（不崩、不混入）', () {
      final ranked = rankDomainsByLatency(['a.cc'], {'zzz.cc': 1, 'a.cc': 50});
      expect(ranked, ['a.cc']);
    });

    test('相同延迟保持稳定（不抖动）', () {
      final ranked = rankDomainsByLatency(all, {'a.cc': 100, 'b.cc': 100});
      expect(ranked.take(2).toSet(), {'a.cc', 'b.cc'});
      expect(ranked.sublist(2), ['c.net', 'd.net']);
    });
  });

  group('书架封面不能落进相册', () {
    test('封面用隐藏文件名（系统扫描器会跳过）', () {
      final file = MangaLibrary.coverFileFor('/sdcard/Pictures/漫画/某本.zip');
      expect(file.path, '/sdcard/Pictures/漫画/.某本.cover.jpg');
    });

    test('非 zip（图片文件夹）的封面也隐藏', () {
      final file = MangaLibrary.coverFileFor('/sdcard/Pictures/漫画/某本');
      expect(file.path, '/sdcard/Pictures/漫画/.某本.cover.jpg');
    });

    test('旧版可见封面名仍能定位（供迁移读取）', () {
      final legacy = MangaLibrary.legacyCoverFileFor('/a/书.zip');
      expect(legacy.path, '/a/书.cover.jpg');
    });
  });
}
