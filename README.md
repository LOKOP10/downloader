# 视频下载器

个人自用的多站点视频 / 图片下载工具。Windows 桌面端与 Android 端各一套实现，
解析与下载链路完全独立，只共用「怎么做」的思路。

仅供个人学习与备份使用。

## 架构

两端都是「界面 → 核心 → 基础设施」三层，**站点差异全部收敛在解析器里**。

```
桌面端                                     安卓端
┌─────────────────────────┐            ┌─────────────────────────┐
│ main.py                 │            │ lib/main.dart           │
│   └ main_window.py 壳层 │            │   └ 四页签导航          │
├─────────────────────────┤            ├─────────────────────────┤
│ app/view/        界面层 │            │ lib/features/    功能层 │
│   design 设计 token     │            │   parse   站点解析      │
│   workspace 壳层组件    │  ───────   │   download 分段/HLS     │
│   *_interface 九个页面  │   同一思路  │   manga library         │
│ app/core/        核心层 │            │   settings              │
│   parser 识别站点       │            │ lib/design/      设计层 │
│   sites  解析直链       │            │ lib/shared/      共享层 │
│   downloader 分段下载   │            │   链接识别 / HTTP       │
│   各功能引擎            │            │ android/         平台桥 │
├─────────────────────────┤            ├─────────────────────────┤
│ proxy 系统代理          │            │ 封装合成 / 相册登记     │
│ Edge 取 Cookie / ffmpeg │            │ 代理设置 / 前台服务     │
└─────────────────────────┘            └─────────────────────────┘
```

### 桌面端（Python + PySide6）

| 层 | 位置 | 职责 |
|---|---|---|
| 入口 / 壳层 | `main.py`、`app/main_window.py` | 标题栏 + 侧栏 + 内容画布 + 状态条 |
| 界面 | `app/view/` | `design.py` 设计 token、`workspace.py` 壳层组件、`*_interface.py` 九个页面 |
| 解析 | `app/core/parser.py`、`sites.py` | 识别链接所属站点，产出画质与直链 |
| 下载 | `app/core/downloader.py` | IDM 式多连接分段，1–32 连接 |
| 功能引擎 | `app/core/` | `webp_converter` / `mp4_to_gif` / `frame_export` / `video_library` / `manga` |
| 基础设施 | `app/core/` | `proxy.py` 系统代理、`browser_bridge.py` 驱动本机 Edge 取 Cookie、`ffmpeg.py` 合并组件 |

数据流：粘贴链接 → 识别站点 → 解析出画质 → 选画质 → 分段下载 → 落盘 → 自动入库。

### Android 端（Flutter）

| 层 | 位置 | 职责 |
|---|---|---|
| 界面 | `lib/features/*/`、`lib/design/` | 首页 / 任务 / 书架 / 设置四个页签，配色与通用组件 |
| 解析 | `lib/features/parse/` | 一个站点一个解析器，产出统一的媒体信息 |
| 下载 | `lib/features/download/` | 多连接分段、HLS 分片、DASH 合并、任务队列、后台保活 |
| 共享 | `lib/shared/` | 站点枚举、链接识别、HTTP（含 Cloudflare 专用通道）、相册登记 |
| 平台桥 | `android/` | 音视频封装合成、相册写入、代理设置、前台服务 |

数据流：链接 → 识别 → 解析 → 下载（分段 / HLS / DASH）→ 原生合成 → 登记进媒体库。

### 共同约定

- **加一个站点 = 加一个解析器 + 在站点表登记**，其它层不用动。
- 解析与下载解耦：解析层只产出媒体信息与直链，下载层只认地址列表。
- 两端不共享代码：改一个站点要两端分别改。
- 功能引擎各自独立，界面层不直接处理字节流。

## 目录结构

```
.
├── douyin-downloader/     # Windows 桌面端（Python + PySide6）
│   ├── main.py            # 入口
│   ├── app/core/          # 解析 / 下载 / 各功能引擎 / 配置
│   ├── app/view/          # 界面与设计 token
│   ├── installer/         # 安装包脚本
│   ├── tests/             # 单元测试
│   └── app_version.txt    # 版本号唯一来源
├── flutter_app/           # Android 端（Flutter）
│   ├── lib/features/      # 解析 / 下载 / 漫画 / 书架 / 设置 / 工具
│   ├── lib/design/        # 配色、主题、通用组件
│   ├── lib/shared/        # 站点枚举、链接识别、HTTP
│   ├── test/              # 离线单测与组件测试
│   └── android/           # 安卓工程与原生桥
├── docs/                  # 补充文档（见文末索引）
├── release-notes/         # 各版本发布说明
└── website/               # 静态下载页
```

## 快速开始

### Windows 桌面端

环境：Windows 10 / 11、Python 3.10+（开发用 3.13）、本机已装 Microsoft Edge
（直接驱动本机 Edge 取 Cookie，**不需要**额外下载浏览器内核）。

