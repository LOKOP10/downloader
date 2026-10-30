import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

/// 禁漫原图「解码」（横向切片重排）。
///
/// 禁漫把每张图按横条切块后**逆序**存进文件，直接看是花屏。
/// 还原就是把第 i 块搬到正确位置（与 jmcomic 的 decode_and_save 逐行等价）：
///
///     over = h % num
///     for i in 0..num-1:
///         move = h / num
///         y_src = h - move*(i+1) - over
///         y_dst = move*i
///         i == 0 时 move 加上 over，否则 y_dst 加上 over
///
/// 像素搬运交给原生（安卓 Bitmap），这里只负责算「切几块」与「每块从哪到哪」，
/// 两端共用同一份算法，单测可以逐条比对。
abstract final class JmScramble {
  static const _scramble268850 = 268850;
  static const _scramble421926 = 421926;

  /// 要切成几块。返回 0 表示这张图没被切过、直接保存即可。
  ///
  /// 规则与 jmcomic 的 get_num 一致：
  ///   aid < scramble_id → 0
  ///   aid < 268850      → 10
  ///   aid < 421926      → x = 10
  ///   否则               → x = 8
  ///   后两种：num = (md5(aid + basename) 末位十六进制字符 % x) * 2 + 2
  ///
  /// basename 必须**不带扩展名**：带上 .webp 会让大部分页算错，只有少数页碰巧对。
  static int segmentationNum(
    String scrambleId,
    String aid,
    String filename,
  ) {
    final salt = int.tryParse(scrambleId) ?? 0;
    final photoId = int.tryParse(aid) ?? 0;
    if (photoId < salt || photoId == 0) return 0;
    if (photoId < _scramble268850) return 10;

    final x = photoId < _scramble421926 ? 10 : 8;
    var basename = filename;
    final slash = basename.lastIndexOf('/');
    if (slash >= 0) basename = basename.substring(slash + 1);
    final dot = basename.lastIndexOf('.');
    if (dot > 0) basename = basename.substring(0, dot);

    final digest = MD5Digest()
        .process(Uint8List.fromList(_utf8(photoId.toString() + basename)));
    // 取 md5 十六进制串最后一位的码点（与 Python ord(hexdigest()[-1]) 一致）
    final lastHex = digest[digest.length - 1].toRadixString(16).padLeft(2, '0');
    final code = lastHex.codeUnitAt(lastHex.length - 1);
    return (code % x) * 2 + 2;
  }

  /// 切块计划：每块「源起点 / 目标起点 / 高度」。越界的块会被裁掉。
  static List<ScrambleSlice> plan(int height, int num) {
    if (num <= 0 || height <= 0 || height < num) return const [];
    final slices = <ScrambleSlice>[];
    final over = height % num;
    final move = height ~/ num;
    for (var i = 0; i < num; i++) {
      var blockHeight = move;
      final sourceTop = height - move * (i + 1) - over;
      var targetTop = move * i;
      if (i == 0) {
        blockHeight += over;
      } else {
        targetTop += over;
      }
      if (blockHeight <= 0) continue;
      final sourceBottom = _min(sourceTop + blockHeight, height);
      final clippedTop = _max(sourceTop, 0);
      if (sourceBottom <= clippedTop) continue;
      final realHeight = sourceBottom - clippedTop;
      final targetClipped = _max(targetTop, 0);
      if (targetClipped + realHeight > height) continue;
      slices.add(ScrambleSlice(clippedTop, targetClipped, realHeight));
    }
    return slices;
  }

  static List<int> _utf8(String text) => utf8.encode(text);

  static int _min(int a, int b) => a < b ? a : b;

  static int _max(int a, int b) => a > b ? a : b;
}

/// 一块的位置：把源图 [sourceTop, sourceTop+height) 画到目标 [targetTop, ...)。
class ScrambleSlice {
  const ScrambleSlice(this.sourceTop, this.targetTop, this.height);

  final int sourceTop;
  final int targetTop;
  final int height;

  @override
  String toString() =>
      'src=' + sourceTop.toString() + ' dst=' + targetTop.toString() + ' h=' + height.toString();
}
