import 'package:flutter/material.dart';

import '../../core/app_state.dart';
import '../../core/download/task_model.dart';
import '../../core/download/task_store.dart';
import '../../design/buttons.dart';
import '../../design/motion.dart';
import '../../design/palette.dart';
import '../../design/widgets.dart';

/// 下载任务页：实时显示队列、进度、速度，可取消与重试。
class TasksPage extends StatelessWidget {
  const TasksPage({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: AppState.tasks,
      builder: (BuildContext context, Widget? _) {
        final jobs = AppState.tasks.jobs;
        final running =
            jobs.where((j) => j.status == TaskStatus.running).length;
        return ListView(
          padding: const EdgeInsets.fromLTRB(
              Space.pageH, Space.xl, Space.pageH, Space.xl),
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Expanded(
                  child: PageHeading(
                    title: '下载任务',
                    subtitle: jobs.isEmpty
                        ? '还没有任务：去「视频解析」粘一个链接试试'
                        : '共 ${jobs.length} 个任务，进行中 $running 个',
                  ),
                ),
                if (jobs.isNotEmpty)
                  GhostButton(
                    icon: Icons.cleaning_services_outlined,
                    label: '清理已完成',
                    onTap: () => AppState.tasks.clearFinished(),
                  ),
              ],
            ),
            const SizedBox(height: Space.xl),
            if (jobs.isEmpty)
              StaggerIn(
                child: Panel(
                  child: Column(
                    children: const <Widget>[
                      Icon(Icons.inbox_outlined,
                          size: 32, color: Palette.textFaint),
                      SizedBox(height: Space.md),
                      Text('队列是空的',
                          style: TextStyle(
                              fontSize: 13, color: Palette.textDim)),
                    ],
                  ),
                ),
              )
            else
              ...jobs.asMap().entries.map(
                (MapEntry<int, DownloadJob> entry) => Padding(
                  padding: const EdgeInsets.only(bottom: Space.sm),
                  child: StaggerIn(
                    index: entry.key,
                    child: _JobCard(job: entry.value),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _JobCard extends StatelessWidget {
  const _JobCard({required this.job});

  final DownloadJob job;

  @override
  Widget build(BuildContext context) {
    final progress = job.progress;
    final double fraction = progress == null || progress.total <= 0
        ? 0
        : (progress.received / progress.total).clamp(0.0, 1.0);
    final Color stateColor = switch (job.status) {
      TaskStatus.done => Palette.success,
      TaskStatus.failed => Palette.danger,
      TaskStatus.running => Palette.accent,
      TaskStatus.waiting => Palette.textFaint,
    };
    final String stateText = switch (job.status) {
      TaskStatus.done => '已完成',
      TaskStatus.failed => '失败',
      TaskStatus.running => '下载中',
      TaskStatus.waiting => '排队中',
    };

    return Panel(
      padding: const EdgeInsets.symmetric(
          horizontal: Space.lg, vertical: Space.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Container(
                width: 6,
                height: 6,
                decoration:
                    BoxDecoration(color: stateColor, shape: BoxShape.circle),
              ),
              const SizedBox(width: Space.sm),
              Expanded(
                child: Text(job.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w500,
                        color: Palette.text)),
              ),
              const SizedBox(width: Space.sm),
              Text(stateText,
                  style: TextStyle(fontSize: 11.5, color: stateColor)),
              const SizedBox(width: Space.md),
              if (job.status == TaskStatus.running)
                GhostButton(
                  icon: Icons.close,
                  label: '取消',
                  onTap: () => AppState.tasks.cancel(job.id),
                )
              else if (job.status == TaskStatus.failed)
                GhostButton(
                  icon: Icons.refresh,
                  label: '重试',
                  onTap: () => AppState.tasks.retry(job.id),
                ),
            ],
          ),
          if (job.status == TaskStatus.running || job.status == TaskStatus.done)
            ...<Widget>[
              const SizedBox(height: Space.sm),
              ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: TweenAnimationBuilder<double>(
                  tween: Tween<double>(begin: 0, end: fraction),
                  duration: Motion.fast,
                  curve: Motion.curve,
                  builder: (BuildContext context, double value, Widget? _) {
                    return LinearProgressIndicator(
                      value: value,
                      minHeight: 4,
                      backgroundColor: Palette.bg,
                      valueColor:
                          AlwaysStoppedAnimation<Color>(stateColor),
                    );
                  },
                ),
              ),
              const SizedBox(height: 6),
              Row(
                children: <Widget>[
                  Text(
                    progress == null
                        ? '准备中…'
                        : _progressText(progress),
                    style: const TextStyle(
                        fontSize: 11.5, color: Palette.textDim),
                  ),
                  const Spacer(),
                  if (job.savePath != null)
                    Flexible(
                      child: Text(job.savePath!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              fontSize: 11, color: Palette.textFaint)),
                    ),
                ],
              ),
            ],
          if (job.error != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(job.error!,
                style: const TextStyle(
                    fontSize: 11.5, color: Palette.danger)),
          ],
        ],
      ),
    );
  }

  static String _progressText(dynamic progress) {
    final int received = progress.received as int;
    final int total = progress.total as int;
    final double speed = (progress.speed as num).toDouble();
    final String size = total > 0
        ? '${_mb(received)} / ${_mb(total)}'
        : _mb(received);
    final String rate = speed > 0 ? ' · ${_mb(speed.round())}/s' : '';
    return '$size$rate';
  }

  static String _mb(int bytes) {
    if (bytes >= 1024 * 1024 * 1024) {
      return '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
    }
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }
}
