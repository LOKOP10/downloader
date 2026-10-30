package com.xd.vdl

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer

/**
 * 把 DASH 分离的视频轨与音频轨合成一个 mp4（B 站 1080P / 4K 用）。
 *
 * 用系统自带的 MediaExtractor + MediaMuxer，不依赖 ffmpeg，也不改源码流。
 * 合并期间每帧都会检查 cancelRequested，用户点取消能立刻中断。
 */
object MuxerBridge {

    @Volatile
    private var cancelRequested = false

    fun cancel() {
        cancelRequested = true
    }

    fun merge(videoFile: File, audioFile: File, outFile: File) {
        cancelRequested = false
        if (outFile.exists()) outFile.delete()
        outFile.parentFile?.mkdirs()

        val videoEx = MediaExtractor()
        val audioEx = MediaExtractor()
        var muxer: MediaMuxer? = null
        try {
            videoEx.setDataSource(videoFile.absolutePath)
            val videoFormat = firstTrack(videoEx, "video/")
                ?: throw IOException("视频轨缺失，无法合并")

            audioEx.setDataSource(audioFile.absolutePath)
            val audioFormat = firstTrack(audioEx, "audio/")
                ?: throw IOException("音频轨缺失，无法合并")

            muxer = MediaMuxer(outFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val videoTrack = muxer.addTrack(videoFormat)
            val audioTrack = muxer.addTrack(audioFormat)
            muxer.start()
            copyTrack(videoEx, muxer, videoTrack)
            copyTrack(audioEx, muxer, audioTrack)
            muxer.stop()
        } finally {
            runCatching { muxer?.release() }
            runCatching { videoEx.release() }
            runCatching { audioEx.release() }
        }
    }

    private fun firstTrack(extractor: MediaExtractor, prefix: String): MediaFormat? {
        for (i in 0 until extractor.trackCount) {
            val format = extractor.getTrackFormat(i)
            val mime = format.getString(MediaFormat.KEY_MIME).orEmpty()
            if (mime.startsWith(prefix)) {
                extractor.selectTrack(i)
                return format
            }
        }
        return null
    }

    private fun copyTrack(extractor: MediaExtractor, muxer: MediaMuxer, trackIndex: Int) {
        val buffer = ByteBuffer.allocate(1 shl 20)
        val info = MediaCodec.BufferInfo()
        while (true) {
            if (cancelRequested) throw IOException("已取消合并")
            val size = extractor.readSampleData(buffer, 0)
            if (size < 0) break
            info.offset = 0
            info.size = size
            info.presentationTimeUs = extractor.sampleTime
            // 必须做语义映射：MediaMuxer 只关心「是否关键帧」。
            // MediaExtractor.SAMPLE_FLAG_SYNC 数值虽也是 1，但语义不同（Lint 会报 WrongConstant）。
            info.flags = if (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) {
                MediaCodec.BUFFER_FLAG_KEY_FRAME
            } else {
                0
            }
            muxer.writeSampleData(trackIndex, buffer, info)
            extractor.advance()
        }
    }
}
