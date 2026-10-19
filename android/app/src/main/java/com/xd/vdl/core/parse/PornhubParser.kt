package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import com.xd.vdl.core.net.SessionCookieJar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Pornhub：纯 HTTP，免登录（与桌面端 sites.py::parse_pornhub 同一套链路）。
 *
 * 1) `GET /view_video.php?viewkey=<id>` → 页面里的 `flashvars_<id>`
 * 2) `mediaDefinitions` 里 `format=="mp4"` 那项是 `get_media?s=<base64>`
 *    签名端点（`hls` 那几项是 m3u8，不用）
 * 3) `GET get_media`（**必须带视频页 Referer + X-Requested-With**）
 *    → 4 档 mp4 直链（240/480/720/1080）
 * 4) 并发 Range 探针取每档精确字节数
 *
 * ⚠️ 两个坑（桌面端踩过，这里原样保留）：
 *  · 该站按 IP 限流：短时间连续请求后页面「能打开但拿不到 flashvars」，
 *    隔几秒重试即可恢复，所以要退避重试而不是一次失败就报错。
 *  · CDN 直链不带视频页 Referer 一律 404，探针与下载都要带。
 */
object PornhubParser : Parser {

    private const val HOST = "https://www.pornhub.com"

    private val VIEWKEY_RE = Regex("viewkey=([A-Za-z0-9]+)", RegexOption.IGNORE_CASE)
    private val EMBED_RE = Regex("pornhub\\.com/embed/([A-Za-z0-9]+)", RegexOption.IGNORE_CASE)
    private val BARE_KEY_RE = Regex("(ph)?([0-9a-f]{13})", RegexOption.IGNORE_CASE)
    private const val RETRIES = 3
    private const val RETRY_WAIT_MS = 1500L

    fun viewkey(text: String): String {
        val raw = text.trim()
        VIEWKEY_RE.find(raw)?.let { return it.groupValues[1] }
        EMBED_RE.find(raw)?.let { return it.groupValues[1] }
        BARE_KEY_RE.matchEntire(raw)?.let { return it.groupValues[1] + it.groupValues[2] }
        return ""
    }

    override suspend fun parse(activity: Activity, url: String): VideoInfo = withContext(Dispatchers.IO) {
        val vk = viewkey(url)
        if (vk.isEmpty()) {
            throw ParseException("无法从链接中识别 Pornhub 视频 ID（应形如 view_video.php?viewkey=xxxx）")
        }
        val pageUrl = "$HOST/view_video.php?viewkey=$vk"

        var html = ""
        var flash: JSONObject? = null
        var lastErr = ""
        for (attempt in 0 until RETRIES) {
            val resp = runCatching {
                Http.call(pageUrl, Platform.PORNHUB,
                    extraHeaders = mapOf("Accept-Language" to "en-US,en;q=0.9"))
            }.getOrNull()
            if (resp == null) {
                lastErr = "连接失败（请确认代理能打开 pornhub.com）"
            } else if (resp.code == 404) {
                throw ParseException("Pornhub 视频 $vk 不存在或已删除")
            } else if (resp.code == 200) {
                html = resp.body
                flash = flashvars(html)
                if (flash != null) break
                lastErr = "页面能打开但没带视频数据（多为限流）"
            } else {
                lastErr = "页面返回 HTTP ${resp.code}"
            }
            if (attempt < RETRIES - 1) Thread.sleep(RETRY_WAIT_MS * (attempt + 1))
        }
        val fv = flash ?: throw ParseException(
            "Pornhub 没返回视频数据：$lastErr。该站会限流，稍等几秒再试；若一直失败请确认代理能打开 pornhub.com")
        runCatching {
            val u = okhttp3.HttpUrl.Companion.run { pageUrl.toHttpUrl() }
            AppLog.i("Pornhub 页面会话 Cookie：${SessionCookieJar.loadForRequest(u).joinToString { it.name }}")
        }

        val defs = fv.optJSONArray("mediaDefinitions") ?: JSONArray()
        val endpoints = mutableListOf<String>()
        val hlsOnly = mutableListOf<String>()
        for (i in 0 until defs.length()) {
            val d = defs.optJSONObject(i) ?: continue
            val format = d.optString("format").lowercase()
            val u = d.optString("videoUrl")
            if (format == "mp4" && u.isNotEmpty()) endpoints.add(u)
            else if (format == "hls") hlsOnly.add(d.optString("quality"))
        }
        if (endpoints.isEmpty()) {
            val extra = if (hlsOnly.isNotEmpty()) "（站点只给了 HLS 流 $hlsOnly，多为会员专享）"
                        else "（可能已下架或受限）"
            throw ParseException("该 Pornhub 视频没有可直接下载的 mp4 地址$extra")
        }

        val apiHeaders = mapOf(
            "Referer" to pageUrl,
            "X-Requested-With" to "XMLHttpRequest",
            "Accept" to "application/json, text/plain, */*",
        )

        val rows = mutableListOf<Pair<String, String>>()
        AppLog.i("Pornhub 页面解析完成，mp4 端点 ${endpoints.size} 个")
        outer@ for (ep in endpoints) {
            for (attempt in 0 until RETRIES) {
                val rr = runCatching { Http.call(ep, Platform.PORNHUB, extraHeaders = apiHeaders) }
                    .onFailure { AppLog.w("Pornhub get_media 异常：${it.message}") }
                    .getOrNull() ?: break
                AppLog.i("Pornhub get_media 第 ${attempt + 1} 次：HTTP ${rr.code}，${rr.body.length} 字节")
                if (!rr.ok) break
                val arr = runCatching { JSONArray(rr.body) }.onFailure {
                    // 限流时会回一整页 HTML（含登录/验证页），别把它打进日志
                    AppLog.w("Pornhub get_media 返回非 JSON（HTTP ${rr.code}，${rr.body.length} 字节）")
                }.getOrNull()
                if (arr != null && arr.length() > 0) {
                    for (i in 0 until arr.length()) {
                        val item = arr.optJSONObject(i) ?: continue
                        val u = item.optString("videoUrl")
                        if (u.isNotEmpty()) rows.add(u to item.optString("quality").trim())
                    }
                    if (rows.isNotEmpty()) break@outer
                }
                if (arr != null) AppLog.w("Pornhub get_media 返回空数组（限流特征）")
                // 空数组同样多为限流，退避后重试
                if (attempt < RETRIES - 1) Thread.sleep(RETRY_WAIT_MS * (attempt + 1))
            }
        }
        if (rows.isEmpty()) {
            throw ParseException("Pornhub 没有返回可下载档位（该站会限流，稍等几秒重试）")
        }

        val seen = HashSet<String>()
        val options = mutableListOf<Quality>()
        for ((u, q) in rows) {
            if (!seen.add(u)) continue
            val label = if (q.isNotEmpty() && q.all { it.isDigit() }) "${q}p" else q.ifEmpty { "视频" }
            options.add(Quality(label, u))
        }
        options.sortByDescending { Regex("(\\d{3,4})").find(it.label)?.groupValues?.get(1)?.toIntOrNull() ?: 0 }
        if (options.isEmpty()) throw ParseException("Pornhub 未解析到可用的下载地址")

        val sizes = probeSizes(options.map { it.url }, pageUrl)
        val merged = options.map { it.copy(sizeBytes = sizes[it.url] ?: 0L) }

        AppLog.i("Pornhub 解析到 ${merged.size} 档：${merged.joinToString { it.label }}")

        VideoInfo(
            platform = Platform.PORNHUB,
            title = fv.optString("video_title").trim().ifEmpty { ogTitle(html) }.ifEmpty { vk },
            author = uploader(html),
            coverUrl = fv.optString("image_url").trim(),
            durationMs = durationMs(html),
            qualities = merged,
        )
    }

