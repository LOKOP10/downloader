import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:vdl/features/download/data/download_location.dart';

import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/features/download/hls_downloader.dart';
import 'package:vdl/features/download/media_muxer.dart';
import 'package:vdl/shared/net/http.dart';
import 'package:vdl/shared/site.dart';
import 'package:vdl/features/download/task_model.dart';

/// 一个下载任务。
class DownloadJob {
  DownloadJob({
    required this.id,
    required this.title,
    required this.url,
    this.platform,
    required this.fileName,
    this.fallbackExt = '.mp4',
    this.audioUrl,
    this.referer,
    this.external = false,
  });

  final String id;
  final String title;
  final String url;
  final SitePlatform? platform;
  final String fileName;

  /// 地址里看不出扩展名时的兜底（图集是图片，兜底 .jpg）。
  final String fallbackExt;

  /// DASH 分离的音频轨地址：非空表示下完视频还要拉音频并合并（B 站 1080P/4K）。
  final String? audioUrl;

  /// 下载时的 Referer 覆盖。
  ///
  /// Pornhub 的 CDN 直链只认「视频页」作 Referer（站点根域一律 404），
  /// 所以由解析器把作品页地址随 MediaInfo 带下来，这里原样写进请求头。
  final String? referer;

  bool get needsMerge => audioUrl != null && audioUrl!.isNotEmpty;

  /// 外部托管的任务（禁漫整本 zip 这类）：进度由调用方 push，队列只管展示与取消。
  final bool external;

  /// 外部任务的取消回调。
  void Function()? onCancel;

  TaskStatus status = TaskStatus.waiting;
  DownloadProgress? progress;
  String? error;
  String? savePath;

  CancelToken? token;

  bool get finished => status == TaskStatus.done || status == TaskStatus.failed;
}

/// 任务中心：真实下载队列。界面通过 ListenableBuilder 监听它。
class TaskStore extends ChangeNotifier {
  TaskStore._();

  static final TaskStore instance = TaskStore._();

  final List<DownloadJob> jobs = [];

  /// 并发连接数（设置页可改）。
  int connections = 4;

  /// 图集下载串行链（见 startGallery）。
  Future<void> _galleryChain = Future<void>.value();

  String? _downloadDir;

  /// 下载目录：跟随设置（默认应用外部目录），每次下载前重新解析一次，
  /// 这样用户在设置里改完立刻生效。
  Future<String> ensureDir() async {
    final path = await DownloadLocation.current();
    if (_downloadDir != path) {
      await Directory(path).create(recursive: true);
      _downloadDir = path;
    }
    return path;
  }

  /// 设置里改过保存位置后调用，让下一次下载重新读目录。
  void invalidateDir() {
    _downloadDir = null;
    notifyListeners();
  }

  String get downloadDir => _downloadDir ?? '（首次下载时创建）';

  /// 登记一个「外部托管」任务：进度由调用方推送，这里只负责在任务页展示与取消。
  ///
  /// 禁漫那种「一本书一个 zip」的下载不适合走 URL→文件模型，但用户仍然希望
  /// 能在任务页看到进度，所以单独给一个入口。
  DownloadJob startExternal({
    required String title,
    SitePlatform? platform,
    void Function()? onCancel,
  }) {
    final job = DownloadJob(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
      title: title,
      url: '',
      platform: platform,
      fileName: title,
      external: true,
    );
    job.status = TaskStatus.running;
    job.onCancel = onCancel;
    jobs.insert(0, job);
    notifyListeners();
    return job;
  }

  /// 外部任务上报进度（单位由调用方决定，任务页按页数显示）。
  void reportExternal(DownloadJob job, {required int done, required int total}) {
    job.progress = DownloadProgress(received: done, total: total, speed: 0);
    notifyListeners();
  }

  /// 外部任务结束：[error] 为空表示成功。
  void finishExternal(DownloadJob job, {String? error}) {
    if (error == null) {
      job.status = TaskStatus.done;
      job.progress = DownloadProgress(
        received: job.progress?.total ?? 0,
        total: job.progress?.total ?? 0,
        speed: 0,
      );
    } else {
      job.status = TaskStatus.failed;
      job.error = error;
    }
    notifyListeners();
  }

