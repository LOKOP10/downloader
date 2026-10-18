# -*- coding: utf-8 -*-
"""主窗口：工作台壳层（侧栏导航 + 标题栏 + 内容画布 + 状态条）

界面重做的组织方式（第二轮）：

    标题栏 32px（窗口拖动 / 最小化 / 最大化 / 关闭）
    ┌──────────┬────────────────────────────────────┐
    │ 侧栏 208 │ 内容画布（各页面自己放 Panel）      │
    │ 导航 6 项 │                                    │
    └──────────┴────────────────────────────────────┘
    状态条 26px（后台任务 / 下载目录 / 版本）

为什么从「顶部 6 个标签」改成「左侧竖排导航」：标签条横着吃宽度，
1180px 窗口下 6 个标签加右侧工具已经把整条占满，长名字（WebP 转 MP4）
还会被压；竖排里每个名字都完整，内容区宽度也不再随标签文案变化。
"""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QHBoxLayout,
                               QVBoxLayout)
from qfluentwidgets import FluentIcon as FIF, FluentWindow, Theme, setTheme

from . import __version__
from .core.config import Config
from .core.downloader import DownloadManager
from .view import design as D
from .view import workspace as W
from .view.batch_interface import BatchInterface
from .view.converter_interface import ConverterInterface
from .view.home_interface import HomeInterface
from .view.manga_interface import MangaInterface
from .view.video_interface import VideoInterface
from .view.perf import tune_scroll_areas
from .view.setting_interface import SettingInterface
from .view.task_interface import TaskInterface
from .view.title_bar import AppTitleBar

# 侧栏导航项：(图标, 文字, tooltip)
_NAV_ITEMS = [
    (FIF.SEARCH, "视频解析", "粘贴分享链接，解析后选择清晰度下载"),
    (FIF.PEOPLE, "批量下载", "抓取作者主页的全部作品，勾选批量下载"),
    (FIF.DOWNLOAD, "下载任务", "队列进度、失败重试与运行日志"),
    (FIF.PHOTO, "漫画阅读器", "本地 JPG 文件夹书架与阅读进度"),
    (FIF.MOVIE, "本地视频", "下载完成的视频自动入库，记住播放位置接着看"),
    (FIF.VIDEO, "WebP 转 MP4", "本地 WebP 动图批量转 MP4 / 顺序合并"),
    (FIF.SETTING, "设置", "下载行为、平台登录态与界面外观"),
]


