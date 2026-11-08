# 配置与运行说明

> 从根 README 拆出来。桌面端配置项默认值以 `app/core/config.py` 的 `DEFAULT_CONFIG` 为准。

## 配置文件

桌面端 `config.json` 的默认值与 `app/core/config.py` 的 `DEFAULT_CONFIG` 一致（那里是默认值唯一来源）：

```jsonc
{
  "download_path": "",              // 留空则用 <用户目录>/Downloads/DouyinDownloader
  "cookie": "",                     // 抖音 Cookie
  "x_cookie": "",                   // X(Twitter)
  "ins_cookie": "",                 // Instagram
  "bili_cookie": "",                // Bilibili
  "xhs_cookie": "",                 // 小红书
  "jm_cookie": "",                  // 禁漫天堂（AVS）
  "naming_rule": "unique_id",       // unique_id / timestamp / author_title / title / id_title
  "max_concurrent": 3,              // 同时下载数
  "theme": "dark",                  // dark / light / auto
  "accent_color": "deepsea",        // 界面强调色（见 view/design.ACCENTS，共 9 套）
  "create_author_folder": true,     // 按作者建子目录
  "download_cover": false,          // 同时下载封面
  "auto_copy_file": true,           // 下载完成后复制文件到剪贴板
  "mica_effect": false,             // Win11 云母特效（会降低滚动流畅度）
  "reduce_motion": false,           // 关闭界面动效
  "timeout": 15,
  "segment_download": true,         // 多连接分段下载（IDM 式加速）
  "segment_connections": 8          // 每文件并发连接数，1-32
}
```

`config.json` 里会存 Cookie，不要提交到公开仓库。

安卓端的对应设置存在 `SharedPreferences`（Cookie 在 `vdl_cookies`，保存目录、并发分段数等各占一个键）。

## 几点说明

- Cookie 在桌面端明文存在 `config.json`，安卓端存在 `SharedPreferences`，都不加密。
- 桌面端用 Playwright 驱动本机 Edge 时会生成 `edge_profile*/`（每个平台一个），里面是登录态，已在 `.gitignore` 排除。
  新增平台时记得同步 `core/config.py` 的属性与 `installer/setup.iss` 卸载逻辑，否则卸载后会残留登录凭据。
- 禁漫的 `SECRET`（如 `185Hcomic3PAPP7R`，见 `flutter_app/lib/features/manga/data/jm_client.dart` 与
  `jmcomic_bridge.py`）是该 App 通信协议的公开逆向常量，不是个人凭据。
- 桌面端支持多连接分段下载；若需要代理的站点报 `CONNECT tunnel failed, response 502`，多半是本地代理端口变了
  而系统代理没跟着更新 —— `core/proxy.py` 会自动探测可用端口。
