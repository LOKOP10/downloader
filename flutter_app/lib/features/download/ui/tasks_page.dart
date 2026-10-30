import 'package:flutter/material.dart';

import 'package:vdl/design/colors.dart';
import 'package:vdl/features/download/download_engine.dart';
import 'package:vdl/design/theme.dart';
import 'package:vdl/features/download/task_model.dart';
import 'package:vdl/features/download/task_store.dart';
import 'package:vdl/design/widgets/app_bars.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/app_list_tile.dart';
import 'package:vdl/design/widgets/platform_style.dart';
import 'package:vdl/design/widgets/status_pill.dart';

/// 任务页：真实下载队列（进行中 / 失败 / 已完成）。
class TasksPage extends StatelessWidget {
  const TasksPage({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      appBar: AppTopBar(
        title: '任务',
        actions: [
          ListenableBuilder(
            listenable: TaskStore.instance,
            builder: (context, _) => IconButton(
              tooltip: '清理已完成',
              icon: Icon(Icons.cleaning_services_outlined, color: palette.labelText),
              onPressed: TaskStore.instance.clearFinished,
            ),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: TaskStore.instance,
        builder: (context, _) {
          final jobs = TaskStore.instance.jobs;
          if (jobs.isEmpty) return _empty(palette);
          final running = jobs.where((j) => j.status == TaskStatus.running).toList();
          final failed = jobs.where((j) => j.status == TaskStatus.failed).toList();
          final done = jobs.where((j) => j.status == TaskStatus.done).toList();
          return ListView(
            padding: const EdgeInsets.fromLTRB(
                AppSize.screenH, AppSize.screenTop, AppSize.screenH, 24),
            children: [
              if (running.isNotEmpty) ...[
                AppSectionLabel('进行中 · ${running.length}'),
                AppCardGroup(
                  children: [
                    for (var i = 0; i < running.length; i++)
                      _JobRow(job: running[i], showDivider: i < running.length - 1),
                  ],
                ),
                const SizedBox(height: AppSize.cardGap),
              ],
              if (failed.isNotEmpty) ...[
                AppSectionLabel('失败 · ${failed.length}'),
                AppCardGroup(
                  children: [
                    for (var i = 0; i < failed.length; i++)
                      _JobRow(job: failed[i], showDivider: i < failed.length - 1),
                  ],
                ),
                const SizedBox(height: AppSize.cardGap),
              ],
              if (done.isNotEmpty) ...[
                AppSectionLabel('已完成 · ${done.length}'),
                AppCardGroup(
                  children: [
                    for (var i = 0; i < done.length; i++)
                      _JobRow(job: done[i], showDivider: i < done.length - 1),
                  ],
                ),
              ],
              const SizedBox(height: AppSize.cardGap + 4),
              Text(
                '文件保存到：${TaskStore.instance.downloadDir}',
                style: TextStyle(color: palette.hint, fontSize: 12),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _empty(AppPalette palette) => ListView(
        padding: const EdgeInsets.fromLTRB(
            AppSize.screenH, AppSize.screenTop, AppSize.screenH, 24),
        children: [
          AppCard(
            child: Column(
              children: [
                Icon(Icons.inbox_outlined, color: palette.chevron, size: 36),
                const SizedBox(height: 8),
                Text('还没有任务', style: TextStyle(color: palette.hint, fontSize: 13)),
                const SizedBox(height: 4),
                Text('在首页粘贴链接后点「开始下载」',
                    style: TextStyle(color: palette.tabInactive, fontSize: 12)),
              ],
            ),
          ),
        ],
      );
}

class _JobRow extends StatelessWidget {
  const _JobRow({required this.job, required this.showDivider});

  final DownloadJob job;
  final bool showDivider;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final (statusText, statusColor) = switch (job.status) {
      TaskStatus.running => ('下载中', palette.accent),
      TaskStatus.done => ('已完成', palette.success),
      TaskStatus.failed => ('失败', palette.danger),
      TaskStatus.waiting => ('等待中', palette.warning),
    };
    final progress = job.progress;
    final platform = job.platform;

    return Column(
      children: [
        InkWell(
          onTap: () => _onTap(context),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(
                AppSize.screenH, 12, AppSize.screenH, 12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: platform == null
                        ? palette.labelText
                        : platformColor(platform),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    platform?.glyph ?? '链',
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 14,
                        fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        job.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            color: palette.titleText,
                            fontSize: 14,
                            fontWeight: FontWeight.w500),
                      ),
                      const SizedBox(height: 4),
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              _subtitle(progress),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(color: palette.hint, fontSize: 12),
                            ),
                          ),
                          StatusPill(statusText, color: statusColor),
                        ],
                      ),
                      if (job.status == TaskStatus.running) ...[
                        const SizedBox(height: 8),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(2),
                          child: LinearProgressIndicator(
                            value: progress?.ratio ?? 0,
                            minHeight: 3,
                            backgroundColor: palette.rowDivider,
                            valueColor: AlwaysStoppedAnimation(palette.accent),
                          ),
                        ),
                      ],
                      if (job.status == TaskStatus.failed && job.error != null) ...[
                        const SizedBox(height: 4),
                        Text(
                          job.error!,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(color: palette.danger, fontSize: 12),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        if (showDivider)
          Divider(height: 1, thickness: 1, indent: 64, color: palette.rowDivider),
      ],
    );
  }

  String _subtitle(DownloadProgress? progress) {
    // 外部托管任务（禁漫整本）的单位是「页」，不是字节
    if (job.external) {
      final done = progress?.received ?? 0;
      final total = progress?.total ?? 0;
      if (job.status == TaskStatus.done) return total > 0 ? total.toString() + ' 页' : '已完成';
      if (total <= 0) return job.status == TaskStatus.failed ? '未开始' : '准备中…';
      return done.toString() + ' / ' + total.toString() + ' 页';
    }
    if (job.status == TaskStatus.done) {
      return progress == null ? '已完成' : progress.totalText;
    }
    if (progress == null) return job.status == TaskStatus.failed ? '未开始' : '准备中…';
    final speed = progress.speed > 0 ? ' · ${progress.speedText}' : '';
    return '${progress.receivedText} / ${progress.totalText}$speed';
  }

  void _onTap(BuildContext context) {
    if (job.status == TaskStatus.running) {
      TaskStore.instance.cancel(job.id);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('已取消下载')));
    } else if (job.status == TaskStatus.failed) {
      TaskStore.instance.retry(job.id);
    } else if (job.savePath != null) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text('已保存：${job.savePath}')));
    }
  }
}
