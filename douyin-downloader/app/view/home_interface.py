# -*- coding: utf-8 -*-
"""首页：单条链接解析与下载。

界面重做的结构（第二轮）：

    ┌ 页头：视频解析 / 支持的站点与能力 ────────────────────────┐
    │ 解析面板                                                  │
    │  ┌ 输入行：[ 粘贴链接…… ] [粘贴] [解析]  ← 页面唯一主操作 │
    │  ├ 平台能力行（12px 三级色，不抢主操作）                   │
    │  ├ ────────────── 结果区（未解析时是空态）────────────── │
    │  │ [封面] 标题 / 作者 / 元信息 / 统计                     │
    │  │ 清晰度 [下拉] [FPS] ...        [下载] [预览] [封面] ⋯  │
    │  └────────────────────────────────────────────────────── │
    └──────────────────────────────────────────────────────────┘

和上一版的差异：输入与结果**同属一张面板**（解析完输入不再占着上半屏，
结果紧接着展开），空态给出明确引导；平台清单从页头副标题降为输入框下的一行
能力提示。业务逻辑一行未改：解析、重试、下载、复制、封面都还是原来的函数。
"""
import os

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, ComboBox, FluentIcon as FIF,
                            IndeterminateProgressBar, InfoBar, InfoBarPosition,
                            LineEdit, PrimaryPushButton, PushButton, ScrollArea,
                            StrongBodyLabel, TransparentToolButton)

from ..core.applog import get_logger
from ..core.domain import needs_cookie_for_source
from ..core.models import (VideoInfo, dropdown_quality_indices, fmt_count,
                           fmt_size)
from ..core.site_ctx import SiteContext
from ..core.workers import CoverWorker, ParseWorker
from . import design as D
from . import workspace as W
from .preview_dialog import PreviewDialog

log = get_logger("home")

# 输入框下的一行能力提示（12px 三级色）。平台清单只在这里出现一次 ——
# 页头再说一遍、按钮再说一遍就是三重复读（设计原则 3）。
_PLATFORM_LINE = ("抖音 · B站 · 小红书 · X · Instagram · Iwara · Pornhub · hanime1 · 禁漫"
                  "　｜　视频 / 图集 / 漫画车号")


