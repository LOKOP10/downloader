import 'dart:io';

/// 找到可用的 ffmpeg：环境变量 → 程序目录 tools → PATH。
Future<String?> resolveFfmpeg() async {
  final env = Platform.environment['VDL_FFMPEG'];
  if (env != null && env.isNotEmpty && File(env).existsSync()) return env;

  final exeDir = File(Platform.resolvedExecutable).parent.path;
  for (final name in <String>['ffmpeg.exe', 'ffmpeg']) {
    for (final dir in <String>[exeDir, '$exeDir\\tools', '$exeDir\\..\\tools']) {
      final candidate = File('$dir\\$name');
      if (candidate.existsSync()) return candidate.path;
    }
  }

  try {
    final probe = await Process.run(
      Platform.isWindows ? 'where' : 'which',
      <String>['ffmpeg'],
    );
    if (probe.exitCode == 0) {
      final first = (probe.stdout as String).split(RegExp(r'\r?\n')).first.trim();
      if (first.isNotEmpty) return first;
    }
  } catch (_) {
    // 没装就算了，调用方会给出提示
  }
  return null;
}

/// 跑一条 ffmpeg 命令，返回退出码与 stderr。
Future<({int code, String error})> runFfmpeg(
  List<String> args, {
  void Function(String line)? onLine,
}) async {
  final ffmpeg = await resolveFfmpeg();
  if (ffmpeg == null) {
    return (code: -1, error: '没有找到 ffmpeg');
  }
  final proc = await Process.start(ffmpeg, args);
  final err = StringBuffer();
  final done = proc.stderr
      .transform(const SystemEncoding().decoder)
      .listen((String chunk) {
    err.write(chunk);
    onLine?.call(chunk);
  }).asFuture<void>();
  await proc.stdout.drain<void>();
  await done;
  final code = await proc.exitCode;
  return (code: code, error: err.toString());
}