  /// 发起下载。同一 URL 正在下载时不重复入队。
  Future<DownloadJob> start({
    required String url,
    required String title,
    SitePlatform? platform,
    String? fileName,
    String? audioUrl,
    String? referer,
  }) async {
    final existing = jobs.firstWhere(
      (j) => j.url == url && j.status == TaskStatus.running,
      orElse: () => DownloadJob(id: '', title: '', url: '', fileName: ''),
    );
    if (existing.id.isNotEmpty) return existing;

    final name = safeName(fileName ?? title);
    final job = DownloadJob(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
      title: title.isEmpty ? name : title,
      url: url,
      platform: platform,
      fileName: name,
      audioUrl: audioUrl,
      referer: referer,
    );
    job.token = CancelToken();
    jobs.insert(0, job);
    notifyListeners();

    // 不 await：下载在后台跑，进度靠 notifyListeners 推给界面
    _run(job).catchError((Object error, StackTrace stack) {
      debugPrint('下载任务异常：§error');
    });
    return job;
  }

  Future<void> _run(DownloadJob job) async {
    job.status = TaskStatus.running;
    notifyListeners();
    try {
      // 所有作品（抖音 / 小红书 / B站、视频 / 图集）一律平铺在下载目录，
      // 不再为单个作品新建一层文件夹。
      final dir = await ensureDir();
      final ext = extensionFor(job.url, fallback: job.fallbackExt);
      final target = File('$dir${Platform.pathSeparator}${job.fileName}$ext');
      job.savePath = target.path;
      final headers =
          AppHttp.headers(platform: job.platform, ua: _uaFor(job.platform));
      final referer = job.referer;
      if (referer != null && referer.isNotEmpty) headers['Referer'] = referer;
      if (isHlsUrl(job.url)) {
        // HLS（Pornhub 这类只给 m3u8 的站）：逐片下载后按序拼接成 TS 流
        await const HlsDownloader().download(
          url: job.url,
          target: target,
          headers: headers,
          cancelToken: job.token,
          onProgress: (progress) {
            job.progress = progress;
            notifyListeners();
          },
        );
      } else if (job.needsMerge) {
        // B 站 DASH：视频轨、音频轨分开下，再用原生 MediaMuxer 合成 mp4
        await _downloadSplit(job, target, headers);
      } else {
        await const Downloader().download(
          url: job.url,
          target: target,
          connections: connections,
          headers: headers,
          cancelToken: job.token,
          onProgress: (p) {
            job.progress = p;
            notifyListeners();
          },
        );
      }
      job.status = TaskStatus.done;
    } on DownloadCancelled {
      job.status = TaskStatus.failed;
      job.error = '已取消';
      final path = job.savePath;
      if (path != null) {
        try {
          final f = File(path);
          if (await f.exists()) await f.delete();
        } catch (_) {}
      }
    } catch (error) {
      job.status = TaskStatus.failed;
      job.error = error.toString();
    }
    notifyListeners();
  }

