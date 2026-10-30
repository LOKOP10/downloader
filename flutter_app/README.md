# 视频下载器 · Flutter 重做版（Android）

用 Flutter 重写的安卓端：粘贴分享链接 → 解析 → 多连接下载。
与旧的 Kotlin 原生版（`com.xd.vdl`）包名不同（本包 `com.xd.vdl.flutter`），可共存安装。

## 已支持的站点

| 站点 | 解析方式 | 是否需要登录 |
|---|---|---|
| 抖音 | 分享页 SSR（ttwid 会话 + `_ROUTER_DATA`），纯 HTTP | 游客可用 |
| 小红书 | 笔记详情页 SSR（`window.__INITIAL_STATE__`），短链自动展开 | 游客可用，带 `xsec_token` 更稳 |
| B站 | view + playurl 接口；DASH（1080P/1080P+/4K，音视频分离，下完用原生 MediaMuxer 合并）+ durl 兜底（720P，单文件），b23.tv 短链自动展开 | 720P 游客可用；1080P 以上需填 `SESSDATA` |
| X（推特） | syndication 公开接口（token = `((id/1e15)*PI)` 的 36 进制），t.co 短链自动展开 | 免登录；敏感/受限推文取不到 |
| Iwara | 详情接口 + `X-Version` 签名取档位表（Source / 540 / 360），并发 Range 探针取真实大小 | 免登录 |
| Pornhub | 播放页 `flashvars` 取直链；2026-10 起站点只发 HLS，自动改走 m3u8 分片拼接 | 免登录，按 IP 限流 |
| hanime1 | `/watch?v=<id>` 页面里的 3 个 `<source>`（1080/720/480）+ 1 字节 Range 探针 | 免登录，页面无时长字段 |
| 禁漫天堂 | 移动端 App 接口（签名 + AES 解密 + 图片切片重排），多章本子逐章合并 | 游客可下大部分；官方整本打包接口需登录 |

设置页的「支持的平台」宫格里，已接入的站点右上角有绿点；新增站点只需写一个
解析器 + 在 `lib/features/parse/parser_registry.dart` 登记一行，首页与下载链路零改动。

剪贴板嗅探**只认上表这 8 个平台**：回到前台时读到链接会先判站点，
不属于这些站点的链接（文档、代码仓库、新闻…）既不提示也不回填输入框
（以前拿「能抠出 URL」当判据，随手复制的网页链接也会弹提示）。

## 目录

见 `lib/README.md`（按功能域分包，跨目录一律走 `package:vdl/...` 绝对导入）。

## 构建

```bash
flutter build apk --release
# 产物：build/app/outputs/flutter-apk/app-release.apk
```

## 测试与实测脚本

```bash
flutter test                                  # 单元 / 组件测试
dart run tool/check_douyin.dart [分享链接]     # 抖音解析 + 下载端到端
dart run tool/check_xhs.dart <分享链接>        # 小红书解析 + 下载端到端
dart run tool/check_iwara.dart <作品链接> --proxy 127.0.0.1:7897   # Iwara
dart run tool/check_pornhub.dart <作品链接> --proxy 127.0.0.1:7897 # Pornhub（HLS）
dart run tool/check_hanime1.dart <作品链接> --proxy 127.0.0.1:7897 # hanime1
dart run tool/check_downloader.dart           # 下载引擎单/多连接一致性
```

> 桌面跑实测脚本要带 `--proxy`（本地代理端口按自己的改）；真机不需要，
> 流量由系统 VPN / 全局代理接管。

## 落盘命名

- 单文件：`下载目录/标题.<真实格式>`，扩展名按地址判断（`TaskStore.extensionFor`）。
- HLS 作品（Pornhub 现况）：`下载目录/标题.ts` —— 分片就是 MPEG-TS，按序拼接即可播放，
  项目不引 ffmpeg，所以**不假装是 mp4**（ExoPlayer / VLC 都能播）。
- 图集：`下载目录/标题_001.jpg`、`标题_002.jpg` … **平铺在下载目录**（不建子文件夹），逐张串行下载。
- 小红书图片地址的**路径里没有扩展名**，格式只写在尾巴上
  （`...!nd_dft_wlteh_jpg_3` / `...!nd_prv_wlteh_webp_3`），所以先认尾巴，
  认不出按图片兜底 `.jpg`；以前一律兜底 `.mp4`，图会被存成 `001.mp4`。
- 标题里的 `#话题[话题]#` 标记会被清掉，避免文件名变成「#职场冷知识科普[话题]# …」。
## 禁漫与本地漫画书架

### 下载（禁漫）

- 走移动端 App 接口，协议三件套缺一不可：`token=md5(ts+密钥)` 签名、
  AES-256-ECB 响应解密（密钥由时间戳派生）、`/chapter_view_template` 用**另一把密钥**且返回 HTML；
