package com.xd.vdl

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Rect
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * 禁漫原图重排（切片还原）。
 *
 * 禁漫把每张图按横条切块后逆序存进文件，直接看是花屏。
 * 这里的搬运顺序与 Dart 侧 JmScramble.plan、以及 Python jmcomic 的
 * decode_and_save 完全一致；只做位移，不重编码内容本身。
 */
object JmImageBridge {

    fun descramble(path: String, num: Int): ByteArray {
        val raw = File(path).readBytes()
        if (num <= 0) return raw
        val source = BitmapFactory.decodeByteArray(raw, 0, raw.size) ?: return raw
        val width = source.width
        val height = source.height
        if (width <= 0 || height <= 0 || height < num) {
            source.recycle()
            return raw
        }
        val out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        val over = height % num
        for (i in 0 until num) {
            var move = height / num
            val sourceTop = height - move * (i + 1) - over
            var targetTop = move * i
            if (i == 0) {
                move += over
            } else {
                targetTop += over
            }
            if (move <= 0) continue
            val sTop = sourceTop.coerceAtLeast(0)
            val sBottom = (sourceTop + move).coerceAtMost(height)
            if (sBottom <= sTop) continue
            val dTop = targetTop.coerceAtLeast(0)
            val dBottom = (targetTop + (sBottom - sTop)).coerceAtMost(height)
            if (dBottom <= dTop) continue
            canvas.drawBitmap(
                source,
                Rect(0, sTop, width, sBottom),
                Rect(0, dTop, width, dBottom),
                null,
            )
        }
        val stream = ByteArrayOutputStream()
        out.compress(Bitmap.CompressFormat.JPEG, 92, stream)
        source.recycle()
        out.recycle()
        return stream.toByteArray()
    }
}
