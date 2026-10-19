package com.xd.vdl.core.gif

import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import com.xd.vdl.core.AppLog
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream

/**
 * 纯 Kotlin 的 GIF89a 编码器。
 *
 * **为什么自己写**：桌面端转 GIF 用 ffmpeg（两遍调色板），安卓端没有 ffmpeg，
 * 而系统也没有 GIF 编码 API（ImageDecoder 只能解码）。抖音图文里的 Live Photo
 * 视频轨是 mp4，这里用 [MediaMetadataRetriever] 逐帧解码，量化后写 GIF。
 *
 * 实现要点：
 *  · 每帧独立局部调色板（6×6×6 共 216 色）+ Floyd–Steinberg 误差扩散，
 *    避免固定调色板把肤色压成一片；
 *  · 流式逐帧写盘，不把所有帧读进内存（90 帧 × 540×960 会直接 OOM）；
 *  · 抽帧上限 [MAX_FPS]、宽度上限 [MAX_WIDTH]，控制体积与耗时。
 */
object GifEncoder {

    /** 动图宽度上限（px）：抖音动图原图通常 1080 宽，缩到这里体积可控 */
    const val MAX_WIDTH = 540

    /** 抽帧上限（fps）：GIF 再高也没意义，反而让文件暴涨 */
    const val MAX_FPS = 12

    /** 最多编码多少帧（防止超长视频把任务卡死） */
    private const val MAX_FRAMES = 240

    /**
     * 把 mp4 转成 GIF。成功返回 true；源没有可解码帧时返回 false。
     */
    fun encodeMp4(src: File, dst: File, maxWidth: Int = MAX_WIDTH, maxFps: Int = MAX_FPS): Boolean {
        if (!src.exists() || src.length() <= 0) return false
        val retriever = MediaMetadataRetriever()
        var frames = 0
        try {
            retriever.setDataSource(src.absolutePath)
            val durationMs = retriever
                .extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L
            if (durationMs <= 0) return false
            val stepMs = (1000L / maxFps.coerceAtLeast(1)).coerceAtLeast(40L)
            val delayCs = (100.0 / maxFps.coerceAtLeast(1)).toInt().coerceIn(2, 50)

            dst.parentFile?.mkdirs()
            BufferedOutputStream(FileOutputStream(dst)).use { out ->
                var timeMs = 0L
                var headerWritten = false
                while (timeMs < durationMs && frames < MAX_FRAMES) {
                    val frame = retriever.getFrameAtTime(
                        timeMs * 1000, MediaMetadataRetriever.OPTION_CLOSEST) ?: break
                    val scaled = scaleToWidth(frame, maxWidth)
                    if (scaled !== frame) frame.recycle()

                    if (!headerWritten) {
                        writeHeader(out, scaled.width, scaled.height)
                        writeNetscapeLoop(out)
                        headerWritten = true
                    }
                    val (indices, palette) = quantize(scaled)
                    writeFrame(out, scaled.width, scaled.height, indices, palette, delayCs)
                    scaled.recycle()
                    frames++
                    timeMs += stepMs
                }
                if (!headerWritten) return false
                out.write(0x3B) // Trailer
                out.flush()
            }
            AppLog.i("动图转 GIF：${src.name} → ${dst.name}，${frames} 帧，${dst.length() / 1024} KB")
            return frames > 0
        } catch (e: Exception) {
            AppLog.w("动图转 GIF 失败（${src.name}）：${e.message}")
            runCatching { if (dst.exists()) dst.delete() }
            return false
        } finally {
            runCatching { retriever.release() }
        }
    }

    private fun scaleToWidth(src: Bitmap, maxWidth: Int): Bitmap {
        if (src.width <= maxWidth) return src
        val ratio = maxWidth.toFloat() / src.width
        val h = (src.height * ratio).toInt().coerceAtLeast(1)
        return Bitmap.createScaledBitmap(src, maxWidth, h, true)
    }

    // ------------------------------------------------------------------ //
    // 量化：6×6×6 + Floyd–Steinberg
    // ------------------------------------------------------------------ //

    private const val LEVELS = 6
    private const val STEP = 255 / (LEVELS - 1) // 51

    /** 返回 (每像素调色板索引, 768 字节调色板) */
    private fun quantize(bmp: Bitmap): Pair<ByteArray, ByteArray> {
        val w = bmp.width
        val h = bmp.height
        val pixels = IntArray(w * h)
        bmp.getPixels(pixels, 0, w, 0, 0, w, h)

        val palette = ByteArray(768)
        for (r in 0 until LEVELS) {
            for (g in 0 until LEVELS) {
                for (b in 0 until LEVELS) {
                    val idx = r * LEVELS * LEVELS + g * LEVELS + b
                    palette[idx * 3] = (r * STEP).toByte()
                    palette[idx * 3 + 1] = (g * STEP).toByte()
                    palette[idx * 3 + 2] = (b * STEP).toByte()
                }
            }
        }

        val out = ByteArray(w * h)
        // 误差缓冲：当前行与下一行（浮点），避免整幅图的浮点数组
        val curErr = FloatArray((w + 2) * 3)
        val nextErr = FloatArray((w + 2) * 3)

        for (y in 0 until h) {
            java.util.Arrays.fill(nextErr, 0f)
            val rowStart = y * w
            for (x in 0 until w) {
                val c = pixels[rowStart + x]
                val ai = (x + 1) * 3
                val r = ((c shr 16) and 0xFF) + curErr[ai]
                val g = ((c shr 8) and 0xFF) + curErr[ai + 1]
                val b = (c and 0xFF) + curErr[ai + 2]

                val ri = quantIndex(r)
                val gi = quantIndex(g)
                val bi = quantIndex(b)
                val idx = ri * LEVELS * LEVELS + gi * LEVELS + bi
                out[rowStart + x] = idx.toByte()

                val er = r - ri * STEP
                val eg = g - gi * STEP
                val eb = b - bi * STEP
                // Floyd–Steinberg：右 7/16、左下 3/16、下 5/16、右下 1/16
                spread(curErr, ai + 3, er, eg, eb, 7f / 16f)
                spread(nextErr, ai - 3, er, eg, eb, 3f / 16f)
                spread(nextErr, ai, er, eg, eb, 5f / 16f)
                spread(nextErr, ai + 3, er, eg, eb, 1f / 16f)
            }
            System.arraycopy(nextErr, 0, curErr, 0, curErr.size)
        }
        return out to palette
    }