- 图片是按横条切块后**逆序**存的，直接存是花屏 —— 重排量（2 / 10 / 8 …）由
  `md5(车号+文件名去扩展名)` 末位决定，算法与 Python jmcomic 逐行一致（`JmScramble`）；
  像素搬运交给原生 `JmImageBridge`（安卓 Bitmap，通道 `vdl/jmdescramble`）；
- 多章本子（`series` 有多项）**逐章**取图，`total_photos` 是各章之和；
  实际页数与它对不上时会写进状态提示，不会静默少页；
- **任一页最终失败就整本报错、不写 zip**：以前失败页被静默跳过，于是「解析 103 页、
  下完只有 59 页」；现在每页最多试 3 轮（轮换 6 个图片域名 + 退避），仍失败则中止并
  列出缺哪几页，绝不产出残缺压缩包；某章一张图都没取到同样直接报错。
- 产物是一本 `<标题>.zip`，落到「漫画保存位置」（**与普通下载目录分开，可单独设置**）。

### 交互与性能

- **预览**：解析卡上的「预览」是**整本连续下滑**（和 JM 网站一样一条长条往下翻），
  滚到哪加载到哪（3 路并发、LRU 缓存 24 页），点任意一页可全屏双指缩放；
  页清单与下载共用同一份目录，页数一定和最终压缩包一致；
  **两级缓存**：内存 LRU 24 页 + 磁盘缓存（重排后的成品，上限 200MB、按最久未用清理），
  退出 App 再进也能秒开；设置页「清理禁漫预览缓存」可看占用并一键清空；
  **解析了别的链接会自动清掉**（换本子 / 解析其它站点都清，同一本重复解析保留）；
- **并发**：整本下载 **8 路并发**抓图（对齐旧版 `CONCURRENCY = 8`），写包仍严格按页序；
- **进度只在一处**：首页解析卡不显示进度，进度与取消都在「任务」页（外部托管任务，单位是页）；
- **封面**：解析卡的封面用放行证书的客户端抓（`Image.network` 拿不到自签名证书），
  4 个接口域名轮询、失败写 `jm-cover` 日志；书架封面优先读同名 `.cover.jpg`（下载时写好的第一页），
  没有就解包首图并落地缓存。
### 书架（本地漫画）

- **缓存**：封面有内存缓存 + 同名 `.cover.jpg` 落地；阅读页最近 2 本留在内存
  （一本 103 页约 20MB），来回打开不用重新读盘解包，删书/重下会自动失效；
  **每本书卡片右上角有单独的「清理缓存」键**（只放掉这本的内存缓存，不动磁盘文件）；
- 书架**以磁盘为准**：扫描漫画目录里的 `.zip` 与「直接放图片的子目录」，
  JSON 只存阅读进度等附加信息，你直接往目录里丢文件也会出现在书架里；
- 阅读器按自然序翻页（`1.jpg` < `2.jpg` < `10.jpg`），支持双指缩放；
  进度**防抖写盘**（停止翻页 400ms 后写一次，退出时补写），下次打开**直接用
  `PageController(initialPage: 上次页)`** 落到位 —— 以前换实例 + 首帧跳转，会出现
  「每次都停在第 2 页」；
  **底部「保存到相册」按钮**把当前页存进系统相册（走 MediaStore 登记，图库与
  系统搜索都能看到；落点 `Pictures/视频下载器/书名_001.webp`，扩展名沿用原页）。
  注意：直接写 `/storage/emulated/0/Pictures` 不登记 MediaStore 的话，相册是扫不到的；
- 移除分两种：**仅移除书架**（默认，不碰文件）与**移除并删文件**（还要二次确认）；
  目录一律不递归删除，避免误删你自己的东西。
## X（推特）解析要点

三条链路，按「能不能拿到东西」排优先级（`lib/features/parse/x_parser.dart`）：

1. **带 Cookie 的 GraphQL**（`auth_token` + `ct0`）：敏感/受限推文的媒体**只有登录后能取**，
   设置页填了 Cookie 就自动走这条；queryId 会随 X 前端轮换，内置 4 个候选依次试；
2. **syndication 公开接口**（免登录）：普通推文最完整，多图与转推都能展开，
   **必须用 `Googlebot` UA**，浏览器 UA 会被拒；
3. **游客令牌的 GraphQL**：前两条都空时兜底（`POST /1.1/guest/activate.json` 取 guest token）。

- 敏感推文两边都只回 `TweetTombstone`（BlurredMediaTombstone），此时会按「有没有登录」
  给不同提示：没登录 → 引导去设置填 Cookie；填了但缺 `auth_token` → 明确说「等于没登录」。
- `variants` 里**没有 bitrate**（实测全是 undefined），分辨率只能从地址路径的 `/1920x1080/` 抠，
  所以档位按像素量排序；`application/x-mpegURL`（m3u8）变体跳过；
- 接口不给字节数，解析后对每个档位发一次 `Range: bytes=0-0` 拿真实大小（并发，约 0.3s）；
- `TweetTombstone` = 敏感/受限内容，游客态无解，直接提示；
- 一条推文要么是视频（含 GIF → 也是 mp4 变体），要么是图片（最多 4 张，走图集下载）。
## B站 DASH 合并（1080P / 4K）