  /// 下载「视频轨 + 音频轨」并合并（B 站 DASH 专用）。
  ///
  /// 中间文件放系统临时目录，合并结果直接写到下载目录的最终文件名；
  /// 进度按「视频 + 音频」总字节算，合并阶段停在 99%，完成后才置 done。
  Future<void> _downloadSplit(
    DownloadJob job,
    File target,
    Map<String, String> headers,
  ) async {
    final sep = Platform.pathSeparator;
    final tmpRoot =
        Directory(Directory.systemTemp.path + sep + '_vdl_mux');
    await tmpRoot.create(recursive: true);
    final stamp = DateTime.now().microsecondsSinceEpoch.toString();
    final videoTmp = File(tmpRoot.path + sep + 'v_' + stamp + '.m4s');
    final audioTmp = File(tmpRoot.path + sep + 'a_' + stamp + '.m4s');
    var videoTotal = 0;
    final audioTotal = await AppHttp.probeSize(
      job.audioUrl!,
      platform: job.platform,
      ua: _uaFor(job.platform),
    );
    try {
      job.token?.throwIfCancelled();
      await const Downloader().download(
        url: job.url,
        target: videoTmp,
        connections: connections,
        headers: headers,
        cancelToken: job.token,
        onProgress: (p) {
          videoTotal = p.total;
          job.progress = DownloadProgress(
            received: p.received,
            total: p.total + audioTotal,
            speed: p.speed,
          );
          notifyListeners();
        },
      );
      job.token?.throwIfCancelled();
      await const Downloader().download(
        url: job.audioUrl!,
        target: audioTmp,
        connections: 1,
        headers: headers,
        cancelToken: job.token,
        onProgress: (p) {
          job.progress = DownloadProgress(
            received: videoTotal + p.received,
            total: videoTotal + (p.total > 0 ? p.total : audioTotal),
            speed: p.speed,
          );
          notifyListeners();
        },
      );
      job.token?.throwIfCancelled();

      // 合并阶段没有进度回调，把条停在 99%，别让人以为卡死
      final total = videoTotal + audioTotal;
      if (total > 0) {
        job.progress = DownloadProgress(
          received: (total * 0.99).round(),
          total: total,
          speed: 0,
        );
        notifyListeners();
      }
      // 合并期间点取消要能打断原生循环：轮询令牌，取消就通知原生
      final watcher = Timer.periodic(const Duration(milliseconds: 400), (_) {
        if (job.token?.isCancelled == true) MediaMuxer.instance.cancel();
      });
      try {
        await MediaMuxer.instance.merge(
          videoPath: videoTmp.path,
          audioPath: audioTmp.path,
          outPath: target.path,
        );
      } finally {
        watcher.cancel();
      }
      job.token?.throwIfCancelled();
    } finally {
      // 中间文件一律清掉（失败也一样）
      for (final file in [videoTmp, audioTmp]) {
        try {
          if (await file.exists()) await file.delete();
        } catch (_) {}
      }
    }
  }
  /// 图集：把选中的若干张图各自入队，**直接平铺在下载目录**，
  /// 文件名「标题_001」「标题_002」…（不再为每个作品新建一层文件夹）。
  Future<int> startGallery({
    required List<String> urls,
    required Set<int> selected,
    required String title,
    SitePlatform? platform,
    String? referer,
  }) async {
    final base = safeName(title);
    var queued = 0;
    final indexes = selected.toList()..sort();
    for (final index in indexes) {
      if (index < 0 || index >= urls.length) continue;
      final url = urls[index];
      final name = galleryFileName(base, index);
      if (jobs.any((j) =>
          j.url == url && (j.status == TaskStatus.running ||
              j.status == TaskStatus.waiting))) {
        continue;
      }
      final job = DownloadJob(
        id: DateTime.now().microsecondsSinceEpoch.toString() + '-' + index.toString(),
        title: '$base · 第 ${index + 1} 张',
        url: url,
        platform: platform,
        fileName: name,
        fallbackExt: '.jpg',
        referer: referer,
      );
      job.token = CancelToken();
      jobs.insert(0, job);
      queued++;
      // 串行排队：6 张图各开 4 连接同时打 CDN，真机上常见「只落了一张，
      // 其余 429/断流」，所以图集一张一张下。
      _galleryChain = _galleryChain.then((_) => _run(job)).catchError(
            (Object error, StackTrace stack) {
        debugPrint('图集下载异常：$error');
      });
    }
    if (queued > 0) notifyListeners();
    return queued;
  }

  void cancel(String id) {
    for (final job in jobs) {
      if (job.id != id || job.finished) continue;
      job.token?.cancel();
      job.onCancel?.call();
    }
  }

  void clearFinished() {
    jobs.removeWhere((j) => j.finished);
    notifyListeners();
  }

  void retry(String id) {
    final job = jobs.firstWhere((j) => j.id == id,
        orElse: () => DownloadJob(id: '', title: '', url: '', fileName: ''));
    if (job.id.isEmpty) return;
    // 外部托管任务没法「重试」，重下请回首页再点一次
    if (job.external) return;
    jobs.remove(job);
    start(
      url: job.url,
      title: job.title,
      platform: job.platform,
      fileName: job.fileName,
      referer: job.referer,
    );
  }

