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
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URLEncoder
import java.security.MessageDigest

/**
 * Iwara：纯 HTTP，免登录（与桌面端 sites.py::parse_iwara 同一套链路）。
 *
 * 1) `GET api.iwara.tv/video/<id>` → 标题 / 作者 / 原始文件信息 + fileUrl
 * 2) 带 X-Version 签名请求 fileUrl → 档位表（Source / 540 / 360 / preview）
 * 3) 并发 Range 探针取每档精确字节数（该 CDN 挡 HEAD）
 *
 * `preview` 是站点自己的预览片段，直接丢掉；`Source` 就是上传的原始文件。
 * 服务端每次请求都会重新签发一批随机命名的 CDN 域名，多取一轮就多一组备用
 * 地址——实测个别域名会连不上，有备用才不至于整单失败。
 */
object IwaraParser : Parser {

    private const val API = "https://api.iwara.tv"
    private const val HOST = "https://www.iwara.tv"

    /** X-Version 签名盐（算法来自前端 main.js，与桌面端一致） */
    private const val SIGN_SALT = "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN"

    private val ID_RE = Regex("iwara\\.tv/(?:video|image)/([A-Za-z0-9]+)", RegexOption.IGNORE_CASE)
    private val SKIP_FORMATS = setOf("preview")
    private const val FORMAT_ROUNDS = 2

    fun videoId(url: String): String = ID_RE.find(url)?.groupValues?.get(1).orEmpty()

    override suspend fun parse(activity: Activity, url: String): VideoInfo = withContext(Dispatchers.IO) {
        val vid = videoId(url)
        if (vid.isEmpty()) {
            throw ParseException("无法从链接中识别 Iwara 作品 ID（应形如 iwara.tv/video/xxxx）")
        }

        var detail = detail(vid)
        // ⚠️ Android 的 org.json 对 JSON null 取 optString 会得到字符串 "null"，
        // 不能只用 isNotEmpty() —— 会把普通作品误判成外链投稿。
        val embed = detail.optString("embedUrl")
        if (!detail.isNull("embedUrl") && embed.isNotEmpty() && embed != "null") {
            throw ParseException("该 Iwara 作品是外链投稿（正文里给了网盘/外部链接），无法直接下载")
        }
        var formats = formats(detail)
        if (formats.isEmpty()) {
            // fileUrl 的签名可能过期：重取一次详情再试
            detail = detail(vid)
            formats = formats(detail)
        }
        if (formats.isEmpty()) {
            throw ParseException("Iwara 没返回可下载档位。可能作品刚上传还在转码，或需要登录才能查看")
        }

        val names = formats.keys.filter { it.lowercase() !in SKIP_FORMATS }
            .sortedByDescending { formatRank(it) }
        if (names.isEmpty()) throw ParseException("Iwara 只返回了预览片段，没有可下载的完整视频")

        val file = detail.optJSONObject("file") ?: JSONObject()
        val width = file.optInt("width")
        val height = file.optInt("height")

        val options = mutableListOf<Quality>()
        val backups = HashMap<String, List<String>>()
        for (name in names) {
            val urls = formats[name].orEmpty().filter { it.isNotEmpty() }
            if (urls.isEmpty()) continue
            val label = qualityLabel(name, width, height)
            options.add(Quality(label, urls.first()))
            if (urls.size > 1) backups[urls.first()] = urls.drop(1)
        }
        if (options.isEmpty()) throw ParseException("Iwara 未解析到可用的下载地址")

        // 并发探针取精确大小（拿不到就不显示，不影响下载）
        val sizes = probeSizes(options.map { it.url })

        val merged = options.map { q ->
            q.copy(
                sizeBytes = sizes[q.url] ?: 0L,
                urlBackups = backups[q.url].orEmpty(),
            )
        }

        val user = detail.optJSONObject("user") ?: JSONObject()
        val fid = file.optString("id")
        val thumb = detail.optInt("thumbnail", -1)
        val cover = if (fid.isNotEmpty() && thumb >= 0) {
            "https://files.iwara.tv/image/original/$fid/thumbnail-%02d.jpg".format(thumb)
        } else ""

        AppLog.i("Iwara 解析到 ${merged.size} 档：${merged.joinToString { it.label }}")

        VideoInfo(
            platform = Platform.IWARA,
            title = detail.optString("title").trim().ifEmpty { vid },
            author = user.optString("name").ifEmpty { user.optString("username") }.trim(),
            coverUrl = cover,
            durationMs = if (file.has("duration")) (file.optDouble("duration", 0.0) * 1000).toLong() else 0L,
            qualities = merged,
        )
    }