```bash
cd douyin-downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py          # 或双击 run.bat（不弹控制台）
```

打包为文件夹版（推荐；单文件版每次启动要解包到临时目录，慢一倍多）：

```powershell
pyinstaller --noconfirm --distpath dist/<version> build_exe.spec
powershell -ExecutionPolicy Bypass -File installer/sync_dist.ps1
powershell -ExecutionPolicy Bypass -File installer/build_setup.ps1
```

### Android 端

环境：Flutter 3.47（Dart 3.13）、Android SDK 34（`minSdk` 24）、JDK 17+。

```bash
cd flutter_app
flutter pub get
flutter build apk --release    # 产物 build/app/outputs/flutter-apk/app-release.apk
flutter test                   # 离线单测与组件测试
```

### 从 clone 到能跑

仓库只提交源码，**克隆下来不能直接运行**，以下都要自己准备：

| 需要什么 | 怎么来 |
|---|---|
| Python 依赖 | `pip install -r requirements.txt` |
| Flutter 依赖 | `flutter pub get` |
| ffmpeg（B 站 / YouTube 音视频合并、GIF 转换） | 桌面端在「设置 → 合并组件」里下载，不是 pip 依赖 |
| 本机 Edge | 桌面端取 Cookie 用，需系统已安装 |
| 各站点登录态 | 设置页填 Cookie，或用内置登录流程导入 |

`dist/`、`build/`、`.venv/`、`edge_profile*/`、`*.exe`、`*.apk` 等构建产物与敏感目录
都在 `.gitignore` 里。

## 支持的站点

| 站点 | 能力 | 两端 |
|---|---|---|
| 抖音 | 去水印、作者主页批量、图文动图转 GIF | 桌面 ✅ / 安卓 ✅（动图只存静态图） |
| B 站 | DASH 音视频自动合并 | 桌面 ✅ ffmpeg / 安卓 ✅ 原生合成 |
| 小红书 | 需 App 分享链接（含 `xsec_token`） | 两端 ✅ |
| X (Twitter) | 敏感内容需填 `auth_token` / `ct0` | 两端 ✅ |
| Instagram | 需登录内容要填 Cookie | 仅桌面 |
| YouTube | 高画质音视频分离，需 ffmpeg 合并 | 仅桌面 |
| Pornhub | 站点已改 HLS | 桌面只到解析层 / 安卓可下 |
| hanime1 | 需 TLS 指纹，免登录 | 两端 ✅ |
| 禁漫天堂 | 种子号解析；安卓另有整本下载 + 书架 | 两端 ✅ |

## 能力对照

| 能力 | 桌面端 | 安卓端 |
|---|---|---|
| 支持站点 | 9 个 | 7 个 |
| 多连接分段下载 | 1–32 连接 | 4–16 段可调 |
| 音视频合并 | ffmpeg | 原生合成，无需 ffmpeg |
| 后台下载 | 窗口最小化照常跑 | 前台服务 + 通知进度 |
| 本地视频库（封面 / 续播 / 倍速） | ✅ | ❌ |
| WebP 转 MP4 / MP4 转 GIF / 导出原始帧 | ✅ | ❌ |
| 漫画书架与阅读器 | ✅ | ✅ |
| 保存目录 | 设置里选任意可写目录 | 系统文件管理器选目录（SAF） |

## 更多文档

| 文档 | 内容 |
|---|---|
| [`docs/desktop-features.md`](docs/desktop-features.md) | 桌面端完整功能说明（视频库、转换工具、漫画阅读器） |
| [`docs/configuration.md`](docs/configuration.md) | 配置项逐条说明与运行注意事项 |
| [`flutter_app/README.md`](flutter_app/README.md) | 安卓端实现细节与踩坑记录 |

## 第三方代码与许可

本仓库**未声明许可证**（个人自用项目，保留所有权利）。未授权任何人使用、复制、修改或分发。

| 位置 | 来源 | 许可证 | 参与构建 |
|---|---|---|---|
| `douyin-downloader/app/core/xhsapi/` | [cv-cat/Spider_XHS](https://github.com/cv-cat/Spider_XHS)（派生裁剪） | **上游未声明** | 是 |
| `douyin-downloader/reference/ref_xbogus.py` | [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | Apache-2.0 | 否（仅参考） |
| `douyin-downloader/installer/ChineseSimplified.isl` | [Inno Setup 简体中文翻译](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation) | 随上游 | 是 |

> `xhsapi/` 的上游没有 LICENSE 文件，未声明许可证即默认保留所有权利。本仓库目前仅个人自用；
> **若要对外分发或为本仓库加许可证，必须先解决这一条** —— 详见 `app/core/xhsapi/__init__.py`。

## 免责

个人学习与备份用途，解析能力都来自各平台公开的网页接口。下载、存储、传播内容的责任由使用者自负。