  /// 图集文件名：前缀_001、前缀_002 …
  ///
  /// 平铺在下载目录里，靠「标题_序号」区分不同作品，不再每个作品建一层文件夹。
  static String galleryFileName(String base, int index) {
    final seq = (index + 1).toString().padLeft(3, '0');
    final prefix = base.length > 60 ? base.substring(0, 60).trim() : base;
    return prefix + '_' + seq;
  }

  /// 去掉 Windows/Android 都不接受的文件名字符，并限长。
  ///
  /// 小红书 / 抖音的标题里常带「#话题[话题]#」标记，既占长度又是文件名里的
  /// 噪声（图集文件夹会变成一长串井号），这里一并清掉。
  static String safeName(String raw) {
    var name = raw.trim();
    name = name.replaceAll(RegExp(r'#[^#\s]{0,40}?\[话题\]#'), ' ');
    name = name.replaceAll('[话题]', ' ');
    name = name.replaceAll(RegExp(r'[#\[\]【】]'), ' ');
    name = name.replaceAll(RegExp(r'[\\/:*?"<>|\r\n\t]'), ' ');
    name = name.replaceAll(RegExp(r'\s+'), ' ').trim();
    if (name.length > 80) name = name.substring(0, 80).trim();
    if (name.isEmpty) name = 'media_${DateTime.now().millisecondsSinceEpoch}';
    return name;
  }

  /// 下载用的 User-Agent。
  ///
  /// · B 站：DASH 的 .m4s 只认桌面 UA，安卓 UA 直接 403（实测），所以整站走桌面 UA；
  /// · Iwara / Pornhub / hanime1：解析阶段就是用桌面 UA 拿到的直链，
  ///   下载与探针保持同一个 UA，免得 CDN 按 UA 判来源再回 403。
  static String _uaFor(SitePlatform? platform) {
    switch (platform) {
      case SitePlatform.bili:
      case SitePlatform.iwara:
      case SitePlatform.pornhub:
      case SitePlatform.hanime1:
        return AppHttp.uaDesktop;
      default:
        return AppHttp.uaMobile;
    }
  }
  /// 从地址判断落盘扩展名。
  ///
  /// 小红书图片走 CDN 时**路径里没有扩展名**，格式只写在尾巴上
  /// （...!nd_dft_wlteh_jpg_3 / ...!nd_prv_wlteh_webp_3）。
  /// 原来一律兜底 .mp4，结果图集 6 张图全被存成 001.mp4 / 002.mp4，
  /// 看图库、发群里都打不开 —— 所以这里要先认尾巴上的格式提示。
  static String extensionFor(String url, {String fallback = '.mp4'}) {
    final uri = Uri.tryParse(url);
    final path = (uri?.path ?? '').toLowerCase();
    // HLS 播放列表：产物是拼好的 TS 流（不引 ffmpeg，就不假装是 mp4）
    if (path.endsWith('.m3u8')) return '.ts';
    // 1) 路径里就有真扩展名
    final direct = RegExp(r'\.([a-z0-9]{2,5})$').firstMatch(path);
    if (direct != null && _knownExts.contains(direct.group(1))) {
      return '.${direct.group(1)}';
    }
    // 2) CDN 把格式写在路径尾巴里（取最后一个提示，最贴近文件体）
    final hints = RegExp(
            r'(?:[!._-])(jpe?g|png|webp|gif|heic|avif|mp4|mov)(?=[!._-]|$)')
        .allMatches(path);
    if (hints.isNotEmpty) {
      final ext = hints.last.group(1)!;
      return ext == 'jpeg' ? '.jpg' : '.${ext}';
    }
    // 3) 抖音图集地址常以 ~tplv-... 结尾、没有扩展名：按图片兜底
    if (path.contains('~tplv')) return '.jpg';
    return fallback;
  }

  /// 是否是 HLS 播放列表地址（下载走分片拼接，而不是单文件多连接）。
  static bool isHlsUrl(String url) =>
      (Uri.tryParse(url)?.path.toLowerCase() ?? '').endsWith('.m3u8');

  static const _knownExts = {
    'mp4', 'mkv', 'webm', 'mov', 'm4v', 'flv', 'ts',
    'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'avif',
  };
}

/// 请求切到指定页签（首页下载完跳任务页用）。
final ValueNotifier<int?> tabRequest = ValueNotifier<int?>(null);