- 1080P 以上只有 DASH 流，音视频是分开的两个 `.m4s`。
- 下载流程：视频轨 → 音频轨（都放系统临时目录）→ 原生 `MediaMuxer` 合并 → 写进下载目录；
  中间文件用完即删，进度条按「视频+音频」总字节走，合并阶段停在 99%。
- 原生实现在 `android/app/src/main/kotlin/com/xd/vdl/MuxerBridge.kt`，
  通过 `MethodChannel("vdl/muxer")` 暴露 `merge` / `cancel`；删除/合并都不依赖 ffmpeg。
- 同一清晰度有 avc1 / hev1 / av01 三种编码时优先 H.264（合并最稳、老机器也能播）。
- B 站的 `.m4s` 只认**桌面 UA**（安卓 UA 直接 403），所以 B 站下载统一用桌面 UA；
  durl 的 `.mp4` 两种 UA 都收，不影响。
## 与 Windows 端的能力差异

- 安卓端不做 WebView 兜底（抖音 Web 在安卓 WebView 里只下发 SSR 骨架），
  解析走纯 HTTP；失败时按提示换链接或填 Cookie。
- 图文作品只保存静态图，不还原 Live Photo 的视频轨。

## Iwara / Pornhub / hanime1 三个站点的坑

三者都走纯 HTTP，但拦人的地方不一样，代码里各自留了注释，这里只记结论：

- **Cloudflare（iwara / hanime1 / Pornhub 的 HLS CDN）**：同一个 URL、同一组请求头，
  `HttpClient` 一律 403（页面）或 410（m3u8），换成裸 TLS 按 Chrome 的头顺序手写请求就 200
  —— `lib/shared/net/browser_http.dart` 就是干这个的，只给这三家用，普通站点继续走 `AppHttp`。
- **Iwara 签名**：请求档位接口必须带 `X-Version = sha1(<fileId>_<expires>_<salt>)`；
  旧盐仍回 200 但只给 360/preview（静默降级），所以签名值有离线测试对照 Python hashlib。
- **Iwara 备用地址**：档位接口每请求一次就重新签发一批「角色名」CDN 域名，
  取两轮攒备用；探针只能 `Range: bytes=0-0`（该 CDN 挡 HEAD）。
- **Pornhub 发流方式变了（2026-10）**：`mediaDefinitions` 只剩 HLS，唯一的 mp4
  `get_media` 端点会回整页 HTML（桌面端同一时刻也拿不到 mp4，已对照验证）。
  解析器做成「mp4 优先、HLS 兜底」，HLS 交给 `HlsDownloader` 逐片下载 + 按序拼接。
- **Pornhub 限流**：短时间连续请求后页面「能打开但没 flashvars」，隔几秒重试即可恢复，
  所以页面与 `get_media` 都做了退避重试。
- **Pornhub Referer**：CDN 直链不带**作品页** Referer 一律 404，所以 `MediaInfo.referer`
  由解析器显式带下来，下载与预览都用它（不是站点根域）。
- **hanime1**：页面没有时长字段，`duration` 留 0、界面显示 `--`，不猜；
  直链是 `?secure=<签名>,<过期>` 的，解析后搁太久再下会 403，重试要重新解析。

## 保存位置（两套配置，互不影响）

| 配置 | 键 | 管什么 |
|---|---|---|
| 视频 / 图片保存位置 | `download.dir` | 普通视频、图集图片（含 Iwara / Pornhub / hanime1 的成品） |
| 漫画保存位置 | `manga.dir` | 禁漫整本的 `.zip`（书架扫描这个目录） |

设置页两行分别显示**当前生效的完整路径**，保存成功后 toast 也报完整路径。

改目录有两条路：

- **「选择文件夹」**（推荐）：直接打开系统文件管理器（Android 走 SAF 的
  `ACTION_OPEN_DOCUMENT_TREE`），选完自动保存。只会给**能转成可写路径**的目录
  （云盘 / 某些系统目录拿不到路径，会明确提示换一个）。
- **直接填绝对路径**：老路子，保存前会试写校验，写不了就引导去开「所有文件访问」。

⚠️ 踩过的两个坑：

1. **曾经两行改的是同一项**：保存对话框内部写死了 `DownloadLocation.save`，于是点「漫画保存位置」
   实际把**视频目录**改成了漫画路径、漫画目录一直是默认值 —— 视频被存进「漫画」文件夹，
   用户按自己设的路径怎么都找不到。现在对话框由调用方传 `save` / `defaultPath`，并且有回归测试锁住。
2. **Android 公共目录只认系统标准分类名**：`Pictures` / `Movies` / `DCIM` / `Download` / `Documents`。
   自己建一个 `Picture`（少个 s）放视频，相册与系统搜索都找不到。填入近似名时对话框会实时提示
   （只提示、不擅自改用户输入）。
