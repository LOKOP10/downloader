package com.xd.vdl

import android.graphics.ImageFormat
import android.media.Image
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.io.IOException

/**
 * WebP 动画逐帧编码成 mp4（H.264，无音轨）。
 *
 * Dart 侧用 dart:ui 的 ImageCodec 逐帧解码（Skia 原生支持 animated WebP），
 * 把每帧压成统一画布后的 RGBA 字节推过来；这里只做 RGBA 到 YUV420 的转换与编码封装。
 * 系统自带 MediaCodec + MediaMuxer，不依赖 ffmpeg。
 *
 * 帧率固定（默认 20fps），每帧按自身时长折算成重复帧数，保证总时长与原动画一致。
 */
object WebpEncoderBridge {

    private const val MIME = MediaFormat.MIMETYPE_VIDEO_AVC
    private const val DEQUEUE_TIMEOUT_US = 10_000L
    private const val BITRATE_PER_PIXEL = 6
    /** 帧时长缺失时的兜底步长（30fps）。 */
    private const val DEFAULT_STEP_MS = 33

    private var encoder: MediaCodec? = null
    private var muxer: MediaMuxer? = null
    private var trackIndex = -1
    private var muxerStarted = false
    private var width = 0
    private var height = 0
    private var fps = 20
    private var frameIndex = 0L
    private var ptsUs = 0L
    private val bufferInfo = MediaCodec.BufferInfo()

    @Volatile
    private var cancelRequested = false

    fun cancel() {
        cancelRequested = true
    }

    fun start(widthPx: Int, heightPx: Int, fpsValue: Int, outPath: String) {
        cancelRequested = false
        releaseAll()
        if (widthPx <= 0 || heightPx <= 0) throw IOException("画布尺寸无效")
        if (widthPx % 2 != 0 || heightPx % 2 != 0) throw IOException("画布宽高必须为偶数")
        width = widthPx
        height = heightPx
        fps = if (fpsValue in 1..60) fpsValue else 20
        frameIndex = 0
        ptsUs = 0L

        val out = File(outPath)
        out.parentFile?.mkdirs()
        if (out.exists()) out.delete()

        val bitrate = (width * height * fps * BITRATE_PER_PIXEL).coerceIn(500_000, 40_000_000)
        val format = MediaFormat.createVideoFormat(MIME, width, height)
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, pickColorFormat())
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
        format.setInteger(MediaFormat.KEY_FRAME_RATE, fps)
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
        try {
            val codec = MediaCodec.createEncoderByType(MIME)
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.start()
            encoder = codec
            muxer = MediaMuxer(outPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        } catch (error: Exception) {
            releaseAll()
            throw IOException("无法启动编码器：" + (error.message ?: error.javaClass.simpleName))
        }
    }

    /**
     * 推入一帧。rgba 必须是 width*height*4 的 RGBA8888；durationMs 是该帧显示时长。
     *
     * ⚠️ 不做「按固定帧率折成重复帧」：H.264/MP4 支持可变帧率，直接用每帧真实时长
     * 累加做 PTS 就能精确还原原动画节奏。之前按 20fps 四舍五入，31ms 的帧会被抬到
     * 50ms —— 真机实测 193 帧 / 6.0 秒的动图转出来变成 9.65 秒（慢了 61%）。
     */
    fun pushFrame(rgba: ByteArray, durationMs: Int) {
        if (cancelRequested) throw IOException("已取消")
        val codec = encoder ?: throw IOException("编码器未启动")
        val expected = width * height * 4
        if (rgba.size < expected) throw IOException("帧数据不足")

        encodeOne(codec, rgba, ptsUs)
        drain(codec, false)
        // 用本帧时长推进时间轴；时长为 0 时按 30fps 兜底，避免整段时间轴停住。
        val stepMs = if (durationMs > 0) durationMs else DEFAULT_STEP_MS
        ptsUs += stepMs * 1000L
        frameIndex += 1
    }

