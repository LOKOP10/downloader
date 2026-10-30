import 'dart:async';

import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/app_list_tile.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/features/home/data/clipboard_sniffer.dart';
import 'package:vdl/features/home/ui/gallery_picker.dart';
import 'package:vdl/features/home/ui/widgets/input_card.dart';
import 'package:vdl/features/home/ui/widgets/result_card.dart';
import 'package:vdl/features/manga/data/jm_client.dart';
import 'package:vdl/features/manga/data/jm_parser.dart';
import 'package:vdl/features/manga/data/jm_preview_cache.dart';
import 'package:vdl/features/manga/ui/jm_panel.dart';
import 'package:vdl/features/parse/media_info.dart';
import 'package:vdl/features/parse/media_info.dart' show MediaInfo, MediaQuality;
import 'package:vdl/features/parse/parser_registry.dart';
import 'package:vdl/features/preview/preview.dart';
import 'package:vdl/shared/app_log.dart';
import 'package:vdl/shared/links.dart';
import 'package:vdl/shared/site.dart';

/// 首页：粘贴链接 → 解析 → 下载。
///
/// 只负责状态与编排，具体 UI 在 widgets/ 下；解析实现由 ParserRegistry 分发。
class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _controller = TextEditingController();
  final _inputFocus = FocusNode();
  late final ClipboardSniffer _sniffer;
  /// 上次自动回填的地址：用户没改过才允许被下一次嗅探覆盖
  String _autoFilled = '';
  bool _disposed = false;
  bool _parsing = false;
  MediaInfo? _media;
  JmAlbum? _jmAlbum;

  /// 上次解析到的禁漫本子 id（'' = 上次解析的不是禁漫）。
  /// 用来判断「解析了新的链接」后要不要清在线预览缓存。
  String _previewCacheAlbumId = '';
  int _quality = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    _sniffer = ClipboardSniffer(onDetected: _onClipboardDetected)..start();
  }

  @override
  void dispose() {
    _disposed = true;
    _sniffer.stop();
    _inputFocus.dispose();
    _controller.dispose();
    super.dispose();
  }

  /// 嗅探到链接：直接回填并提示，不自动开始解析（给用户确认的机会）。
  ///
  /// 只有本站支持的站点才会走到这里（判定在 `ClipboardSniffer.checkNow`，
  /// 别的网站的链接不嗅探）。用户**正在输入框里打字**时不覆盖，改为提示去点「粘贴」。
  void _onClipboardDetected(String url, SitePlatform platform) {
    // 页面已销毁时不能再碰 FocusNode（已 dispose 会抛异常）
    if (_disposed || !mounted) return;
    final platformLabel = platform.label;
    if (!ClipboardSniffer.canReplace(
        inputFocused: _inputFocus.hasFocus, current: _controller.text)) {
      _toast('剪贴板里有新的$platformLabel链接，点「粘贴」填入');
      return;
    }
    final replaced = _controller.text.trim().isNotEmpty &&
        _controller.text.trim() != url;
    setState(() {
      _controller.text = url;
      _autoFilled = url;
      _error = null;
      _media = null;
    });
    _toast(replaced
        ? '已用剪贴板里的$platformLabel链接替换输入框，点「开始解析」'
        : '已从剪贴板识别到$platformLabel，点「开始解析」');
  }

  /// 「粘贴」按钮：立即读剪贴板并填入。
  Future<void> _pasteFromClipboard() async {
    final text = await _sniffer.read();
    if (text == null) {
      _toast('剪贴板里没有文字');
      return;
    }
    final url = LinkDetect.extract(text);
    if (url == null) {
      _toast('剪贴板里没有可识别的链接');
      return;
    }
    _sniffer.markHandled(text);
    setState(() {
      _controller.text = url;
      _autoFilled = url;
      _error = null;
    });
    _toast('已粘贴链接');
  }

  void _toast(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _parse() async {
    final text = _controller.text.trim();
    if (text.isEmpty) {
      _toast('先粘贴一个作品链接');
      return;
    }
    // 认不出来就直接说清楚，绝不把整段文案当地址去请求
    // （之前就是这样把 "v.douyin.com/xxx gbA:/ ..." 整段送进网络层，报成 DNS 错误）
    var url = LinkDetect.extract(text);
    SitePlatform? platform = url == null ? null : LinkDetect.platformOf(url);
    // 禁漫用户习惯直接报车号（如 350234 / JM350234），而不是贴链接
    if (url == null) {
      final jmId = JmParser.bareIdOf(text);
      if (jmId != null) {
        url = JmParser.linkFor(jmId);
        platform = SitePlatform.jm;
      }
    }
    if (url == null) {
      setState(() {
        _parsing = false;
        _media = null;
        _jmAlbum = null;
        _error = '没从这段文字里认出链接。粘贴作品链接，'
            '禁漫也可以直接填车号（如 350234）';
      });
      return;
    }
    // 固定成不可变局部变量：闭包里用可空变量拿不到 null 提升
    final target = url;
    AppLog.instance.add('parse', '开始解析 platform=' +
        (platform?.name ?? '未知') + ' url=' + url);
    setState(() {
      _parsing = true;
      _media = null;
      _jmAlbum = null;
      _error = null;
      _quality = 0;
    });

    try {
      // 禁漫走专用链路：产物是一本书（zip），不是单个媒体地址
      if (platform == SitePlatform.jm) {
        final album = await JmParser.parse(target);
        if (!mounted) return;
        setState(() {
          _jmAlbum = album;
          _parsing = false;
        });
        _syncPreviewCache(album.id);
        return;
      }
      if (platform != null && ParserRegistry.supports(platform)) {
        final info = await ParserRegistry.parse(platform, url);
        if (!mounted) return;
        _syncPreviewCache(null);
        setState(() {
          _media = info;
          _parsing = false;
        });
        AppLog.instance.add(
            'parse',
            '解析成功：title=' + info.title + ' 图集=' +
                info.images.length.toString() + ' 档位=' +
                info.qualities.map((q) => q.label).join(','));
      } else if (LinkDetect.isDirectMedia(target)) {
        final segments = Uri.parse(target).pathSegments;
        final name = segments.isEmpty ? '视频' : segments.last;
        if (!mounted) return;
        _syncPreviewCache(null);
        setState(() {
          _media = MediaInfo(
            platform: platform ?? SitePlatform.douyin,
            title: name,
            author: '媒体直链',
            qualities: [MediaQuality(label: '原始文件（不分档）', url: target)],
          );
          _parsing = false;
        });
      } else {
        setState(() {
          _parsing = false;
          _error = platform == null
              ? '没认出链接所属站点，请粘贴作品页或完整分享文案'
              : '${platform.label} 的解析还没接入（当前支持：'
                  '${ParserRegistry.readySites.map((s) => s.label).join('、')}）';
        });
      }
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _parsing = false;
        _error = _friendlyError(error);
      });
    }
  }

  /// 解析完一条新链接：把上一个本子的在线预览磁盘缓存清掉。
  ///
  /// 只在「换本子 / 解析的不是禁漫」时清，同一本重复解析保留缓存。
  void _syncPreviewCache(String? albumId) {
    if (!shouldClearPreviewCache(albumId, _previewCacheAlbumId)) return;
    _previewCacheAlbumId = (albumId ?? '').trim();
    // 不 await：清缓存不该拖慢解析返回
    unawaited(JmPreviewCache.clear());
  }

  /// 把底层异常翻成人话，技术细节附在后面方便排查。
  String _friendlyError(Object error) {
    if (error is ParseException) return error.message;
    final text = error.toString();
    if (text.contains('Failed host lookup') || text.contains('SocketException')) {
      return '网络连不上（${_hostOf(text)}）：检查手机网络，或关掉代理 / VPN 后重试';
    }
    if (text.contains('TimeoutException') || text.contains('timed out')) {
      return '请求超时，稍后重试';
    }
    // 用正则切行，避免在字符串里写 \n 被工具链转义
    final firstLine = text.split(RegExp(r'\r?\n')).first;
    if (firstLine.contains('HTTP 4') || firstLine.contains('HTTP 5')) {
      return '站点返回异常：$firstLine';
    }
    return '解析失败：$firstLine';
  }

  String _hostOf(String text) {
    final m = RegExp(r"host lookup: '([^']+)'").firstMatch(text);
    return m?.group(1) ?? '目标站点';
  }

  Future<void> _startDownload() async {
    final media = _media;
    if (media == null) {
      _toast('先解析出可下载地址');
      return;
    }
    // 图集：进选择页，勾完再入队
    if (media.isImageAlbum) {
      final queued = await Navigator.of(context).push<int>(
        MaterialPageRoute<int>(builder: (_) => GalleryPicker(media: media)),
      );
      if (queued != null && queued > 0) {
        tabRequest.value = 1;
      }
      return;
    }
    if (media.qualities.isEmpty) {
      _toast('没有可下载的地址');
      return;
    }
    final quality = media.qualities[_quality];
    await TaskStore.instance.start(
      url: quality.url,
      title: media.title,
      platform: media.platform,
      fileName: media.title,
      // B 站 DASH 档位：视频与音频分开，下载后由原生 Muxer 合并
      audioUrl: quality.audioUrl,
      // Pornhub 这类 CDN 只认作品页做 Referer，解析器会带下来
      referer: media.referer,
    );
    tabRequest.value = 1;
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      appBar: AppTopBar(
        title: '视频下载器',
        actions: [
          IconButton(
            tooltip: '清空',
            icon: Icon(Icons.backspace_outlined, color: palette.labelText),
            onPressed: () {
              _controller.clear();
              // 清空 = 输入框、解析结果（含禁漫面板）、错误提示一起重置
              setState(() {
                _media = null;
                _jmAlbum = null;
                _error = null;
                _parsing = false;
                _quality = 0;
              });
            },
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
            AppSize.screenH, AppSize.screenTop, AppSize.screenH, 24),
        children: [
          HomeInputCard(
            controller: _controller,
            focusNode: _inputFocus,
            parsing: _parsing,
            onParse: _parse,
            onPaste: _pasteFromClipboard,
          ),
          if (_parsing) ...[
            const SizedBox(height: AppSize.cardGap),
            AppCard(
              child: Row(
                children: [
                  SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: palette.accent),
                  ),
                  const SizedBox(width: 12),
                  Text('正在解析…',
                      style: TextStyle(color: palette.hint, fontSize: 13)),
                ],
              ),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: AppSize.cardGap),
            AppCard(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.error_outline, color: palette.danger, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(_error!,
                        style: TextStyle(color: palette.danger, fontSize: 13)),
                  ),
                ],
              ),
            ),
          ],
          if (_jmAlbum != null) ...[
            const SizedBox(height: AppSize.cardGap),
            const AppSectionLabel('解析结果'),
            JmPanel(
              album: _jmAlbum!,
              onOpenLibrary: () => tabRequest.value = 2,
            ),
          ],
          if (_media != null) ...[
            const SizedBox(height: AppSize.cardGap),
            const AppSectionLabel('解析结果'),
            HomeResultCard(
              media: _media!,
              qualityIndex: _quality,
              onPickQuality: () => _pickQuality(_media!),
              onDownload: _startDownload,
              onPreview: () =>
                  Preview.openMedia(context, _media!, qualityIndex: _quality),
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _pickQuality(MediaInfo media) async {
    if (media.qualities.length <= 1) {
      _toast('该作品只有一档画质');
      return;
    }
    final palette = AppPalette.of(context);
    final picked = await showModalBottomSheet<int>(
      context: context,
      backgroundColor: palette.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(14)),
      ),
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Text('选择画质',
                  style: TextStyle(
                      color: palette.titleText,
                      fontSize: 15,
                      fontWeight: FontWeight.w600)),
            ),
            for (var i = 0; i < media.qualities.length; i++)
              ListTile(
                title: Text(media.qualities[i].label,
                    style: TextStyle(color: palette.titleText, fontSize: 14)),
                subtitle: media.qualities[i].sizeBytes > 0
                    ? Text(
                        '${(media.qualities[i].sizeBytes / 1024 / 1024).toStringAsFixed(1)} MB',
                        style: TextStyle(color: palette.hint, fontSize: 12))
                    : null,
                trailing: i == _quality
                    ? Icon(Icons.check, color: palette.accent, size: 20)
                    : null,
                onTap: () => Navigator.of(sheetContext).pop(i),
              ),
          ],
        ),
      ),
    );
    if (picked != null && mounted) setState(() => _quality = picked);
  }
}