    private fun quantIndex(v: Float): Int =
        (Math.round(v / STEP).toInt()).coerceIn(0, LEVELS - 1)

    private fun spread(err: FloatArray, at: Int, r: Float, g: Float, b: Float, f: Float) {
        if (at < 0 || at + 2 >= err.size) return
        err[at] += r * f
        err[at + 1] += g * f
        err[at + 2] += b * f
    }

    // ------------------------------------------------------------------ //
    // GIF 结构
    // ------------------------------------------------------------------ //

    private fun writeHeader(out: OutputStream, w: Int, h: Int) {
        out.write("GIF89a".toByteArray(Charsets.US_ASCII))
        writeShort(out, w)
        writeShort(out, h)
        out.write(0x70) // 无全局色表，8 位色深
        out.write(0x00) // 背景色索引
        out.write(0x00) // 像素宽高比
    }

    /** Netscape 扩展：无限循环 */
    private fun writeNetscapeLoop(out: OutputStream) {
        out.write(0x21)
        out.write(0xFF)
        out.write(0x0B)
        out.write("NETSCAPE2.0".toByteArray(Charsets.US_ASCII))
        out.write(0x03)
        out.write(0x01)
        writeShort(out, 0) // 0 = 无限循环
        out.write(0x00)
    }

    private fun writeFrame(
        out: OutputStream,
        w: Int, h: Int,
        indices: ByteArray,
        palette: ByteArray,
        delayCs: Int,
    ) {
        // Graphic Control Extension：disposal=2（恢复背景）、无透明色
        out.write(0x21)
        out.write(0xF9)
        out.write(0x04)
        out.write(0x08)
        writeShort(out, delayCs)
        out.write(0x00)
        out.write(0x00)

        // Image Descriptor：带局部色表，256 色（size=7）
        out.write(0x2C)
        writeShort(out, 0)
        writeShort(out, 0)
        writeShort(out, w)
        writeShort(out, h)
        out.write(0x87)
        out.write(palette, 0, 768)

        // LZW 图像数据
        out.write(8) // min code size
        lzw(out, indices)
    }

    private fun writeShort(out: OutputStream, v: Int) {
        out.write(v and 0xFF)
        out.write((v shr 8) and 0xFF)
    }

    /**
     * GIF LZW 编码（定长 8 位索引）。输出按 255 字节分块，
     * 每块前写长度，最后写 0 结束。
     */
    private fun lzw(out: OutputStream, indices: ByteArray) {
        if (indices.isEmpty()) {
            out.write(0)
            return
        }
        val clearCode = 256
        val eoiCode = 257
        var codeSize = 9
        var nextCode = 258
        val dict = HashMap<Int, Int>(1 shl 13)
        val block = ByteArrayOutputStream(4096)
        var bitBuf = 0
        var bitCnt = 0

        fun emit(code: Int) {
            bitBuf = bitBuf or (code shl bitCnt)
            bitCnt += codeSize
            while (bitCnt >= 8) {
                block.write(bitBuf and 0xFF)
                bitBuf = bitBuf ushr 8
                bitCnt -= 8
            }
        }

        fun flushBlock() {
            if (block.size() == 0) return
            val data = block.toByteArray()
            var off = 0
            while (off < data.size) {
                val n = minOf(255, data.size - off)
                out.write(n)
                out.write(data, off, n)
                off += n
            }
            out.write(0)
            block.reset()
        }

        emit(clearCode)
        var prefix = indices[0].toInt() and 0xFF
        for (i in 1 until indices.size) {
            val k = indices[i].toInt() and 0xFF
            val key = (prefix shl 8) or k
            val existing = dict[key]
            if (existing != null) {
                prefix = existing
            } else {
                emit(prefix)
                if (nextCode < 4096) {
                    dict[key] = nextCode
                    nextCode++
                    if (nextCode - 1 == (1 shl codeSize) && codeSize < 12) codeSize++
                } else {
                    emit(clearCode)
                    dict.clear()
                    codeSize = 9
                    nextCode = 258
                }
                prefix = k
            }
        }
        emit(prefix)
        emit(eoiCode)
        if (bitCnt > 0) block.write(bitBuf and 0xFF)
        flushBlock()
    }
}