    /** 发 EOS 并把剩余码流写完。返回是否真的产出了轨道数据。 */
    fun finish(): Boolean {
        val codec = encoder ?: return false
        try {
            // EOS 必须抢到输入缓冲才发得出去：抢不到就先 drain 现有输出再重试，
            // 但不能无限等（单次 dequeue 10ms，之前 50 次就白等半秒）。
            for (attempt in 0 until 20) {
                val index = codec.dequeueInputBuffer(DEQUEUE_TIMEOUT_US * 10)
                if (index >= 0) {
                    codec.queueInputBuffer(index, 0, 0, ptsUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                    break
                }
                drain(codec, false)
            }
            drain(codec, true)
            val ok = muxerStarted
            runCatching { muxer?.stop() }
            runCatching { muxer?.release() }
            muxer = null
            muxerStarted = false
            runCatching { codec.stop() }
            runCatching { codec.release() }
            encoder = null
            return ok
        } catch (error: Exception) {
            releaseAll()
            throw IOException("编码收尾失败：" + (error.message ?: error.javaClass.simpleName))
        }
    }

    private fun encodeOne(codec: MediaCodec, rgba: ByteArray, pts: Long) {
        var index = codec.dequeueInputBuffer(DEQUEUE_TIMEOUT_US * 100)
        var spins = 0
        while (index < 0 && spins < 2000) {
            if (cancelRequested) throw IOException("已取消")
            drain(codec, false)
            index = codec.dequeueInputBuffer(DEQUEUE_TIMEOUT_US * 100)
            spins += 1
        }
        if (index < 0) throw IOException("编码器长时间无空闲输入缓冲")
        val image: Image? = codec.getInputImage(index)
        if (image != null) {
            fillImage(image, rgba)
            // 输入缓冲是 YUV420，容量恒为 w*h*3/2。不能用各 plane 的 limit() 相加：
            // 真机实测那样算出来是 1487998（y/u/v 视图 limit 重叠计算），超过 codec
            // 实际容量 1116000，直接报「buffer offset and size goes beyond the capacity」。
            codec.queueInputBuffer(index, 0, width * height * 3 / 2, pts, 0)
        } else {
            val buffer = codec.getInputBuffer(index) ?: throw IOException("编码器输入缓冲不可用")
            buffer.clear()
            buffer.put(toPlanarYuv420(rgba))
            codec.queueInputBuffer(index, 0, buffer.position(), pts, 0)
        }
    }

    /** 按 Image 的平面写入，兼容 pixelStride 为 1（planar）与 2（semi-planar）的实现。 */
    private fun fillImage(image: Image, rgba: ByteArray) {
        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]
        val yBuf = yPlane.buffer
        val uBuf = uPlane.buffer
        val vBuf = vPlane.buffer
        val yRow = yPlane.rowStride
        val yPix = yPlane.pixelStride
        val uvRow = uPlane.rowStride
        val uPix = uPlane.pixelStride
        val vPix = vPlane.pixelStride

        for (y in 0 until height) {
            var src = y * width * 4
            var dst = y * yRow
            for (x in 0 until width) {
                val r = rgba[src].toInt() and 0xFF
                val g = rgba[src + 1].toInt() and 0xFF
                val b = rgba[src + 2].toInt() and 0xFF
                if (dst < yBuf.limit()) {
                    yBuf.put(dst, clamp(((66 * r + 129 * g + 25 * b + 128) shr 8) + 16))
                }
                src += 4
                dst += yPix
            }
        }

        val cw = (width + 1) / 2
        val ch = (height + 1) / 2
        for (cy in 0 until ch) {
            for (cx in 0 until cw) {
                var rSum = 0
                var gSum = 0
                var bSum = 0
                var n = 0
                for (dy in 0..1) {
                    val yy = cy * 2 + dy
                    if (yy >= height) continue
                    for (dx in 0..1) {
                        val xx = cx * 2 + dx
                        if (xx >= width) continue
                        val o = (yy * width + xx) * 4
                        rSum += rgba[o].toInt() and 0xFF
                        gSum += rgba[o + 1].toInt() and 0xFF
                        bSum += rgba[o + 2].toInt() and 0xFF
                        n += 1
                    }
                }
                if (n == 0) continue
                val r = rSum / n
                val g = gSum / n
                val b = bSum / n
                val u = clamp(((-38 * r - 74 * g + 112 * b + 128) shr 8) + 128)
                val v = clamp(((112 * r - 94 * g - 18 * b + 128) shr 8) + 128)
                val uPos = cy * uvRow + cx * uPix
                if (uPos < uBuf.limit()) uBuf.put(uPos, u)
                val vPos = cy * uvRow + cx * vPix
                if (vPos < vBuf.limit()) vBuf.put(vPos, v)
            }
        }
    }