    /**
     * 从页面里抠出 `flashvars_<id>` 这个 JS 对象。
     *
     * 它是几千字符的大 JSON，**不能靠正则括到结尾**——页面里还有其他 `};`
     * 会提前截断。这里用花括号配对扫描（跳过字符串内部的括号与转义）。
     */
    private fun flashvars(html: String): JSONObject? {
        val m = Regex("flashvars_\\d+\\s*=\\s*\\{").find(html) ?: return null
        val start = html.indexOf('{', m.range.first)
        if (start < 0) return null
        var depth = 0
        var inStr = false
        var esc = false
        for (i in start until html.length) {
            val c = html[i]
            if (inStr) {
                when {
                    esc -> esc = false
                    c == '\\' -> esc = true
                    c == '"' -> inStr = false
                }
            } else {
                when (c) {
                    '"' -> inStr = true
                    '{' -> depth++
                    '}' -> {
                        depth--
                        if (depth == 0) {
                            return runCatching { JSONObject(html.substring(start, i + 1)) }.getOrNull()
                        }
                    }
                }
            }
        }
        return null
    }

    private fun durationMs(html: String): Long {
        Regex("\"video_duration\"\\s*:\\s*(\\d+)").find(html)?.let {
            return it.groupValues[1].toLongOrNull()?.times(1000) ?: 0L
        }
        Regex("\"duration\"\\s*:\\s*\"PT(\\d+)H(\\d+)M(\\d+)S\"").find(html)?.let {
            val h = it.groupValues[1].toLongOrNull() ?: 0L
            val m = it.groupValues[2].toLongOrNull() ?: 0L
            val s = it.groupValues[3].toLongOrNull() ?: 0L
            return (h * 3600 + m * 60 + s) * 1000
        }
        return 0L
    }

    private fun ogTitle(html: String): String {
        val m = Regex("<meta[^>]+property=\"og:title\"[^>]+content=\"([^\"]*)\"", RegexOption.IGNORE_CASE)
            .find(html) ?: return ""
        return unescape(m.groupValues[1]).trim()
    }

    private fun uploader(html: String): String {
        Regex("\"author\"\\s*:\\s*\"([^\"]{1,80})\"").find(html)?.let { return it.groupValues[1].trim() }
        Regex("class=\"videoUploaderLink\"[^>]*>([^<]{1,80})<").find(html)?.let { return it.groupValues[1].trim() }
        return ""
    }

    private fun unescape(s: String): String = s
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", "\"").replace("&#39;", "'").replace("&nbsp;", " ")

    private suspend fun probeSizes(urls: List<String>, pageUrl: String): Map<String, Long> = coroutineScope {
        val distinct = urls.filter { it.isNotEmpty() }.distinct()
        distinct.map { u ->
            async(Dispatchers.IO) {
                val n = Http.rangeLength(u, Platform.PORNHUB, mapOf("Referer" to pageUrl))
                if (n > 0) u to n else null
            }
        }.awaitAll().filterNotNull().toMap()
    }
}
