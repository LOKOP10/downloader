import 'package:flutter/foundation.dart';

/// 一条日志。
class LogEntry {
  const LogEntry({required this.time, required this.tag, required this.message});

  final DateTime time;
  final String tag;
  final String message;

  String get line {
    final t = time;
    final stamp = '${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}.${t.millisecond.toString().padLeft(3, '0')}';
    return '$stamp [$tag] $message';
  }

  static String _two(int v) => v.toString().padLeft(2, '0');
}

/// 应用内运行日志：环形缓冲，设置页可查看 / 复制。
///
/// 目的很直接 —— 出问题时用户能一键复制日志发过来，不用连电脑看 logcat。
class AppLog {
  AppLog._();

  static final AppLog instance = AppLog._();

  static const int _max = 800;

  final List<LogEntry> _entries = <LogEntry>[];

  /// 界面监听（有新增就 +1，触发重建）。
  final ValueNotifier<int> revision = ValueNotifier<int>(0);

  List<LogEntry> get entries => List<LogEntry>.unmodifiable(_entries);

  int get count => _entries.length;

  void add(String tag, Object? message) {
    final text = message?.toString() ?? '';
    _entries.add(LogEntry(time: DateTime.now(), tag: tag, message: text));
    if (_entries.length > _max) {
      _entries.removeRange(0, _entries.length - _max);
    }
    revision.value++;
    // 同时打到控制台，连电脑时也能看
    debugPrint('$tag | $text');
  }

  void clear() {
    _entries.clear();
    revision.value++;
  }

  /// 导出为可复制的纯文本（新的在后）。
  String export() => _entries.map((e) => e.line).join('\n');
}
