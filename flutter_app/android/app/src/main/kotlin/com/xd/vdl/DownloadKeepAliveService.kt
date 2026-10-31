package com.xd.vdl

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

/**
 * 下载保活用的前台服务（通知里带下载进度）。
 *
 * 为什么需要：Flutter App 切到后台 / 锁屏后，系统的后台限制会把 Dart isolate
 * 降频甚至冻结，网络请求随之挂起 —— 表现就是「一退到后台下载就停」。
 * 起一个前台服务（带常驻通知）能让进程保持前台优先级：
 *  · 有任务在跑时 startOrUpdate()，通知里显示「正在下载 N 个任务 · 45% · 3.2 MB/s」；
 *  · 全部完成 / 取消 / 失败后由 Dart 侧调 stop()，通知随之消失。
 *
 * [progress] 传 0~100 显示确定进度条；传 -1（或越界）显示不确定进度条。
 * 通知用 IMPORTANCE_LOW（静默、不响铃），渠道建一次即可。
 */
class DownloadKeepAliveService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val text = intent?.getStringExtra(EXTRA_TEXT)?.takeIf { it.isNotBlank() } ?: "正在下载"
        val progress = intent?.getIntExtra(EXTRA_PROGRESS, -1) ?: -1
        startForeground(NOTIFICATION_ID, buildNotification(text, progress))
        // 不要 START_STICKY：被杀掉后由 Dart 侧重新拉起，避免留下空通知
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        super.onDestroy()
    }

    private fun buildNotification(text: String, progress: Int): Notification {
        val launch = packageManager.getLaunchIntentForPackage(packageName)
        val pending = launch?.let {
            PendingIntent.getActivity(
                this,
                0,
                it,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("视频下载器")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .apply { if (pending != null) setContentIntent(pending) }
        if (progress in 0..100) {
            builder.setProgress(100, progress, false)
        } else {
            builder.setProgress(0, 0, true)
        }
        return builder.build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(CHANNEL_ID, "下载任务", NotificationManager.IMPORTANCE_LOW)
        channel.description = "后台下载时显示进度，避免切到后台被系统挂起"
        channel.setShowBadge(false)
        manager.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "vdl_download"
        private const val NOTIFICATION_ID = 4801
        private const val EXTRA_TEXT = "text"
        private const val EXTRA_PROGRESS = "progress"

        /** 起服务或更新通知（服务已在跑时只更新文案与进度条）。 */
        fun startOrUpdate(context: Context, text: String, progress: Int = -1) {
            val intent = Intent(context, DownloadKeepAliveService::class.java)
                .putExtra(EXTRA_TEXT, text)
                .putExtra(EXTRA_PROGRESS, progress)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, DownloadKeepAliveService::class.java))
        }
    }
}
