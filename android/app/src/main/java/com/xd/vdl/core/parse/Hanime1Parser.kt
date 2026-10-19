package com.xd.vdl.core.parse

import android.app.Activity
import com.xd.vdl.core.AppLog
import com.xd.vdl.core.Platform
import com.xd.vdl.core.model.Quality
import com.xd.vdl.core.model.VideoInfo
import com.xd.vdl.core.net.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext

/**
 * hanime1.me：纯 HTTP，免登录（与桌面端 sites.py::parse_hanime1 同一套链路）。
 *
 * 1) `GET /watch?v=<id>` → 页面 `<video>` 里直接挂着 3 个
 *    `<source type="video/mp4" size="…">`（1080 / 720 / 480，HTML 顺序是乱的）
 * 2) 并发 1 字节 Range 取每档精确字节数（直链带 `?secure=<签名>,<过期>`）
 *
 * 已知边界（与桌面端一致）：
 *  · 页面没有时长字段，duration 留 0，界面显示 `--:--`，不猜。
 *  · 直链是签名 + 时效的，解析后搁太久再下会 403，续传依赖重新解析。
 *  · 没有备用 CDN 地址，所以 urlBackups 为空。
 *  · 站点在 Cloudflare 后面且认 TLS 指纹：桌面端用 curl_cffi 的 chrome 指纹，
 *    安卓端 OkHttp 的 ClientHello 不是浏览器指纹，可能被直接断开。
 *    失败时给出明确提示，不静默。
 */
object Hanime1Parser : Parser {

    private const val HOST = "https://hanime1.me"
    private val ID_RE = Regex("/watch\\?v=([A-Za-z0-9_\\-]+)", RegexOption.IGNORE_CASE)
    private val BARE_ID_RE = Regex("^(\\d{4,10})$")
    private const val TITLE_SUFFIX = " - Hanime1.me"
    private const val RETRIES = 2
    private const val RETRY_WAIT_MS = 1200L

    fun videoId(text: String): String {
        val raw = text.trim()
        ID_RE.find(raw)?.let { return it.groupValues[1] }
        BARE_ID_RE.matchEntire(raw)?.let { return it.groupValues[1] }
        return ""
    }

    override suspend fun parse(activity: Activity, url: String): VideoInfo = withContext(Dispatchers.IO) {
        val vid = videoId(url)
        if (vid.isEmpty()) {
            throw ParseException("无法从链接中识别 hanime1 视频 ID（应形如 hanime1.me/watch?v=123456）")
        }
        val pageUrl = "$HOST/watch?v=$vid"

        var html = ""
        var lastErr = ""
        for (attempt in 0 until RETRIES) {
            val resp = runCatching {
                Http.call(pageUrl, Platform.HANIME1,
                    extraHeaders = mapOf("Accept-Language" to "zh-TW,zh;q=0.9,en;q=0.8"))
            }.getOrNull()
            if (resp == null) {
                lastErr = "连接失败（请确认代理能打开 hanime1.me）"
            } else if (resp.code == 404) {
                throw ParseException("hanime1 视频 $vid 不存在或已删除")
            } else if (resp.code == 200) {
                html = resp.body
                if (sources(html).isNotEmpty()) break
                lastErr = "页面能打开但没有视频源（可能被风控拦了）"
            } else {
                lastErr = "页面返回 HTTP ${resp.code}"
            }
            if (attempt < RETRIES - 1) Thread.sleep(RETRY_WAIT_MS * (attempt + 1))
        }

        val list = sources(html)
        if (list.isEmpty()) {
            throw ParseException("hanime1 没返回可下载的视频源：$lastErr。该站有 Cloudflare 风控，" +
                "稍后重试；若一直失败请确认代理能打开 hanime1.me")
        }

        val seen = HashSet<String>()
        val options = mutableListOf<Quality>()
        for ((height, u) in list) {
            if (!seen.add(u)) continue
            options.add(Quality(if (height > 0) "${height}p" else "视频", u))
        }
        options.sortByDescending { Regex("^(\\d+)p$").find(it.label)?.groupValues?.get(1)?.toIntOrNull() ?: 0 }
        if (options.isEmpty()) throw ParseException("hanime1 未解析到可用的下载地址")

        val sizes = probeSizes(options.map { it.url })
        val merged = options.map { it.copy(sizeBytes = sizes[it.url] ?: 0L) }

        AppLog.i("hanime1 解析到 ${merged.size} 档：${merged.joinToString { it.label }}")

        VideoInfo(
            platform = Platform.HANIME1,
            title = title(html).ifEmpty { vid },
            author = author(html),
            coverUrl = cover(html),
            durationMs = 0L,
            qualities = merged,
        )
    }

