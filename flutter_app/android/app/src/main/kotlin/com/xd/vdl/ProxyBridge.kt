package com.xd.vdl

import android.content.Context
import android.net.Uri
import android.provider.Settings
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.ProxySelector

/**
 * 读系统代理。
 *
 * 为什么需要：Dart 的 `HttpClient` 与裸 `SecureSocket` **不会**读 Android 的
 * WiFi 代理设置（默认 DIRECT），所以用户开着 Clash 之类却不填代理时，App 里
 * 所有请求都是直连 —— 表现就是 hanime1 / Pornhub 的 TLS 握手 30 秒超时。
 * 只有 Clash 的 VPN / TUN 全局模式才会透明接管，其余（WiFi 代理）必须由 App 自己读。
 *
 * 返回 `host:port`；没有代理返回 null。
 */
object ProxyBridge {

    fun systemProxy(context: Context): String? {
        // ① ProxySelector：WiFi 代理、部分 ROM 的集中配置都走这里
        try {
            val uri = Uri.parse("https://www.pornhub.com")
            val selected = ProxySelector.getDefault()?.select(java.net.URI(uri.toString()))
            val proxy = selected?.firstOrNull()
            if (proxy != null && proxy != Proxy.NO_PROXY) {
                val address = proxy.address()
                if (address is InetSocketAddress && address.port > 0) {
                    // Kotlin 映射：getHostString() → hostString，getHostName() → hostName（大写 N）
                    val host = address.hostString ?: address.hostName
                    if (!host.isNullOrBlank()) return host + ":" + address.port
                }
            }
        } catch (_: Exception) {
        }
        // ② Settings.Global.HTTP_PROXY（形如 127.0.0.1:7890 或 http://127.0.0.1:7890）
        try {
            val raw = Settings.Global.getString(context.contentResolver, Settings.Global.HTTP_PROXY)
            if (!raw.isNullOrBlank()) {
                val cleaned = raw.trim()
                    .removePrefix("http://")
                    .removePrefix("https://")
                    .trimEnd('/')
                if (cleaned.contains(':') && !cleaned.contains(' ')) return cleaned
            }
        } catch (_: Exception) {
        }
        return null
    }
}
