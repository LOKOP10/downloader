package com.xd.vdl

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.media.MediaScannerConnection
import android.provider.MediaStore
import java.io.File
import java.io.IOException

/**
 * 把一张图写进系统相册（MediaStore），让图库 / 系统搜索都能看到。
 *
 * 为什么不能直接 `File` 写 `/storage/emulated/0/Pictures`：Android 10+ 的相册
 * 只认**登记进 MediaStore** 的图片；直接写文件即使成功，图库与搜索也扫不到
 * （用户会以为「保存失败」）。所以这里走 insert + openOutputStream + IS_PENDING。
 *
 * 落点：`Pictures/视频下载器/文件名`（相册「图片」分类下的一级文件夹）。
 */
object MediaStoreBridge {

    /**
     * 保存一张图片，返回给人看的相对路径（如 `Pictures/视频下载器/xxx.webp`）。
     *
     * @throws IOException 插入/写入失败（例如低版本没有存储权限）
     */
    fun saveImage(
        context: Context,
        source: File,
        displayName: String,
        relativeDir: String = "Pictures/视频下载器",
    ): String {
        if (!source.exists() || source.length() <= 0) {
            throw IOException("源文件不存在或为空")
        }
        val resolver = context.contentResolver
        val mime = mimeOf(displayName)
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, displayName)
            put(MediaStore.Images.Media.MIME_TYPE, mime)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Images.Media.RELATIVE_PATH, relativeDir)
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
        }
        val collection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        }
        val uri = resolver.insert(collection, values)
            ?: throw IOException("插入相册失败（可能缺少存储权限）")
        try {
            resolver.openOutputStream(uri)?.use { out ->
                source.inputStream().use { input -> input.copyTo(out) }
            } ?: throw IOException("无法打开相册条目输出流")
        } catch (error: Exception) {
            // 失败就把占位行删掉，别在图库里留下 0 字节僵尸条目
            runCatching { resolver.delete(uri, null, null) }
            throw error
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val done = ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }
            resolver.update(uri, done, null, null)
        }
        return relativeDir.trimEnd('/') + "/" + displayName
    }

    /**
     * 让已经写进公共目录的文件被系统媒体库收录（MediaScanner）。
     *
     * 为什么需要：用 dart:io 往 /storage/emulated/0/Pictures 直写文件，图库 App
     * 有时能自己遍历到，但 **QQ / 微信的「最近图片」只查 MediaStore**，不登记就永远
     * 看不到。下载完成后扫一次，等同系统相册收到这个文件。
     *
     * @return 实际发起扫描的文件数
     */
    fun scanFiles(context: Context, paths: List<String>): Int {
        val existing = paths.filter { it.isNotBlank() && File(it).exists() }
        if (existing.isEmpty()) return 0
        MediaScannerConnection.scanFile(context, existing.toTypedArray(), null, null)
        return existing.size
    }

    private fun mimeOf(name: String): String {
        val lower = name.lowercase()
        return when {
            lower.endsWith(".png") -> "image/png"
            lower.endsWith(".webp") -> "image/webp"
            lower.endsWith(".gif") -> "image/gif"
            lower.endsWith(".bmp") -> "image/bmp"
            else -> "image/jpeg"
        }
    }
}
