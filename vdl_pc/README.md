# vdl_pc

PC 端 Flutter 桌面版，按 `douyin-downloader/`（Python + PySide6）的功能重写，
解析与下载链路是纯 Dart 重实现，不调用 Python 代码。

## 现状

能走通：「视频解析」「下载任务」两页，粘链接 → 识别站点 → 选画质 → 分段下载 → ffmpeg 合成 → 落盘。

还是占位页（每页带待办清单）：「批量下载」「漫画阅读器」「本地视频」「WebP 转 MP4」
「MP4 转 GIF」「导出原始帧」「设置」。

解析器：抖音、小红书、B 站、X、Pornhub、hanime1。禁漫只在站点枚举里预留，
整本下载与书架还没做。

## 目录

```
lib/app/                壳层：九页导航 + 内容画布 + 状态条
lib/features/           页面：parse、tasks，其余走 features/placeholder.dart
lib/design/             配色 token、动效、按钮与通用组件
lib/core/parse/         六个站点解析器 + parser_registry 注册表
lib/core/download/      分段下载、HLS 分片、封装合成、任务持久化
lib/core/shared/        站点枚举、链接识别、HTTP 与代理、日志、媒体落盘
lib/core/ffmpeg.dart    ffmpeg 定位：VDL_FFMPEG → 程序目录 tools/ → PATH
lib/core/cookie_bridge.dart  读原桌面端 config.json 里的 Cookie
windows/                Windows 原生 runner（CMake）
test/                   壳层组件测试
```

## 跑起来

环境：Flutter 3.47+（Dart 3.13+）、Visual Studio 2022 的「使用 C++ 的桌面开发」工作负载。

```bash
flutter pub get
flutter run -d windows            # 调试运行
flutter build windows --release   # 产物 build/windows/x64/runner/Release/
flutter test                      # 壳层组件测试
```

音视频合并要用 ffmpeg：设 `VDL_FFMPEG` 指向 `ffmpeg.exe`，或把 `ffmpeg.exe` 放到程序目录的
`tools/` 下，都找不到时回退到 PATH。
