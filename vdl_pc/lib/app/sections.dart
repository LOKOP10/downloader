import 'package:flutter/material.dart';

import '../features/parse/page.dart';
import '../features/placeholder.dart';
import '../features/tasks/page.dart';

/// 一个导航项：图标 + 标题 + 构建内容的回调。
class Section {
  const Section({
    required this.id,
    required this.title,
    required this.icon,
    required this.builder,
  });

  final String id;
  final String title;
  final IconData icon;
  final WidgetBuilder builder;
}

/// 侧栏九个页面，顺序与桌面端保持一致。
const List<Section> kSections = <Section>[
  Section(
    id: 'parse',
    title: '视频解析',
    icon: Icons.search,
    builder: _parse,
  ),
  Section(
    id: 'batch',
    title: '批量下载',
    icon: Icons.download_for_offline_outlined,
    builder: _batch,
  ),
  Section(
    id: 'tasks',
    title: '下载任务',
    icon: Icons.list_alt,
    builder: _tasks,
  ),
  Section(
    id: 'manga',
    title: '漫画阅读器',
    icon: Icons.menu_book_outlined,
    builder: _manga,
  ),
  Section(
    id: 'library',
    title: '本地视频',
    icon: Icons.video_library_outlined,
    builder: _library,
  ),
  Section(
    id: 'webp',
    title: 'WebP 转 MP4',
    icon: Icons.animation,
    builder: _webp,
  ),
  Section(
    id: 'gif',
    title: 'MP4 转 GIF',
    icon: Icons.gif_box_outlined,
    builder: _gif,
  ),
  Section(
    id: 'frames',
    title: '导出原始帧',
    icon: Icons.photo_library_outlined,
    builder: _frames,
  ),
  Section(
    id: 'settings',
    title: '设置',
    icon: Icons.settings_outlined,
    builder: _settings,
  ),
];

Widget _parse(BuildContext c) => const ParsePage();

Widget _batch(BuildContext c) => const PlaceholderPage(
      title: '批量下载',
      subtitle: '按作者主页批量抓取作品，可先勾选再入库。',
      todo: <String>['主页作品列表抓取与分页', '勾选、全选与画质批量设置', '进入下载队列'],
    );

Widget _tasks(BuildContext c) => const TasksPage();

Widget _manga(BuildContext c) => const PlaceholderPage(
      title: '漫画阅读器',
      subtitle: '本地漫画书架，按文件夹导入，记住阅读进度。',
      todo: <String>['书架与自然排序', '阅读器翻页、缩放与连续下滑', '书架记录持久化与迁移'],
    );

Widget _library(BuildContext c) => const PlaceholderPage(
      title: '本地视频',
      subtitle: '下载完成的视频自动入库 · 只收下载目录这一层 · 记住播放位置。',
      todo: <String>[
        '卡片墙、封面与搜索排序',
        '内置播放器（续播、倍速、全屏）',
        '检查文件：补入新视频、清理失效与越界记录',
      ],
    );

Widget _webp(BuildContext c) => const PlaceholderPage(
      title: 'WebP 转 MP4',
      subtitle: '动图逐帧转视频，保留每帧毫秒时长，多张可合并。',
      todo: <String>['多选与列表排序', '帧预览与拼接顺序', 'CRF 画质与背景色'],
    );

Widget _gif(BuildContext c) => const PlaceholderPage(
      title: 'MP4 转 GIF',
      subtitle: '两遍调色板，避免逐帧独立调色导致的闪烁。',
      todo: <String>['帧率 / 宽度 / 时长 / 颜色数', 'Bayer 抖动与循环设置', '批量转换与取消'],
    );

Widget _frames(BuildContext c) => const PlaceholderPage(
      title: '导出原始帧',
      subtitle: '在时间轴上拖两端夹取区间，逐帧导出并回放。',
      todo: <String>['时间轴与区间夹取', '逐帧导出与进度', '按源帧率回放'],
    );

Widget _settings(BuildContext c) => const PlaceholderPage(
      title: '设置',
      subtitle: '保存目录、命名规则、Cookie、并发数与主题。',
      todo: <String>['保存目录与命名规则', '各站点 Cookie 与登录', '并发分段数与 ffmpeg 组件'],
    );