    /** 作品详情；404 / 非 JSON / 连不上都给中文提示 */
    private fun detail(vid: String): JSONObject {
        val resp = runCatching {
            Http.call("$API/video/$vid", Platform.IWARA,
                extraHeaders = mapOf("Accept" to "application/json, text/plain, */*"))
        }.getOrElse { e ->
            throw ParseException("Iwara 接口连接失败（${e.message}）。iwara.tv 需要能出网，请确认代理已开启")
        }
        if (resp.code == 404) throw ParseException("Iwara 作品 $vid 不存在或已删除")
        if (!resp.ok) throw ParseException("Iwara 详情接口返回 HTTP ${resp.code}")
        return runCatching { JSONObject(resp.body) }
            .getOrElse { throw ParseException("Iwara 详情接口返回的不是 JSON（可能被拦截，检查代理）") }
    }

    /**
     * 请求 fileUrl 拿档位表：档位名 → [主地址, 备用地址, …]。
     * 每一轮都会换一批 CDN 域名，所以多请求一轮就多一组备用地址。
     */
    private fun formats(detail: JSONObject): Map<String, MutableList<String>> {
        val fileUrl = detail.optString("fileUrl").trim()
        if (fileUrl.isEmpty()) return emptyMap()
        val uri = runCatching { URI(fileUrl) }.getOrNull() ?: return emptyMap()
        val query = parseQuery(uri.rawQuery)
        val expires = query["expires"].orEmpty()
        val fid = uri.path.orEmpty().trimEnd('/').substringAfterLast('/')
        if (expires.isEmpty() || fid.isEmpty()) return emptyMap()

        val base = "${uri.scheme}://${uri.authority}${uri.path}?expires=$expires&hash=${query["hash"].orEmpty()}"
        // 前端会把 download 参数设成 "Iwara - <标题> [<id>].mp4"（空格用 + 编码）
        val dlName = URLEncoder.encode(
            "Iwara - ${detail.optString("title")} [${detail.optString("id")}].mp4", "UTF-8")
        val headers = mapOf(
            "X-Version" to sign(fid, expires),
            "Accept" to "application/json, text/plain, */*",
        )

        val out = LinkedHashMap<String, MutableList<String>>()
        repeat(FORMAT_ROUNDS) {
            val resp = runCatching {
                Http.call("$base&download=$dlName", Platform.IWARA, extraHeaders = headers)
            }.getOrNull() ?: return@repeat
            if (!resp.ok) return@repeat
            val arr = runCatching { JSONArray(resp.body) }.getOrNull() ?: return@repeat
            for (i in 0 until arr.length()) {
                val f = arr.optJSONObject(i) ?: continue
                val name = f.optString("name").trim()
                val src = f.optJSONObject("src") ?: continue
                val u = abs(src.optString("download").ifEmpty { src.optString("view") })
                if (name.isEmpty() || u.isEmpty()) continue
                val bucket = out.getOrPut(name) { mutableListOf() }
                if (!bucket.contains(u)) bucket.add(u)
            }
        }
        return out
    }

    private fun sign(fileId: String, expires: String): String {
        val raw = "${fileId}_${expires}_$SIGN_SALT"
        val md = MessageDigest.getInstance("SHA-1")
        return md.digest(raw.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    private fun abs(u: String): String {
        val s = u.trim()
        return when {
            s.startsWith("//") -> "https:$s"
            s.startsWith("/") -> HOST + s
            else -> s
        }
    }

    /** 档位排序键（越大越好）：Source 最高，然后按分辨率数字降序 */
    private fun formatRank(name: String): Int {
        val low = name.lowercase()
        if (low == "source") return 1_000_000
        return Regex("(\\d{3,4})").find(low)?.groupValues?.get(1)?.toIntOrNull() ?: 0
    }

    private fun qualityLabel(name: String, width: Int, height: Int): String {
        val low = name.lowercase()
        if (low == "source") {
            return if (width > 0 && height > 0) "${width}×${height} 原始" else "原始画质"
        }
        val n = Regex("(\\d{3,4})").find(low)?.groupValues?.get(1)
        return if (n != null) "${n}p" else name.ifEmpty { "视频" }
    }

    private suspend fun probeSizes(urls: List<String>): Map<String, Long> = coroutineScope {
        val distinct = urls.filter { it.isNotEmpty() }.distinct()
        distinct.map { u ->
            async(Dispatchers.IO) {
                val n = Http.rangeLength(u, Platform.IWARA)
                if (n > 0) u to n else null
            }
        }.awaitAll().filterNotNull().toMap()
    }

    private fun parseQuery(raw: String?): Map<String, String> {
        if (raw.isNullOrEmpty()) return emptyMap()
        return raw.split('&').mapNotNull { seg ->
            val i = seg.indexOf('=')
            if (i <= 0) null else seg.substring(0, i) to seg.substring(i + 1)
        }.toMap()
    }
}
