# lib 目录结构

按**功能域**分包，每个功能一个目录，内部再分 `ui/`（界面）、`data/`（状态与数据）与纯逻辑文件。
跨目录引用一律用 `package:vdl/...` 绝对导入，挪动文件不会连带改一堆相对路径。

```
lib/
├── main.dart                      入口，只有 runApp
├── app.dart                       VdlApp（主题）+ RootShell（四页签外壳）
│
├── design/                        设计系统，与业务无关
│   ├── colors.dart                色板（浅/深各 13 个 token）+ AppPalette
│   ├── theme.dart                 ThemeData + 尺寸常量 AppSize
│   ├── theme_controller.dart      主题模式（跟随系统/浅色/深色）
│   └── widgets/                   通用组件：卡片、列表行、顶栏底栏、状态胶囊、平台色
│
├── shared/                        跨功能的基础设施
│   ├── site.dart                  站点枚举（纯 Dart，无 Flutter 依赖）
│   ├── links.dart                 链接识别：抠 URL、判断站点、判断媒体直链
│   └── net/
│       ├── http.dart              统一 HTTP：UA/Referer/Cookie + 进程内 cookie 罐
│       └── browser_http.dart      Cloudflare 专用：裸 TLS + Chrome 头序（HttpClient 会被 403/410）
│
└── features/                      业务功能，一个目录一件事
    ├── parse/                     站点解析（抖音、小红书、B站、X、Iwara、Pornhub、hanime1）
    │   ├── media_info.dart        MediaInfo / MediaQuality / ParseException（含 referer 覆盖）
    │   ├── douyin_parser.dart     抖音：ttwid 会话 + 分享页 SSR，纯 HTTP 无 WebView
    │   ├── xhs_parser.dart        小红书：短链展开 + 详情页 SSR（__INITIAL_STATE__）
    │   ├── bili_parser.dart       B站：view + playurl（DASH 高清 + durl 兜底）
    │   ├── x_parser.dart          X（推特）：GraphQL(带 Cookie) → syndication → guest GraphQL
    │   ├── iwara_parser.dart      Iwara：详情接口 + X-Version 签名取档位（走 browser_http）
    │   ├── pornhub_parser.dart    Pornhub：flashvars 取直链，mp4 优先 / HLS 兜底
    │   ├── hanime1_parser.dart    hanime1：页面 <source> 三档（走 browser_http）
    │   ├── html_text.dart         HTML 实体还原 / 去标签（og:title 这类字段用得上）
    │   └── parser_registry.dart   「站点 → 解析实现」注册表，新增站点只改这里
    │
    ├── download/                  下载
    │   ├── download_engine.dart   多连接分段下载（Range 探测 / 串行落盘 / 取消）
    │   ├── hls_downloader.dart    HLS：m3u8 逐片下载 + 按序拼成 TS（Pornhub 现况）
    │   ├── media_muxer.dart      DASH 音视频合并（转发安卓原生 MediaMuxer）
    │   ├── task_model.dart        TaskStatus
    │   ├── task_store.dart        任务队列：入队 / 进度 / 取消 / 重试 / 目录 / 命名与扩展名
    │   └── ui/tasks_page.dart     任务页
    │
    ├── home/                      首页（粘贴 → 解析 → 下载）
    │   └── ui/
    │       ├── home_page.dart         只有状态与编排
    │       └── widgets/               input_card / platform_grid / result_card
    │
    ├── library/                   本地漫画书架
    │   ├── manga_book.dart        MangaBook
    │   ├── demo_library.dart      演示数据（接真实扫描后删掉）
    │   └── ui/library_page.dart   书架页
    │
    ├── manga/                     禁漫与本地漫画
    │   ├── data/jm_client.dart    禁漫 App 接口（签名 + AES 解密 + 图片地址）
    │   ├── data/jm_scramble.dart  切片重排算法（与 jmcomic 逐行一致）
    │   ├── data/jm_parser.dart    链接 → 本子/章节
    │   ├── data/jm_downloader.dart 逐页下载 → 重排 → 打包 zip → 入库
    │   ├── data/manga_book.dart   书架条目 + JSON 存取
    │   ├── data/manga_library.dart 书架单例（扫描 / 进度 / 移除）
    │   ├── data/manga_location.dart 漫画压缩包保存位置（可单独设置）
    │   └── ui/                    书架卡片、阅读器、禁漫解析面板
    │
    └── settings/
        └── ui/settings_page.dart  设置页
```

## 新增一个站点要改哪里

1. `features/parse/` 下加一个 `xxx_parser.dart`，返回 `MediaInfo`；
2. `parser_registry.dart` 里加一行 `case`；
3. 首页与下载链路零改动（宫格的绿点会自动亮起）。

## 新增一个页面

在 `features/<功能>/ui/` 下加页面，再到 `app.dart` 的 `_tabs` 与 `IndexedStack` 登记。

## 测试

- `test/widget_test.dart`：四页签切换、主题切换、剪贴板嗅探、结果卡
- `test/links_test.dart`：链接识别（抠 URL / 判站点 / 补协议头）
- `test/xhs_parser_test.dart`：小红书 SSR 解析（离线样本，不联网）
- `test/bili_parser_test.dart`：B站 view + playurl 解析（离线样本，不联网）
- `test/x_parser_test.dart`：X 推文解析 + syndication token（离线样本，不联网）
- `test/iwara_parser_test.dart`：Iwara 作品 ID / X-Version 签名（对照 Python hashlib）/ 档位排序
- `test/pornhub_parser_test.dart`：Pornhub viewkey / flashvars 花括号配对 / 时长与标题
- `test/hanime1_parser_test.dart`：hanime1 视频 ID / `<source>` 档位 / 标题与封面
- `test/download_naming_test.dart`：落盘扩展名与文件名清洗
- `test/manga_test.dart`：禁漫协议解密 / 切片算法 / 自然排序 / 书架 JSON / zip 往返
- `tool/check_douyin.dart`：抖音解析 + 下载端到端实测（`dart run tool/check_douyin.dart`）
- `tool/check_xhs.dart`：小红书解析 + 下载端到端实测（`dart run tool/check_xhs.dart <分享链接>`）
- `tool/check_bili.dart`：B站解析 + 下载端到端实测（`dart run tool/check_bili.dart <链接> [--dry]`）
- `tool/check_x.dart`：X 解析 + 下载端到端实测（`dart run tool/check_x.dart <推文链接> [--dry]`）
- `tool/check_jm.dart`：禁漫解析 + 首图下载实测（`dart run tool/check_jm.dart <车号>`）
- `tool/check_iwara.dart`：Iwara 解析 + 下载端到端（`--proxy` 桌面实测用）
- `tool/check_pornhub.dart`：Pornhub 解析 + HLS 分片下载端到端（`--proxy`）
- `tool/check_hanime1.dart`：hanime1 解析 + 下载端到端（`--proxy`）
- `tool/check_downloader.dart`：下载引擎单连接 vs 多连接一致性 + 取消
