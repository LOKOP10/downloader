package com.xd.vdl

import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * 两个原生能力都挂在这里：
 *   - vdl/muxer：B 站 DASH 音视频合并（MediaMuxer）
 *   - vdl/jmdescramble：禁漫图片切片重排（Bitmap）
 *
 * 两个都是 CPU 密集的同步操作，统一切到后台线程，结果回主线程再回 Dart。
 */
class MainActivity : FlutterActivity() {

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        registerMuxer(flutterEngine)
        registerDescramble(flutterEngine)
        registerMedia(flutterEngine)
        registerKeepAlive(flutterEngine)
    }

    /** 下载保活：起/停前台服务（切后台不被系统挂起）。 */
    private fun registerKeepAlive(flutterEngine: FlutterEngine) {
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_KEEPALIVE)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        val text = call.argument<String>("text") ?: "正在下载"
                        val progress = call.argument<Int>("progress") ?: -1
                        DownloadKeepAliveService.startOrUpdate(this, text, progress)
                        result.success(true)
                    }
                    "stop" -> {
                        DownloadKeepAliveService.stop(this)
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun registerMuxer(flutterEngine: FlutterEngine) {
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_MUXER)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "merge" -> {
                        val video = call.argument<String>("videoPath")
                        val audio = call.argument<String>("audioPath")
                        val out = call.argument<String>("outPath")
                        if (video.isNullOrEmpty() || audio.isNullOrEmpty() || out.isNullOrEmpty()) {
                            result.error("bad_args", "缺少文件路径", null)
                        } else {
                            Thread {
                                try {
                                    MuxerBridge.merge(File(video), File(audio), File(out))
                                    mainHandler.post { result.success(true) }
                                } catch (error: Exception) {
                                    val message = error.message ?: error.javaClass.simpleName
                                    mainHandler.post { result.error("merge_failed", message, null) }
                                }
                            }.start()
                        }
                    }
                    "cancel" -> {
                        MuxerBridge.cancel()
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun registerMedia(flutterEngine: FlutterEngine) {
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_MEDIA)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "saveImage" -> {
                        val path = call.argument<String>("path")
                        val name = call.argument<String>("name")
                        val relativeDir = call.argument<String>("relativeDir")
                            ?: "Pictures/视频下载器"
                        if (path.isNullOrEmpty() || name.isNullOrEmpty()) {
                            result.error("bad_args", "缺少文件路径或文件名", null)
                        } else {
                            Thread {
                                try {
                                    val saved = MediaStoreBridge.saveImage(
                                        this,
                                        File(path),
                                        name,
                                        relativeDir,
                                    )
                                    mainHandler.post { result.success(saved) }
                                } catch (error: Exception) {
                                    val message = error.message ?: error.javaClass.simpleName
                                    mainHandler.post { result.error("save_failed", message, null) }
                                }
                            }.start()
                        }
                    }
                    "scanFiles" -> {
                        @Suppress("UNCHECKED_CAST")
                        val paths = call.argument<List<String>>("paths") ?: emptyList()
                        Thread {
                            try {
                                val count = MediaStoreBridge.scanFiles(this, paths)
                                mainHandler.post { result.success(count) }
                            } catch (error: Exception) {
                                val message = error.message ?: error.javaClass.simpleName
                                mainHandler.post { result.error("scan_failed", message, null) }
                            }
                        }.start()
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun registerDescramble(flutterEngine: FlutterEngine) {
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_DESCRAMBLE)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "descramble" -> {
                        val path = call.argument<String>("path")
                        val num = call.argument<Int>("num") ?: 0
                        if (path.isNullOrEmpty()) {
                            result.error("bad_args", "缺少图片路径", null)
                        } else {
                            Thread {
                                try {
                                    val bytes = JmImageBridge.descramble(path, num)
                                    mainHandler.post { result.success(bytes) }
                                } catch (error: Exception) {
                                    val message = error.message ?: error.javaClass.simpleName
                                    mainHandler.post { result.error("descramble_failed", message, null) }
                                }
                            }.start()
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    companion object {
        private const val CHANNEL_MUXER = "vdl/muxer"
        private const val CHANNEL_DESCRAMBLE = "vdl/jmdescramble"
        private const val CHANNEL_MEDIA = "vdl/media"
        private const val CHANNEL_KEEPALIVE = "vdl/keepalive"
    }
}
