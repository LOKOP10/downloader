@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo == 部署视频下载器官网到 Cloudflare Pages ==
echo.

call npx --yes wrangler@4 pages deploy . --project-name=video-downloader --branch=main --commit-dirty=true

if errorlevel 1 (
  echo.
  echo 部署失败。若提示未登录，先执行： npx wrangler@4 login
) else (
  echo.
  echo 部署完成：https://video-downloader-crq.pages.dev/
)

echo.
pause