    /** 抠出所有 `<source type="video/mp4">`：[(档位高度, 地址)]；size 是档位高度不是字节数 */
    private fun sources(html: String): List<Pair<Int, String>> {
        val out = mutableListOf<Pair<Int, String>>()
        for (tag in Regex("<source\\b[^>]*>", RegexOption.IGNORE_CASE).findAll(html)) {
            val t = tag.value
            val src = Regex("src=\"([^\"]+)\"", RegexOption.IGNORE_CASE).find(t)?.groupValues?.get(1) ?: continue
            val u = unescape(src).trim()
            if (!u.lowercase().contains(".mp4")) continue
            val size = Regex("size=\"(\\d+)\"", RegexOption.IGNORE_CASE)
                .find(t)?.groupValues?.get(1)?.toIntOrNull() ?: 0
            out.add(size to u)
        }
        return out
    }

    private fun title(html: String): String {
        Regex("<h3[^>]*id=\"shareBtn-title\"[^>]*>(.*?)</h3>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
            .find(html)?.let {
                val text = unescape(it.groupValues[1].replace(Regex("<[^>]+>"), "")).trim()
                if (text.isNotEmpty()) return text
            }
        val m = Regex("<meta[^>]+property=\"og:title\"[^>]+content=\"([^\"]*)\"", RegexOption.IGNORE_CASE)
            .find(html) ?: return ""
        var t = unescape(m.groupValues[1]).trim()
        if (t.endsWith(TITLE_SUFFIX)) t = t.dropLast(TITLE_SUFFIX.length).trim()
        return t
    }

    private fun author(html: String): String {
        Regex("<a[^>]*id=\"video-artist-name\"[^>]*>(.*?)</a>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
            .find(html)?.let {
                val text = unescape(it.groupValues[1].replace(Regex("<[^>]+>"), "")).trim()
                if (text.isNotEmpty()) return text
            }
        Regex("<img[^>]*id=\"video-user-avatar\"[^>]*alt=\"([^\"]*)\"", RegexOption.IGNORE_CASE)
            .find(html)?.let { return unescape(it.groupValues[1]).trim() }
        Regex("<img[^>]*alt=\"([^\"]*)\"[^>]*id=\"video-user-avatar\"", RegexOption.IGNORE_CASE)
            .find(html)?.let { return unescape(it.groupValues[1]).trim() }
        return ""
    }

    private fun cover(html: String): String {
        Regex("<video[^>]+poster=\"([^\"]+)\"", RegexOption.IGNORE_CASE).find(html)?.let {
            val u = unescape(it.groupValues[1])
            if (u.startsWith("http")) return u
        }
        Regex("<meta[^>]+property=\"og:image\"[^>]+content=\"([^\"]*)\"", RegexOption.IGNORE_CASE)
            .find(html)?.let { return unescape(it.groupValues[1]).trim() }
        return ""
    }

    private fun unescape(s: String): String = s
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", "\"").replace("&#39;", "'").replace("&#x27;", "'")

    private suspend fun probeSizes(urls: List<String>): Map<String, Long> = coroutineScope {
        urls.filter { it.isNotEmpty() }.distinct().map { u ->
            async(Dispatchers.IO) {
                val n = Http.rangeLength(u, Platform.HANIME1)
                if (n > 0) u to n else null
            }
        }.awaitAll().filterNotNull().toMap()
    }
}