class HomeInterface(ScrollArea):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx          # AppContext: config / download manager
        self.setObjectName("homeInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.view = QWidget(self)
        self.setWidget(self.view)
        self.view.setObjectName("homeView")
        self.vBox = W.page_body(self.view, pad=28, spacing=14)

        self._current: VideoInfo = None
        self._workers = []
        self._copy_on_done = {}   # task_id -> 提示文案（下载完成后自动复制文件）
        # 下拉可见项 → quality_options 下标（下拉已按「分辨率前三高」筛过）
        self._quality_indices = []
        self._parse_generation = 0
        self._parse_input = ""
        self._empty_result_retries = 0
        self._parse_running = False

        self._build_header()
        self._build_work_panel()
        # 解析面板吃掉剩余高度（空态在面板内居中，结果区从顶部展开）
        self.vBox.addStretch(0)
        self._init_style()

        self.ctx.manager.task_finished.connect(self._on_task_finished)

    # ------------------------------------------------------------------ #
    def _init_style(self):
        self.view.setStyleSheet("QWidget#homeView { background: transparent; }")

    def _build_header(self):
        self.vBox.addWidget(W.PageHeading(
            "视频解析",
            "粘一条分享链接，选清晰度，下载。解析结果会展开在下方，不用切页。",
            self.view))

    def _build_work_panel(self):
        """解析面板：输入、能力提示、结果分区（结果未就绪时显示空态）"""
        # 面板按内容高度：结果卡不会被拉成一张通栏大框，下方留白交给画布光晕。
        # 必须把垂直策略设成 Maximum —— 只把 stretch 写成 0 没用，
        # QVBoxLayout 仍会按 Preferred 策略把多余高度塞给唯一可扩展的控件。
        self.panel = W.Panel(self.view, padding=(20, 16, 20, 16), spacing=0)
        self.panel.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Maximum)
        self.vBox.addWidget(self.panel, 0)

        # ---------------- 输入行 ----------------
        row = QHBoxLayout()
        row.setSpacing(D.GAP_SM)
        self.pasteBtn = PushButton(FIF.PASTE, "粘贴", self.panel)
        self.pasteBtn.setToolTip("从剪贴板粘贴分享链接（Ctrl+V 也可以直接粘进输入框）")
        self.pasteBtn.clicked.connect(self._on_paste)
        self.parseBtn = PrimaryPushButton(FIF.SEARCH, "解析", self.panel)
        self.parseBtn.setFixedHeight(34)
        self.parseBtn.setMinimumWidth(96)
        self.parseBtn.clicked.connect(self._on_parse)
        self.parseBtn.setToolTip("解析链接（输入框里回车同样可以）")

        self.linkEdit = LineEdit(self.panel)
        # 平台清单已经在下方说过了，这里只讲"粘什么、然后怎么走"
        self.linkEdit.setPlaceholderText(
            "粘贴分享链接或分享文案，回车解析　·　也支持禁漫车号 JM123456")
        self.linkEdit.setClearButtonEnabled(True)
        self.linkEdit.setFixedHeight(34)
        self.linkEdit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        self.linkEdit.returnPressed.connect(self._on_parse)
        self.pasteBtn.setFixedHeight(34)
        row.addWidget(self.linkEdit, 1)
        row.addWidget(self.pasteBtn)
        row.addWidget(self.parseBtn)
        self.panel.body.addLayout(row)

        hint = W.text_label(_PLATFORM_LINE, self.panel, D.TEXT_TERTIARY,
                            D.FONT_CAPTION, wrap=True)
        hint.setContentsMargins(0, 8, 0, 0)
        self.panel.body.addWidget(hint)

        self.progress = IndeterminateProgressBar(self.panel)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        self.panel.body.addSpacing(8)
        self.panel.body.addWidget(self.progress)

        # ---------------- 结果分区 ----------------
        self.panel.body.addSpacing(12)
        self.panel.body.addWidget(W.Divider(self.panel))
        self.panel.body.addSpacing(12)

        self.resultCard = QWidget(self.panel)
        result = QVBoxLayout(self.resultCard)
        result.setContentsMargins(0, 0, 0, 0)
        result.setSpacing(D.GAP_MD)
        self._build_result_body(result)
        # 空态：在可伸缩容器里垂直居中，页面下半部分留给画布氛围层
        self.emptyWrap = QWidget(self.panel)
        emptyLay = QVBoxLayout(self.emptyWrap)
        emptyLay.setContentsMargins(0, 0, 0, 0)
        emptyLay.setSpacing(0)
        emptyLay.addStretch(1)
        self.emptyState = W.EmptyState(
            FIF.SEARCH, "粘贴链接开始解析",
            "解析成功后这里会展示作品信息与清晰度选项；\n"
            "图集与禁漫车号（JM123456）同样支持。",
            self.emptyWrap)
        emptyLay.addWidget(self.emptyState)
        emptyLay.addStretch(2)
        # 空态需要一块明确的"舞台"高度，否则收起时面板会塌成一条
        self.emptyWrap.setMinimumHeight(300)

        # 结果与空态共用一个容器：同一块区域在"还没解析"和"解析好了"之间切换，
        # 高度不会被内容撑来撑去，切页/解析时布局不跳动。
        self.resultStack = QStackedWidget(self.panel)
        self.resultStack.addWidget(self.emptyWrap)     # 0 = 空态
        self.resultStack.addWidget(self.resultCard)    # 1 = 结果
        self.panel.body.addWidget(self.resultStack, 1)
        self.resultStack.setCurrentIndex(0)

    def _build_result_body(self, result: QVBoxLayout):
        """结果区：封面 + 信息列 + 操作条。

        信息分四层（标题 / 作者 + 徽章 / 元信息 / 统计），层级靠字号与
        字重拉开，颜色只用两级 —— 上一版四行同色号，扫不出重点。
        """
        body = QHBoxLayout()
        body.setSpacing(D.GAP_LG)

        # 封面槽按 3:4 取，132×176：既能看出内容，又不至于把右侧文字挤窄
        self.coverLabel = D.Placeholder("封面", size=(132, 176),
                                        parent=self.resultCard)
        body.addWidget(self.coverLabel, 0, Qt.AlignTop)

        infoCol = QVBoxLayout()
        infoCol.setSpacing(D.GAP_XS)
        # L1 作品标题 —— 整页视觉权重最高的一行
        self.titleLabel = StrongBodyLabel("-", self.resultCard)
        self.titleLabel.setFont(D.font(D.FONT_TITLE, D.W_SEMI))
        self.titleLabel.setTextColor(*D.TEXT_PRIMARY)
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # L2 作者 + 类型/时长徽章（徽章用小面积底色，不跟标题抢）
        badgeRow = QHBoxLayout()
        badgeRow.setSpacing(D.GAP_SM)
        # L2 作者 —— 小字 + 强调色：次要但要看得出"是谁的作品"
        self.authorLabel = BodyLabel("-", self.resultCard)
        self.authorLabel.setFont(D.font(D.FONT_CAPTION, D.W_MEDIUM))
        self.authorLabel.setTextColor(*D.TEXT_ACCENT)
        badgeRow.addWidget(self.authorLabel)
        self.kindChip = W.Chip("", D.TEXT_SECONDARY, self.resultCard)
        badgeRow.addWidget(self.kindChip)
        self.durationChip = W.Chip("", D.TEXT_SECONDARY, self.resultCard)
        badgeRow.addWidget(self.durationChip)
        badgeRow.addStretch(1)

        # L3 元信息（画质 / BGM / 章节页数）
        self.metaLabel = W.text_label("-", self.resultCard, D.TEXT_TERTIARY,
                                      D.FONT_CAPTION, wrap=True)
        # L4 统计：数字是主体，标签退成背景
        self.statsLabel = D.StatStrip(self.resultCard)

        infoCol.addWidget(self.titleLabel)
        infoCol.addSpacing(D.GAP_XS)
        infoCol.addLayout(badgeRow)
        infoCol.addWidget(self.metaLabel)
        infoCol.addSpacing(D.GAP_SM)
        infoCol.addWidget(self.statsLabel)
        infoCol.addStretch(1)
        body.addLayout(infoCol, 1)
        result.addLayout(body)

        result.addWidget(W.Divider(self.resultCard))

        # 清晰度 + 动作条：左侧「选什么」，右侧「拿到文件」
        # 主操作只留一个（下载），其余降为图标 + tooltip，减少一排等权文字按钮
        actions = QHBoxLayout()
        actions.setSpacing(D.GAP_SM)
        self.qualityLabel = W.text_label("清晰度", self.resultCard, D.TEXT_SECONDARY,
                                         D.FONT_CAPTION)
        self.qualityCombo = ComboBox(self.resultCard)
        self.qualityCombo.setMinimumWidth(220)
        self.qualityCombo.setToolTip("选择要下载的清晰度，默认最高")
        self.qualityCombo.currentIndexChanged.connect(self._update_fps_badge)
        # 帧率小标签：跟着下拉实时变（60fps 绿、30fps 灰、更低橙）
        self.fpsBadge = W.Chip("", D.FPS_MID, self.resultCard)
        self.fpsBadge.setVisible(False)
        self.fpsBadge.setToolTip(
            "当前档位帧率：60fps 更顺滑（码率通常更低），30fps 码率更高")
        actions.addWidget(self.qualityLabel)
        actions.addWidget(self.qualityCombo)
        actions.addWidget(self.fpsBadge)
        actions.addStretch(1)

        self.copyLinkBtn = TransparentToolButton(FIF.LINK, self.resultCard)
        self.copyLinkBtn.setToolTip("复制无水印链接")
        self.copyLinkBtn.clicked.connect(self._on_copy_link)
        self.copyTitleBtn = TransparentToolButton(FIF.COPY, self.resultCard)
        self.copyTitleBtn.setToolTip("复制标题")
        self.copyTitleBtn.clicked.connect(
            lambda: self._copy(self.titleLabel.text(), "已复制标题"))
        self.coverBtn = PushButton(FIF.PHOTO, "封面", self.resultCard)
        self.coverBtn.setToolTip("只下载封面图片")
        self.coverBtn.clicked.connect(self._on_download_cover)
        self.previewBtn = PushButton(FIF.VIEW, "预览", self.resultCard)
        self.previewBtn.setToolTip("下载前在窗口里预览画面")
        self.previewBtn.clicked.connect(self._on_preview)
        self.downloadBtn = PrimaryPushButton(FIF.DOWNLOAD, "下载视频",
                                             self.resultCard)
        self.downloadBtn.setFixedHeight(34)
        self.downloadBtn.clicked.connect(self._on_download)

        actions.addWidget(self.copyLinkBtn)
        actions.addWidget(self.copyTitleBtn)
        actions.addSpacing(D.GAP_XS)
        actions.addWidget(self.coverBtn)
        actions.addWidget(self.previewBtn)
        actions.addWidget(self.downloadBtn)
        result.addLayout(actions)
        # 内容贴顶：底部留白属于画布（有光晕），不要用空白把操作条推到最下
        result.addStretch(1)

    # ------------------------------------------------------------------ #
    def _on_paste(self):
        text = QGuiApplication.clipboard().text()
        if text:
            self.linkEdit.setText(text)
        else:
            self._toast("剪贴板为空", "请先复制抖音分享文案", "warning")

    def _on_parse(self):
        if self._parse_running:
            return
        text = self.linkEdit.text().strip()
        if not text:
            self._toast("请输入链接", "粘贴分享文案后再开始解析", "warning")
            return
        self._parse_generation += 1
        self._empty_result_retries = 0
        self._start_parse(text)

    def _start_parse(self, text: str):
        self._parse_input = text
        self._parse_running = True
        self.parseBtn.setEnabled(False)
        self.resultStack.setCurrentIndex(0)
        self.emptyState.set_text("正在解析…", "根据站点不同，通常 1~5 秒；"
                                             "需要登录的内容会稍慢。")
        self.emptyState.set_body_visible(True)
        self._current = None
        self.progress.show()
        # 站点凭据统一由 SiteContext 从 Config 组装（字段集中在一处维护）
        w = ParseWorker(SiteContext.from_config(self.ctx.config), text, self)
        w.succeeded.connect(self._on_parse_ok)
        w.failed.connect(self._on_parse_fail)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _retry_empty_result(self, generation: int, text: str):
        if generation != self._parse_generation:
            return
        if self.linkEdit.text().strip() != text:
            self._parse_done()
            return
        self._start_parse(text)

    def _on_parse_ok(self, info: VideoInfo):
        self._empty_result_retries = 0
        self._current = info
        live_count = sum(1 for g in info.image_live_url_groups if g)
        self.coverLabel.reset()
        self.titleLabel.setText(info.title or "（无标题）")
        self.authorLabel.setText(f"@{info.author}" if info.author else "未知作者")
        self.kindChip.setText(info.type_text)
        self.kindChip.set_color(D.TEXT_ACCENT)
        if not info.is_image:
            self.durationChip.setText(f"时长 {info.duration_text}")
            self.durationChip.setVisible(True)
        elif info.source == "jmcomic":
            pages = (info.extra or {}).get("pages")
            self.durationChip.setText(f"{pages} 页" if pages else "整本")
            self.durationChip.setVisible(True)
        else:
            chip = f"{len(info.image_urls)} 张"
            if live_count:
                chip += f" · {live_count} 个动图"
            self.durationChip.setText(chip)
            self.durationChip.setVisible(True)

        # 元信息行：类型已由徽章表达，这里只留"选了会得到什么"（画质 / 张数 / 章节 / BGM）
        parts = []
        if not info.is_image:
            if info.quality:
                parts.append(f"画质 {info.quality}")
        elif info.source == "jmcomic":
            extra = info.extra or {}
            chapters = extra.get("chapters")
            pages = extra.get("pages")
            if chapters:
                parts.append(f"{chapters} 章")
            if pages:
                parts.append(f"{pages} 页")
        else:
            parts.append(f"{len(info.image_urls)} 张图片")
            if live_count:
                parts.append(f"{live_count} 个动图转 GIF")
        if info.music_title:
            parts.append(f"BGM {info.music_title}")
        self.metaLabel.setText(" · ".join(parts))
        # 统计：显示折成「万」，精确值放 tooltip —— 量级要一眼可读，精度不能丢
        self.statsLabel.set_stats([
            ("点赞", fmt_count(info.digg_count)),
            ("评论", fmt_count(info.comment_count)),
            ("分享", fmt_count(info.share_count)),
        ])
        self.statsLabel.setToolTip(
            f"点赞 {info.digg_count}　评论 {info.comment_count}　分享 {info.share_count}")
        if info.source == "jmcomic":
            self.downloadBtn.setText("下载本子")
            self.downloadBtn.setIcon(FIF.PHOTO)
        else:
            if info.is_image and live_count:
                self.downloadBtn.setText("下载图集（动图转 GIF）")
            else:
                self.downloadBtn.setText("下载全部图片" if info.is_image else "下载视频")
            self.downloadBtn.setIcon(FIF.PHOTO if info.is_image else FIF.DOWNLOAD)
        self.copyLinkBtn.setVisible(not info.is_image)
        self.coverBtn.setVisible(bool(info.cover_url) and not info.is_image)

        # 清晰度下拉：有多个可选档位时展示，默认选最高
        self.qualityCombo.clear()
        options = info.quality_options
        # 有 60fps 就不摆 30fps；剩下的分辨率只留前三高（同分辨率多条码流算一档）
        self._quality_indices = dropdown_quality_indices(options)
        if info.is_image or not options:
            self.qualityLabel.setVisible(False)
            self.qualityCombo.setVisible(False)
        else:
            for i in self._quality_indices:
                # 标签带文件大小（拿不到大小则不追加）
                self.qualityCombo.addItem(info.quality_label(i))
            self.qualityCombo.setCurrentIndex(0)
            self.qualityLabel.setVisible(True)
            self.qualityCombo.setVisible(True)
            # 下拉里文字可能变长，按最长项自适应宽度
            self.qualityCombo.setMinimumWidth(
                max(220, self.qualityCombo.fontMetrics().horizontalAdvance(
                    max((self.qualityCombo.itemText(i)
                         for i in range(self.qualityCombo.count())),
                        default="")) + 56))
            if self.qualityCombo.count() == 1:
                self.qualityCombo.setEnabled(False)
                self.qualityCombo.setToolTip("该来源仅提供这一档画质")
            else:
                self.qualityCombo.setEnabled(True)
                self.qualityCombo.setToolTip("选择要下载的清晰度，默认最高")
        self._update_fps_badge()

        # 结果区淡入：解析是一次"有等待"的操作，硬 show() 会让内容突然砸出来
        self.resultStack.setCurrentIndex(1)
        D.fade_in(self.resultCard, D.DUR_ENTER)

        if info.cover_url:
            w = CoverWorker(info.cover_url, self)
            w.loaded.connect(self._set_cover)
            w.finished.connect(lambda: self._cleanup_worker(w))
            self._workers.append(w)
            w.start()
        self._parse_done()

    def _on_parse_fail(self, msg: str):
        self._parse_running = False
        transient = ("作品信息获取失败" in msg
                     and "douyin.com/" in self._parse_input
                     and self.linkEdit.text().strip() == self._parse_input)
        if transient and self._empty_result_retries == 0:
            # Douyin sometimes returns a 200 response with an empty note page.
            # A fresh worker/session can succeed; retry once without blocking UI.
            self._empty_result_retries = 1
            generation, text = self._parse_generation, self._parse_input
            self.progress.show()
            QTimer.singleShot(1800, lambda: self._retry_empty_result(generation, text))
            return
        self._parse_done()
        self._show_empty_result(msg)
        if transient:
            self._toast("抖音暂未返回作品数据",
                        "已自动重试一次；请稍后再试，或在浏览器确认作品仍可访问。",
                        "warning")
        else:
            self._toast("解析失败", msg, "error")

    def _show_empty_result(self, reason: str):
        """解析失败：回到空态，并把失败原因留在页面上（不只是一闪而过的提示）"""
        self.emptyState.set_text("没有解析出内容",
                                 (reason or "链接无法识别")[:160] +
                                 "\n可以再点一次「粘贴」，或换一条分享链接重试。")
        self.emptyState.set_body_visible(True)
        self.resultStack.setCurrentIndex(0)

    def _parse_done(self):
        self._parse_running = False
        self.parseBtn.setEnabled(True)
        self.progress.hide()

    def _set_cover(self, data: bytes):
        pix = QPixmap()
        if pix.loadFromData(data):
            self.coverLabel.set_image(pix)

    def _update_fps_badge(self, *_):
        """把当前选中档位的帧率做成徽章（≥50fps 绿、30fps 浅、更低橙）"""
        info = self._current
        fps = 0
        # 用 isHidden() 而不是 isVisible()：后者在窗口还没 show() 时恒为 False，
        # 会把帧率徽章误判成"不该显示"
        if (info and not info.is_image and not self.qualityCombo.isHidden()
                and self.qualityCombo.count()):
            fps = info.fps_at(self._selected_quality_index())
        if not fps:
            self.fpsBadge.setVisible(False)
            return
        if fps >= 50:
            color = D.FPS_HIGH
        elif fps >= 30:
            color = D.FPS_MID
        else:
            color = D.FPS_LOW
        self.fpsBadge.set_color(color)
        self.fpsBadge.setText(f"{fps} FPS")
        self.fpsBadge.setVisible(True)
        # 换档时淡入一次，让"帧率变了"这件事被看见（原先是硬切）
        D.fade_in(self.fpsBadge, D.DUR_INSTANT)

    def _selected_quality_index(self) -> int:
        """下拉当前项对应的 quality_options 下标

        下拉里只摆了「分辨率前三高」的档位，位置和 quality_options 的下标不是
        一回事，下载 / 复制链接 / 帧率徽章都得先换算回来。
        """
        pos = self.qualityCombo.currentIndex()
        if 0 <= pos < len(self._quality_indices):
            return self._quality_indices[pos]
        return 0

    # ------------------------------------------------------------------ #
    def _on_preview(self):
        info = self._current
        if not info:
            return
        options = info.quality_options
        idx = self._selected_quality_index() if options and not self.qualityCombo.isHidden() else 0
        if options and 0 <= idx < len(options):
            label, url = options[idx]
            label = info.quality_label(idx)
        else:
            label, url = info.quality or "视频", info.play_url
        context = SiteContext.from_config(self.ctx.config)
        # 禁漫预览要靠 Cookie 换原图，但它不在 needs_cookie_for_source 里
        # （CDN 直链本身不需要 Cookie），所以这里显式带上
        cookie = ""
        if needs_cookie_for_source(info.source) or info.source == "jmcomic":
            cookie = context.cookie_for(info.source)
        cover = self.coverLabel.pixmap() if info.source == "jmcomic" else None
        dialog = PreviewDialog(
            info, url=url, label=label, cookies=cookie, cover=cover,
            parent=self)
        if dialog.exec():
            self._on_download()

    def _on_download(self):
        info = self._current
        if not info:
            return
        log.info("用户点击下载：%s「%s」| 图集=%s",
                 info.source, (info.title or "")[:60], bool(info.is_image))
        cfg = self.ctx.config
        # 单个下载直接存到设置的下载目录，不按作者分文件夹
        base = cfg.get("download_path")
        # 一次下载只生成一次文件名：多次调用 build_filename 会因跨秒得到不同
        # 时间戳，导致视频与其封面失去同名前缀（删除时找不到关联封面）
        fname = cfg.build_filename(info)
        if info.source == "jmcomic":
            folder = os.path.join(base, fname)
            self.ctx.manager.add(
                info.play_url, folder, info.safe_title(60),
                cookies=SiteContext.from_config(cfg).cookie_for("jmcomic"))
            pages = (info.extra or {}).get("pages") or "?"
            self._toast("已加入队列", f"禁漫本子共 {pages} 页，开始下载", "success")
            return
        # 只有 IG / B站 的 CDN 需要登录态 Cookie。图集也要带：IG 多图的图挂在
        # cdninstagram.com 上，漏传会 403（此前只有视频分支带，图集需要登录时必挂）
        task_cookies = (SiteContext.from_config(cfg).cookie_for(info.source)
                        if needs_cookie_for_source(info.source) else "")
        if info.is_image:
            folder = os.path.join(base, fname)
            live_groups = info.image_live_url_groups
            live_count = 0
            for i, url in enumerate(info.image_urls, 1):
                group = live_groups[i - 1] if i - 1 < len(live_groups) else []
                if group:
                    # 动图：先下载视频轨（临时 .mp4），完成瞬间转成同名 GIF
                    live_count += 1
                    mp4_path = os.path.join(folder, f"{i:02d}_live.mp4")
                    gif_path = os.path.join(folder, f"{i:02d}.gif")
                    self.ctx.manager.add(
                        group[0], mp4_path,
                        f"{info.safe_title(30)}_{i:02d}.gif",
                        fallbacks=group[1:], cookies=task_cookies,
                        convert_to_gif=True, gif_path=gif_path)
                else:
                    ext = ".webp" if "webp" in url else ".jpeg"
                    path = os.path.join(folder, f"{i:02d}{ext}")
                    self.ctx.manager.add(url, path,
                                         f"{info.safe_title(30)}_{i:02d}{ext}",
                                         cookies=task_cookies)
            msg = f"图集共 {len(info.image_urls)} 张图片"
            if live_count:
                msg += f"，其中 {live_count} 个动图将转为 GIF"
            self._toast("已加入队列", msg + "，开始下载", "success")
        else:
            # 取用户选择的清晰度（默认最高档）
            options = info.quality_options
            idx = (self._selected_quality_index()
                   if self.qualityCombo.isVisible() else 0)
            if options and 0 <= idx < len(options):
                url = options[idx][1]
                others = [u for i, (_, u) in enumerate(options) if i != idx]
            else:
                url = info.play_url
                others = info.play_url_candidates[1:]
            # 记住用户选的这一档标签（含帧率），任务页要展示
            chosen = (options[idx][0] if options and 0 <= idx < len(options)
                      else "最高画质")
            path = os.path.join(base, fname + ".mp4")
            task = self.ctx.manager.add(
                url, path, fname + ".mp4",
                fallbacks=others, cookies=task_cookies,
                audio_url=info.audio_map.get(url, ""),
                quality=chosen,
                # 同档备用 CDN / 音频备用地址一起交给下载器，避免主地址挂了卡住
                audio_map=info.audio_map,
                url_backups=info.url_backups,
                audio_backups=info.audio_backups,
                # 封面地址带给本地视频库：入库时即使没开"同时下载封面"也能拿到封面
                cover_url=info.cover_url)
            if cfg.get("auto_copy_file"):
                self._copy_on_done[task.task_id] = path
            size = info.size_of(url)
            picked = f"{chosen} · {fmt_size(size)}" if size else chosen
            self._toast("已加入队列", f"{picked} · 开始下载，可在「下载任务」查看进度", "success")
        # 附带封面：封面另存为 _cover，免得和视频文件同名互相覆盖。
        # 注意不要把它也登记进 _copy_on_done —— 剪贴板里该留的是用户要的视频，
        # 封面晚一点下完就会把视频顶掉。
        if cfg.get("download_cover") and info.cover_url and not info.is_image:
            cpath = os.path.join(base, fname + "_cover.jpg")
            self.ctx.manager.add(info.cover_url, cpath, "封面")

    def _on_download_cover(self):
        info = self._current
        if not info or not info.cover_url:
            return
        cfg = self.ctx.config
        path = os.path.join(cfg.get("download_path"),
                            cfg.build_filename(info) + "_cover.jpg")
        task = self.ctx.manager.add(info.cover_url, path, "封面")
        if cfg.get("auto_copy_file"):
            self._copy_on_done[task.task_id] = path
        self._toast("已加入队列", "封面开始下载", "success")

    def _on_copy_link(self):
        info = self._current
        if not info:
            return
        url = info.play_url
        options = info.quality_options
        idx = (self._selected_quality_index()
               if self.qualityCombo.isVisible() else 0)
        if options and 0 <= idx < len(options):
            url = options[idx][1]
        if url:
            label = options[idx][0] if options and 0 <= idx < len(options) else ""
            self._copy(url, f"已复制链接（{label}）" if label else "已复制无水印链接")

    def _copy(self, text: str, msg: str):
        QGuiApplication.clipboard().setText(text)
        self._toast(msg, "", "success")

    def _on_task_finished(self, info):
        # 无论成功与否都要摘掉登记项：失败 / 取消的任务不会再走「已完成」分支，
        # 不 pop 就会永久留在 dict 里（长期运行缓慢泄漏）。
        path = self._copy_on_done.pop(info.task_id, None)
        if path and info.status == "已完成":
            try:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(path)])
                mime.setText(path)
                QGuiApplication.clipboard().setMimeData(mime)
                self._toast("已复制到剪贴板",
                            "文件已复制，可直接 Ctrl+V 或右键粘贴", "success")
            except Exception:  # noqa: BLE001
                pass

    def _toast(self, title: str, content: str, kind: str):
        fn = {"success": InfoBar.success, "error": InfoBar.error,
              "warning": InfoBar.warning}.get(kind, InfoBar.info)
        fn(title, content, orient=Qt.Horizontal, isClosable=True,
           position=InfoBarPosition.TOP, duration=3500, parent=self)

    def _cleanup_worker(self, w):
        if w in self._workers:
            self._workers.remove(w)
        w.deleteLater()
