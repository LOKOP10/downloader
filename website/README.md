# 视频下载器官网

纯静态站点（HTML + CSS + 少量 JS），无构建步骤。源码在仓库根目录的 `website/`。

## 本地预览

```bash
cd website
python -m http.server 8811
# 浏览器打开 http://127.0.0.1:8811/
```

## 部署

```bash
cd website
npx wrangler@4 pages deploy . --project-name=video-downloader --branch=main
```

Windows 下也可以直接双击 `deploy.bat`。首次使用需先登录：

```bash
npx wrangler@4 login
```

- 生产地址：https://video-downloader-crq.pages.dev/
- Cloudflare 项目：`video-downloader`（Direct Upload，未接 Git 自动构建）
- 每次部署会生成一个 `<hash>.video-downloader-crq.pages.dev` 预览地址

## 内容维护

| 要改什么 | 改哪里 |
|---|---|
| 文案、功能、FAQ | `index.html` |
| 下载版本号与链接 | `index.html` 中 `#download` 区块 |
| 更新日志 | `index.html` 中 `#changelog` 时间线 |
| 截图 | 替换 `assets/shot-*.png`，保持文件名不变 |
| 配色、间距 | `assets/styles.css` 顶部 CSS 变量 |

截图取自桌面端 `_scratch_x_probe/_shots/light_*.png`，更新界面后重新复制同名文件即可。

## 绑定自定义域名

1. Cloudflare 控制台 → Workers & Pages → `video-downloader` → Custom domains
2. 添加域名，按提示在域名注册商处配置 CNAME（或把 NS 托管整体切到 Cloudflare）
3. 证书由 Cloudflare 自动签发，通常几分钟生效
