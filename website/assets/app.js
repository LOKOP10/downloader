/* 视频下载器 · 官网交互（无依赖） */
(function () {
  "use strict";

  /* 导航：滚动后加分隔线 */
  var nav = document.getElementById("nav");
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* 移动端菜单 */
  var toggle = document.getElementById("navToggle");
  var links = document.getElementById("navLinks");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.addEventListener("click", function (e) {
      if (e.target.closest("a")) {
        links.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* 界面截图切换 */
  var shotsRoot = document.querySelector("[data-shots]");
  if (shotsRoot) {
    var img = document.getElementById("shotImg");
    var caption = document.getElementById("shotCaption");
    var tabs = shotsRoot.querySelectorAll(".tab");
    var meta = {
      home: "粘贴一条链接，选清晰度，下载。解析结果展会在下方，不用切页。",
      batch: "贴一个作者主页，整页作品一次抓取，按作者自动建目录。",
      task: "队列进度与失败重试都在这里；底部日志记录解析与下载的关键节点。",
      manga: "文件夹即一本书，按自然排序翻页；支持搜索、收藏与阅读进度。",
      converter: "多选或拖入 WebP，批量转换或按顺序合并成一个 MP4。",
      setting: "Cookie、下载目录、命名规则、主题与强调色都在设置页。"
    };
    var cache = {};

    // 预加载，避免切换时闪白
    Object.keys(meta).forEach(function (key) {
      var pre = new Image();
      pre.src = "assets/shot-" + key + ".png";
      cache[key] = pre;
    });

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var key = tab.getAttribute("data-shot");
        if (!key || tab.classList.contains("is-active")) return;
        tabs.forEach(function (t) {
          t.classList.remove("is-active");
          t.setAttribute("aria-selected", "false");
        });
        tab.classList.add("is-active");
        tab.setAttribute("aria-selected", "true");

        var frame = img.closest(".shot-frame");
        frame.classList.add("is-swapping");
        window.setTimeout(function () {
          img.src = "assets/shot-" + key + ".png";
          img.alt = "视频下载器界面预览：" + tab.textContent.trim();
          if (caption && meta[key]) caption.textContent = meta[key];
          frame.classList.remove("is-swapping");
        }, 140);
      });
    });
  }

  /* 年份 */
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