def _safe_int(value, default: int, low: int = 1, high: int = 64) -> int:
    """把配置值转成 [low, high] 区间的 int；坏值回退默认值，绝不抛异常"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def resource_path(*parts) -> str:
    """兼容源码运行与 PyInstaller 单文件模式，定位随包资源。"""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon() -> QIcon:
    """优先用新版图标，缺失时回退到旧版或内置图标。"""
    for name in ("assets/app-v2.ico", "assets/app-v2.png",
                 "assets/app.ico", "assets/app.png"):
        p = resource_path(name.replace("/", os.sep))
        if os.path.exists(p):
            ic = QIcon(p)
            if not ic.isNull():
                return ic
    return FIF.VIDEO.icon()


class AppContext:
    """跨页面共享的运行时上下文"""
    def __init__(self, config: Config, manager: DownloadManager):
        self.config = config
        self.manager = manager


class MainWindow(FluentWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._mica_enabled = False
        # 配置项已在 Config.load() 里收敛成合法 int；这里再兜一层，
        # 避免手工改坏 config.json 时 int() 抛 ValueError 让程序起不来。
        # （_safe_int 随 9ce9b0f 一并合入，见本文件顶部）
        self.ctx = AppContext(config, DownloadManager(
            _safe_int(config.get("max_concurrent", 3), 3), self,
            segment_enabled=bool(config.get("segment_download", True)),
            segment_connections=_safe_int(config.get("segment_connections", 8), 8)))

        self.homeInterface = HomeInterface(self.ctx, self)
        self.batchInterface = BatchInterface(self.ctx, self)
        self.taskInterface = TaskInterface(self.ctx, self)
        self.mangaInterface = MangaInterface(self.ctx, self)
        self.videoInterface = VideoInterface(self.ctx, self)
        self.converterInterface = ConverterInterface(self.ctx, self)
        self.settingInterface = SettingInterface(self.ctx, self)
        self.taskInterface.read_manga.connect(self._read_manga)

        self.initNavigation()
        self.initWindow()
        self._apply_theme()
        self._wire_status_bar()

        # 启动即展示首页
        self.switchTo(self.homeInterface)

    # ------------------------------------------------------------------ #
    def initNavigation(self):
        # 先把各页面注册进 stackedWidget（addSubInterface 会顺带挂进左侧
        # NavigationInterface）。旧的侧边栏对象保留但不销毁，避免 qrouter
        # 与 stackedWidget 的既有链路断掉。
        self.addSubInterface(self.homeInterface, FIF.SEARCH, "视频解析")
        self.addSubInterface(self.batchInterface, FIF.PEOPLE, "批量下载")
        self.addSubInterface(self.taskInterface, FIF.DOWNLOAD, "下载任务")
        self.addSubInterface(self.mangaInterface, FIF.PHOTO, "漫画阅读器")
        self.addSubInterface(self.videoInterface, FIF.MOVIE, "本地视频")
        self.addSubInterface(self.converterInterface, FIF.VIDEO, "WebP 转 MP4")
        self.addSubInterface(self.settingInterface, FIF.SETTING, "设置")

        self.navigationInterface.setAcrylicEnabled(False)
        self.navigationInterface.hide()
        self.hBoxLayout.removeWidget(self.navigationInterface)

        self._pages = [self.homeInterface, self.batchInterface,
                       self.taskInterface, self.mangaInterface,
                       self.videoInterface, self.converterInterface,
                       self.settingInterface]

        # 左侧导航（TabBar 这个名字保留：外部与测试用它判断导航项数量 /
        # 主题按钮位置，语义仍是「导航栏」，只是方向从横改竖）
        self.tabBar = W.NavRail(_NAV_ITEMS, app_name="视频下载器",
                                version=f"v{__version__}", parent=self)
        self.tabBar.setObjectName("appTabBar")
        self.tabBar.themeButton.clicked.connect(self._toggle_theme)

        self.statusBar = W.StatusBar(self)

        # widgetLayout 原本只放 stackedWidget。改成：
        #   [侧栏] + [内容(stackedWidget) / 状态条]
        self.widgetLayout.removeWidget(self.stackedWidget)
        self.hBoxLayout.insertWidget(0, self.tabBar)
        self.hBoxLayout.setStretchFactor(self.tabBar, 0)
        self.hBoxLayout.setStretchFactor(self.widgetLayout, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stackedWidget, 1)
        right.addWidget(self.statusBar)
        self.widgetLayout.addLayout(right)
        self.widgetLayout.setStretchFactor(right, 1)

        self.tabBar.currentChanged.connect(self._on_tab_changed)
        # 页面若被其它途径切走（设置页跳转等），导航高亮要跟着走
        self.stackedWidget.currentChanged.connect(self._sync_tab)

    # ------------------------------------------------------------------ #
    def _read_manga(self, folder):
        self.switchTo(self.mangaInterface)
        self.mangaInterface.open_folder(folder)

    def closeEvent(self, event):
        # 异步等待转换取消清理，防止解释器退出时遗留 FFmpeg；不阻塞 GUI 线程。
        if self.converterInterface._busy:
            self.converterInterface.shutdown()
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        super().closeEvent(event)
        if event.isAccepted():
            self.mangaInterface.shutdown()
            self.videoInterface.shutdown()
            self.converterInterface.shutdown()

    def _on_tab_changed(self, index: int):
        if 0 <= index < len(self._pages):
            self.switchTo(self._pages[index])

    def _sync_tab(self, index: int):
        if 0 <= index < len(self._pages):
            self.tabBar.set_current(index)

    def _toggle_theme(self):
        """侧栏底部的明暗切换（太阳 / 月亮图标按钮）"""
        cur = self.config.get("theme", "dark")
        self.config.set("theme", "light" if cur != "light" else "dark")
        self._apply_theme()

    def switchTo(self, interface):
        """页面切换：换掉 qfluentwidgets 默认的 InQuad/OutQuad 曲线。

        入场动画应该"先快后慢"（减速），原来的曲线在起步阶段偏软，切页会有一点
        拖泥带水的感觉。这里统一用 ease-out，并复用设计系统的时长与曲线。
        """
        if isinstance(interface, QAbstractScrollArea):
            interface.verticalScrollBar().setValue(0)
        self.stackedWidget.view.setCurrentWidget(
            interface, False, True, D.DUR_ENTER, D.EASE_ENTER)

    def resizeEvent(self, event):
        """标题栏顶到最左、横跨整窗。

        FluentWindow.resizeEvent 会固定把标题栏右移 46px，那是给左侧导航的
        "返回"键留位；我们的导航是自绘的侧栏，那个位置不再需要。
        """
        super().resizeEvent(event)
        self._canvas_pix = None
        if self.titleBar is not None:
            self.titleBar.move(0, 0)
            self.titleBar.resize(self.width(), self.titleBar.height())

    def initWindow(self):
        # 1280×720：宽给到任务表（7 列）与设置行的控件边界，高不超过 720
        # （小屏笔记本 1366×768 下仍能完整显示，含任务栏）。
        self.resize(1280, 720)
        self.setMinimumSize(1024, 620)
        self.setWindowTitle("视频下载器")
        self.setWindowIcon(app_icon())
        # 换掉 qfluentwidgets 自带的 48px 标题栏，改用 32px 的紧凑版本
        self.setTitleBar(AppTitleBar(self))
        # 页面区顶部要让出标题栏高度（FluentWindow 默认按 48 预留，得跟着改）
        self.widgetLayout.setContentsMargins(0, D.TITLEBAR_H, 0, 0)
        # 窗口底色走设计系统的 BG_PRIMARY（浅 #FAFAFA / 深 #09090B）。
        try:
            self.setCustomBackgroundColor(*D.BG_PRIMARY)
        except Exception:  # noqa: BLE001 - 旧版 qfluentwidgets 没有这个 API
            pass
        # 页面底一律**透明**：底色与氛围光晕由主窗口的 paintEvent 统一画，
        # 页面再铺一层不透明的 BG_PRIMARY 会把光晕整片盖掉（这是本轮改的关键：
        # 画布属于窗口，不属于某一页，切页时光晕不会跟着跳）。
        for itf in self._pages:
            name = itf.objectName()
            D.theme_qss(itf,
                        f"#{name}{{background:transparent;}}",
                        f"#{name}{{background:transparent;}}")
        # stackedWidget 默认给页面铺一层不透明底（FLUENT_WINDOW 样式），同样要透明
        self.stackedWidget.setStyleSheet("background: transparent; border: none;")
        # Mica 云母特效需要持续采样桌面做模糊合成，在部分机器上是滚动卡顿的主因。
        # 默认关闭，设置页可手动开启（见 setting_interface）。
        self._apply_mica(self.config.get("mica_effect", False))
        # 居中
        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = self.frameGeometry().width(), self.frameGeometry().height()
        self.move(desktop.width() // 2 - w // 2, desktop.height() // 2 - h // 2)
        # 统一优化各页面滚动流畅度
        tune_scroll_areas(self)
        self._refresh_status()

    # ------------------------------------------------------------------ #
    # 状态条：后台任务 / 下载目录 / 版本
    def _wire_status_bar(self):
        m = self.ctx.manager
        m.task_added.connect(lambda _info: self._refresh_status())
        m.task_finished.connect(lambda _info: self._refresh_status())
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self.statusBar.opened.connect(self._open_download_dir)
        self._refresh_status()

    def _open_download_dir(self):
        """点状态条右侧（下载目录）直接打开文件夹"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        path = self.ctx.config.get("download_path")
        if path:
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _refresh_status(self):
        tasks = self.ctx.manager.all_tasks()
        running = sum(1 for t in tasks if t.status in ("下载中", "合并中"))
        waiting = sum(1 for t in tasks if t.status == "等待中")
        done = sum(1 for t in tasks if t.status == "已完成")
        self.statusBar.set_busy(bool(running or waiting))
        if running or waiting:
            self.statusBar.set_message(
                f"后台下载中 {running + waiting} 个（进行 {running} · 排队 {waiting}）")
        else:
            self.statusBar.set_message(
                f"空闲 · 已完成 {done} 个下载" if done else "空闲")
        path = self.ctx.config.get("download_path") or ""
        name = os.path.basename(os.path.normpath(path)) if path else ""
        self.statusBar.set_right(f"保存到 {name} · v{__version__}", path)

    # ------------------------------------------------------------------ #
    # 画布：底色 + 两团角落光晕（静态、不参与交互）
    def paintEvent(self, event):
        """窗口级画布。

        FluentWidget / FramelessWindow 已经填过一遍底色，这里在**内容区**
        （侧栏右侧、状态条以上）再叠两团径向光晕：右上主光晕、左下副光晕。
        光晕透明度极低（深色 26/255 ≈ 10%），只在画布留白处可见，不压在
        文字或面板上；面板是不透明的，天然形成"内容浮在氛围上"的层次。
        """
        super().paintEvent(event)
        if self._mica_enabled:
            return                    # 云母特效时窗口半透明，不叠光晕（会把系统背景染色）
        rect = QRectF(self.rect())
        rail = self.tabBar.width() if getattr(self, "tabBar", None) else 0
        top = D.TITLEBAR_H
        bottom = rect.height() - (self.statusBar.height()
                                  if getattr(self, "statusBar", None) else 0)
        area = QRectF(rail, top, rect.width() - rail, max(0.0, bottom - top))
        if area.width() <= 0 or area.height() <= 0:
            return
        canvas = self._canvas_payload(area)
        painter = QPainter(self)
        painter.drawPixmap(area.topLeft(), canvas)

    def _canvas_payload(self, area: QRectF):
        """画布氛围层（**缓存**）。

        光晕是两个大半径径向渐变，每帧重画会明显拖慢滚动；这里按
        「内容区尺寸 + 主题 + 强调色」缓存成一张位图，只有这些条件变化时才重画。
        滚动/切页时只做一次 drawPixmap。
        """
        key = (round(area.width()), round(area.height()), W.isDarkTheme(),
               W.accent().name())
        if getattr(self, "_canvas_key", None) == key and \
                getattr(self, "_canvas_pix", None) is not None:
            return self._canvas_pix

        pix = QPixmap(int(area.width()), int(area.height()))
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        local = QRectF(0, 0, area.width(), area.height())
        dark = W.isDarkTheme()
        alpha = D.GLOW_ALPHA_DARK if dark else D.GLOW_ALPHA_LIGHT
        # 主光晕用当前强调色（换强调色整屏氛围跟着走），副光晕保持固定色相
        primary = QColor(W.accent())
        secondary = QColor(D.GLOW_SECONDARY[1] if dark else D.GLOW_SECONDARY[0])
        radius = min(local.width(), local.height()) * 0.78
        W.draw_glow(painter,
                    QPointF(local.right() - local.width() * 0.14,
                            local.top() + local.height() * 0.06),
                    radius, primary, alpha)
        W.draw_glow(painter,
                    QPointF(local.left() + local.width() * 0.08,
                            local.bottom() - local.height() * 0.04),
                    radius * 0.9, secondary, int(alpha * 0.75))
        # 顶部一条极淡的强调色渐变横线：给画布一个"光源在右上"的方向感
        band = QLinearGradient(QPointF(local.left(), 0),
                               QPointF(local.right(), 0))
        band.setColorAt(0.0, QColor(0, 0, 0, 0))
        band.setColorAt(0.55, W.with_alpha(primary, 26 if dark else 20))
        band.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(band)
        painter.drawRect(QRectF(0, 0, local.width(), 1))
        painter.end()

        self._canvas_key = key
        self._canvas_pix = pix
        return pix

    def _apply_mica(self, enabled: bool):
        """安全地开关 Mica 特效（非 Win11 环境会静默失败）"""
        self._mica_enabled = bool(enabled)
        try:
            self.setMicaEffectEnabled(bool(enabled))
        except Exception:  # noqa: BLE001 - 非 Win11 环境静默降级
            pass
        self.update()

    def set_mica(self, enabled: bool):
        """供设置页调用：切换 Mica 并持久化"""
        self._apply_mica(enabled)
        self.config.set("mica_effect", bool(enabled))

    def _apply_theme(self):
        key = self.config.get("theme", "dark")
        theme = {"dark": Theme.DARK, "light": Theme.LIGHT,
                 "auto": Theme.AUTO}.get(key, Theme.DARK)
        setTheme(theme)
