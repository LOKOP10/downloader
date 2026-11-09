import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/app_state.dart';
import '../../core/parse/media_info.dart';
import '../../core/parse/parser_registry.dart';
import '../../core/shared/links.dart';
import '../../core/shared/site.dart';
import '../../design/buttons.dart';
import '../../design/motion.dart';
import '../../design/palette.dart';
import '../../design/widgets.dart';

/// 视频解析页：粘贴链接 → 识别站点 → 解析 → 选画质加入下载队列。
class ParsePage extends StatefulWidget {
  const ParsePage({super.key});

  @override
  State<ParsePage> createState() => _ParsePageState();
}

class _ParsePageState extends State<ParsePage> {
  final TextEditingController _input = TextEditingController();
  bool _busy = false;
  MediaInfo? _info;
  String? _error;
  String _status = '';
  final Set<String> _queued = <String>{};

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (text.isEmpty || !mounted) return;
    setState(() => _input.text = text);
  }

  Future<void> _parse() async {
    final raw = _input.text.trim();
    if (raw.isEmpty) {
      setState(() => _error = '先粘贴一个分享链接或分享文案');
      return;
    }
    final link = LinkDetect.extract(raw);
    if (link == null) {
      setState(() => _error = '这段文字里没有找到链接');
      return;
    }
    final platform = LinkDetect.platformOf(link);
    if (platform == null) {
      setState(() => _error = '这个站点暂不支持');
      return;
    }
    if (!ParserRegistry.supports(platform)) {
      setState(() => _error = '还不支持解析该站点：${platform.label}');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _info = null;
      _status = '正在解析 ${platform.label}…';
    });
    try {
      final info = await ParserRegistry.parse(platform, link);
      if (!mounted) return;
      setState(() {
        _info = info;
        _status = '';
      });
    } on ParseException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _status = '';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '解析失败：$error';
        _status = '';
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _download(MediaQuality quality) async {
    final info = _info!;
    await AppState.tasks.start(
      url: quality.url,
      title: info.title,
      platform: info.platform,
      audioUrl: quality.audioUrl,
      referer: info.referer,
    );
    if (!mounted) return;
    setState(() => _queued.add(quality.url));
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text('已加入下载队列：${quality.label}'),
      duration: const Duration(seconds: 2),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(
          Space.pageH, Space.xl, Space.pageH, Space.xl),
      children: <Widget>[
        const PageHeading(
          title: '视频解析',
          subtitle: '粘贴分享链接或分享文案，选画质，下载。解析结果显示在下方，不用切页。',
        ),
        const SizedBox(height: Space.xl),
        StaggerIn(
          child: Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                TextField(
                  controller: _input,
                  maxLines: 3,
                  style: const TextStyle(fontSize: 13, color: Palette.text),
                  decoration: InputDecoration(
                    hintText: '粘贴分享链接或分享文案，回车解析',
                    hintStyle: const TextStyle(
                        fontSize: 13, color: Palette.textFaint),
                    filled: true,
                    fillColor: Palette.bg,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(Space.sm),
                      borderSide: const BorderSide(color: Palette.border),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(Space.sm),
                      borderSide: const BorderSide(color: Palette.border),
                    ),
                  ),
                  onSubmitted: (_) => _parse(),
                ),
                const SizedBox(height: Space.md),
                Row(
                  children: <Widget>[
                    GhostButton(
                      icon: Icons.content_paste,
                      label: '粘贴',
                      onTap: _busy ? null : _paste,
                    ),
                    const SizedBox(width: Space.sm),
                    PrimaryButton(
                      icon: Icons.search,
                      label: _busy ? '解析中…' : '解析',
                      onTap: _busy ? null : _parse,
                    ),
                    const Spacer(),
                    if (_status.isNotEmpty)
                      Text(_status,
                          style: const TextStyle(
                              fontSize: 12, color: Palette.textDim)),
                  ],
                ),
                const SizedBox(height: Space.sm),
                const Text(
                  '抖音 · B站 · 小红书 · X · Pornhub · hanime1 · 禁漫',
                  style: TextStyle(
                      fontSize: 12, color: Palette.textFaint),
                ),
              ],
            ),
          ),
        ),
        if (_error != null) ...<Widget>[
          const SizedBox(height: Space.lg),
          StaggerIn(
            child: Panel(
              child: Row(
                children: <Widget>[
                  const Icon(Icons.error_outline,
                      size: 16, color: Palette.danger),
                  const SizedBox(width: Space.sm),
                  Expanded(
                    child: Text(_error!,
                        style: const TextStyle(
                            fontSize: 13, color: Palette.danger)),
                  ),
                ],
              ),
            ),
          ),
        ],
        if (_info != null) ...<Widget>[
          const SizedBox(height: Space.lg),
          StaggerIn(
            index: 1,
            child: _ResultCard(
              info: _info!,
              onDownload: _download,
              queued: _queued,
            ),
          ),
        ],
      ],
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({
    required this.info,
    required this.onDownload,
    required this.queued,
  });

  final MediaInfo info;
  final Future<void> Function(MediaQuality) onDownload;
  final Set<String> queued;

  @override
  Widget build(BuildContext context) {
    return Panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Container(
                width: 96,
                height: 54,
                clipBehavior: Clip.antiAlias,
                decoration: BoxDecoration(
                  color: Palette.bg,
                  borderRadius: BorderRadius.circular(Space.sm),
                ),
                child: info.coverUrl.isEmpty
                    ? const Icon(Icons.movie_outlined,
                        size: 20, color: Palette.textFaint)
                    : Image.network(
                        info.coverUrl,
                        fit: BoxFit.cover,
                        errorBuilder:
                            (BuildContext c, Object e, StackTrace? s) =>
                                const Icon(Icons.broken_image_outlined,
                                    size: 20, color: Palette.textFaint),
                      ),
              ),
              const SizedBox(width: Space.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(info.title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                            color: Palette.text)),
                    const SizedBox(height: 2),
                    Text(
                      <String>[
                        info.platform.label,
                        if (info.author.isNotEmpty) info.author,
                        if (info.durationText.isNotEmpty) info.durationText,
                      ].join(' · '),
                      style: const TextStyle(
                          fontSize: 12, color: Palette.textDim),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: Space.lg),
          const Text('画质',
              style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: Palette.textDim)),
          const SizedBox(height: Space.sm),
          if (info.qualities.isEmpty)
            const Text('没有可下载的画质',
                style: TextStyle(fontSize: 12.5, color: Palette.textFaint))
          else
            ...info.qualities.asMap().entries.map(
              (MapEntry<int, MediaQuality> entry) {
                final q = entry.value;
                final done = queued.contains(q.url);
                return Padding(
                  padding: const EdgeInsets.only(bottom: Space.sm),
                  child: StaggerIn(
                    index: entry.key,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: Space.md, vertical: 10),
                      decoration: BoxDecoration(
                        color: Palette.bg,
                        borderRadius: BorderRadius.circular(Space.sm),
                        border: Border.all(color: Palette.border),
                      ),
                      child: Row(
                        children: <Widget>[
                          Text(q.label,
                              style: const TextStyle(
                                  fontSize: 13, color: Palette.text)),
                          if (q.sizeBytes > 0) ...<Widget>[
                            const SizedBox(width: Space.sm),
                            Text(_sizeText(q.sizeBytes),
                                style: const TextStyle(
                                    fontSize: 12,
                                    color: Palette.textFaint)),
                          ],
                          const Spacer(),
                          GhostButton(
                            icon: done
                                ? Icons.check
                                : Icons.download_outlined,
                            label: done ? '已加入' : '下载',
                            onTap: done ? null : () => onDownload(q),
                          ),
                        ],
                      ),
                    ),
                  ),
                );
              },
            ),
        ],
      ),
    );
  }

  static String _sizeText(int bytes) {
    if (bytes <= 0) return '';
    if (bytes >= 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
    }
    if (bytes >= 1024 * 1024) {
      return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024).toStringAsFixed(0)} KB';
  }
}