    /** 兜底路径：拿不到 Image 时按 I420 planar 顺序写入。 */
    private fun toPlanarYuv420(rgba: ByteArray): ByteArray {
        val out = ByteArray(width * height * 3 / 2)
        var yi = 0
        val uStart = width * height
        val vStart = uStart + uStart / 4
        for (y in 0 until height) {
            for (x in 0 until width) {
                val o = (y * width + x) * 4
                val r = rgba[o].toInt() and 0xFF
                val g = rgba[o + 1].toInt() and 0xFF
                val b = rgba[o + 2].toInt() and 0xFF
                out[yi] = clamp(((66 * r + 129 * g + 25 * b + 128) shr 8) + 16)
                yi += 1
            }
        }
        var ci = 0
        for (cy in 0 until (height + 1) / 2) {
            for (cx in 0 until (width + 1) / 2) {
                var rSum = 0
                var gSum = 0
                var bSum = 0
                var n = 0
                for (dy in 0..1) {
                    val yy = cy * 2 + dy
                    if (yy >= height) continue
                    for (dx in 0..1) {
                        val xx = cx * 2 + dx
                        if (xx >= width) continue
                        val o = (yy * width + xx) * 4
                        rSum += rgba[o].toInt() and 0xFF
                        gSum += rgba[o + 1].toInt() and 0xFF
                        bSum += rgba[o + 2].toInt() and 0xFF
                        n += 1
                    }
                }
                if (n == 0) n = 1
                val r = rSum / n
                val g = gSum / n
                val b = bSum / n
                if (uStart + ci < out.size && vStart + ci < out.size) {
                    out[uStart + ci] = clamp(((-38 * r - 74 * g + 112 * b + 128) shr 8) + 128)
                    out[vStart + ci] = clamp(((112 * r - 94 * g - 18 * b + 128) shr 8) + 128)
                }
                ci += 1
            }
        }
        return out
    }

    private fun drain(codec: MediaCodec, endOfStream: Boolean) {
        // 非 EOS：拿不到输出就立刻返回（编码器会异步出帧）。
        // EOS：最多空转 100 次（约 1 秒）收尾，绝不无限等 —— 之前写成 10000 次，
        // 真机上结束阶段会白卡 100 秒。
        var idle = 0
        val maxIdle = if (endOfStream) 100 else 1
        while (idle < maxIdle) {
            val index = codec.dequeueOutputBuffer(bufferInfo, DEQUEUE_TIMEOUT_US)
            if (index == MediaCodec.INFO_TRY_AGAIN_LATER) {
                idle += 1
                continue
            }
            idle = 0
            if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                val mux = muxer ?: throw IOException("封装器已释放")
                if (!muxerStarted) {
                    trackIndex = mux.addTrack(codec.outputFormat)
                    mux.start()
                    muxerStarted = true
                }
                continue
            }
            if (index >= 0) {
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && bufferInfo.size > 0 && muxerStarted) {
                    val isConfig = bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0
                    if (!isConfig) {
                        val mux = muxer ?: throw IOException("封装器已释放")
                        buffer.position(bufferInfo.offset)
                        buffer.limit(bufferInfo.offset + bufferInfo.size)
                        mux.writeSampleData(trackIndex, buffer, bufferInfo)
                    }
                }
                codec.releaseOutputBuffer(index, false)
                if (bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) return
            }
        }
    }

    private fun pickColorFormat(): Int {
        val probe = MediaCodec.createEncoderByType(MIME)
        try {
            val caps = probe.codecInfo.getCapabilitiesForType(MIME)
            val formats = caps.colorFormats.toList()
            if (formats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)) {
                return MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible
            }
            if (formats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar)) {
                return MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar
            }
            if (formats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar)) {
                return MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar
            }
            if (formats.contains(ImageFormat.YUV_420_888)) return ImageFormat.YUV_420_888
            return if (formats.isEmpty()) MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible else formats[0]
        } finally {
            runCatching { probe.release() }
        }
    }

    private fun clamp(value: Int): Byte {
        return value.coerceIn(0, 255).toByte()
    }

    private fun releaseAll() {
        runCatching { muxer?.stop() }
        runCatching { muxer?.release() }
        muxer = null
        muxerStarted = false
        trackIndex = -1
        runCatching { encoder?.stop() }
        runCatching { encoder?.release() }
        encoder = null
    }
}
