# -*- coding: utf-8 -*-
"""主窗口壳层控件：TopBar / NavRail / StatusBar。"""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets.common.icon import FluentIconBase

from . import design as D
from . import workspace as _W
from .workspace import accent, col, draw_glow, on_theme, text_label


# --------------------------------------------------------------------- 导航
class NavItem(QWidget):
    """侧栏一项：图标 + 文字。选中态 = 内层底 + 左侧 2px 强调色竖条"""

    clicked = Signal(int)

    def __init__(self, icon, text: str, index: int, parent=None, hint: str = ""):
        super().__init__(parent)
        self._icon = icon
        self._text = text
        self._index = index
        self._active = False
        self._hover = False
        self.setFixedHeight(D.NAV_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(hint or text)
        self.setFont(D.font(D.FONT_BODY))
        on_theme(self)

    def set_active(self, on: bool) -> None:
        if bool(on) != self._active:
            self._active = bool(on)
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        if self._active:
            # 选中项：浅色平坦填充（SURFACE_HI），左侧 3px 强调色竖条。
            # 去掉了第二版的横向渐变 —— 选中态只需要"能被一眼看到"，
            # 不需要"看起来有光"。
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.SURFACE_HI))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)
            painter.setBrush(accent())
            painter.drawRoundedRect(
                QRectF(rect.left() + 1, rect.top() + 8, 3, rect.height() - 16), 1.5, 1.5)
        elif self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.BG_HOVER))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)

        side = 15
        icon_x = int(rect.left()) + 14
        icon_y = int(rect.top()) + (int(rect.height()) - side) // 2
        _W.drawIcon(self._icon, painter, QRect(icon_x, icon_y, side, side))

        painter.setPen(col(D.TEXT_PRIMARY if self._active else D.TEXT_SECONDARY))
        painter.setFont(D.font(D.FONT_BODY - 1,
                               D.W_MEDIUM if self._active else D.W_REGULAR))
        text_rect = QRect(icon_x + side + 9, int(rect.top()),
                          max(0, int(rect.width()) - side - 42),
                          int(rect.height()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, self._text)


class RailTool(QWidget):
    """侧栏底部工具按钮：图标 + 文字，整行可点"""

    clicked = Signal()

    def __init__(self, icon, text: str, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._text = text
        self._hover = False
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        on_theme(self)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event):
        if self.isEnabled() and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if not event.isAutoRepeat():
                self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 1, -0.75, -1)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.BG_HOVER))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)
        icon_only = self.width() <= self.height()
        icon_x = (self.width() - 16) // 2 if icon_only else 12
        _W.drawIcon(self._icon, painter, QRect(icon_x, (self.height() - 16) // 2, 16, 16))
        if not icon_only:
            painter.setPen(col(D.TEXT_TERTIARY))
            painter.setFont(D.font(D.FONT_CAPTION))
            painter.drawText(QRect(38, 0, max(0, self.width() - 44), self.height()),
                             Qt.AlignmentFlag.AlignVCenter, self._text)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent(), 1.5))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)


class RailDivider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.HAIRLINE_SOFT))


class TopTab(QWidget):
    """顶部标签：图标 + 文字，选中态 = 柔和底块 + 主色加粗文字 + 底部 2px 强调色指示条。"""

    clicked = Signal(int)
    HEIGHT = 40

    def __init__(self, text: str, index: int, parent=None, hint: str = "",
                 icon=None):
        super().__init__(parent)
        self._text = text
        self._index = index
        self._icon = icon
        self._active = False
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(hint or text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self.setAccessibleDescription(hint or text)
        self.setFont(D.font(D.FONT_BODY))
        fm = self.fontMetrics()
        icon_space = 19 if icon is not None else 0
        self.setMinimumWidth(fm.horizontalAdvance(text) + 24 + icon_space)
        on_theme(self)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        if not self.isEnabled():
            return super().keyPressEvent(event)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if not event.isAutoRepeat():
                self.clicked.emit(self._index)
            event.accept()
            return
        siblings = [item for item in getattr(self.parentWidget(), "items", ())
                    if item.isEnabled()]
        if self in siblings:
            index = siblings.index(self)
            targets = {
                Qt.Key.Key_Left: (index - 1) % len(siblings),
                Qt.Key.Key_Right: (index + 1) % len(siblings),
                Qt.Key.Key_Home: 0,
                Qt.Key.Key_End: len(siblings) - 1,
            }
            if key in targets:
                target = siblings[targets[key]]
                target.setFocus(Qt.FocusReason.TabFocusReason)
                target.clicked.emit(target._index)
                event.accept()
                return
        super().keyPressEvent(event)

    def set_active(self, on: bool) -> None:
        if bool(on) != self._active:
            self._active = bool(on)
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        surface = QRectF(rect).adjusted(2, 4, -2, -4)
        if self._active or self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.SURFACE_HI if self._active else D.BG_HOVER))
            painter.drawRoundedRect(surface, D.RADIUS_SM, D.RADIUS_SM)
        color = col(D.TEXT_PRIMARY) if self._active else (
            col(D.TEXT_SECONDARY) if self._hover else col(D.TEXT_TERTIARY))
        painter.setPen(color)
        painter.setFont(D.font(D.FONT_BODY,
                               D.W_SEMI if self._active else D.W_REGULAR))
        if self._icon is not None:
            icon_s = 15
            tw = painter.fontMetrics().horizontalAdvance(self._text)
            total = icon_s + 5 + tw
            sx = rect.center().x() - total / 2
            iy = (rect.height() - icon_s) // 2
            icon = self._icon
            if isinstance(icon, FluentIconBase):
                icon = icon.icon(color=color)
            _W.drawIcon(icon, painter, QRect(int(sx), iy, icon_s, icon_s))
            painter.drawText(QRectF(sx + icon_s + 5, rect.top(), tw + 4, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter, self._text)
        else:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        if self._active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent())
            painter.drawRoundedRect(
                QRectF(rect.left() + 14, rect.bottom() - 4,
                       rect.width() - 28, 2), 1.0, 1.0)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent(), 1.5))
            painter.drawRoundedRect(surface.adjusted(0.75, 0.75, -0.75, -0.75),
                                    D.RADIUS_SM, D.RADIUS_SM)


class TopBar(QWidget):
    """顶部栏：品牌（左）+ 页面 tab（中）+ 主题切换（右）。

    与标题栏、状态条同属窗口壳层（BG_PRIMARY），与下方内容区面板（SURFACE）
    拉开清晰的主次层次。
    """

    currentChanged = Signal(int)
    HEIGHT = 48

    def __init__(self, items, app_name: str, version: str = "", parent=None):
        """items: [(icon, 文字)] 或 [(icon, 文字, tooltip)]"""
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("topBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 16, 0)
        lay.setSpacing(2)

        self.appLabel = text_label(app_name, self, D.TEXT_PRIMARY,
                                   D.FONT_BODY, D.W_SEMI)
        self.appLabel.setToolTip(f"{app_name} {version}".strip())
        # 版本号已在底部状态条常驻，这里保留属性以兼容既有调用但不重复占用顶栏空间
        self.versionLabel = text_label(version, self, D.TEXT_TERTIARY, D.FONT_MICRO)
        self.versionLabel.hide()
        lay.addWidget(self.appLabel, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(20)

        self.items = []
        for i, item in enumerate(items):
            icon = item[0] if len(item) > 0 else None
            text = item[1]
            hint = item[2] if len(item) > 2 else ""
            tab = TopTab(text, i, self, hint=hint, icon=icon)
            tab.clicked.connect(self._on_clicked)
            lay.addWidget(tab, 0, Qt.AlignmentFlag.AlignVCenter)
            self.items.append(tab)

        lay.addStretch(1)

        self.themeButton = RailTool(FIF.CONSTRACT, "切换浅色 / 深色主题", self)
        self.themeButton.setFixedSize(32, 32)
        lay.addWidget(self.themeButton, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_current(0)
        on_theme(self)

    def set_current(self, index: int) -> None:
        for i, item in enumerate(self.items):
            item.set_active(i == index)

    def count(self) -> int:
        return len(self.items)

    def set_hint(self, text: str) -> None:
        """兼容 NavRail 的接口（顶部栏没有 hint 位，忽略）"""

    def _on_clicked(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.currentChanged.emit(index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.BG_PRIMARY))
        painter.setPen(col(D.BORDER))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

class NavRail(QWidget):
    """左侧导航栏：品牌块 + 页面列表 + 底部工具。

    换掉原来的顶部标签条：标签条横向吃宽度（6 个标签在 1180px 窗口下已经排满），
    而左侧竖排里每个名字都完整，且给内容区留出稳定的最大宽度。
    宽度固定 208px，与内容区之间只有一条 1px 竖线。
    """

    currentChanged = Signal(int)
    WIDTH = 188

    def __init__(self, items, app_name: str, version: str = "", parent=None):
        """items: [(icon, 文字)] 或 [(icon, 文字, tooltip)]"""
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.setObjectName("navRail")

        lay = QVBoxLayout(self)
        # 顶部多留一个标题栏高度：标题栏横跨整窗，侧栏从它下方开始
        lay.setContentsMargins(10, D.TITLEBAR_H + 10, 10, 10)
        lay.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(4, 0, 0, 0)
        brand.setSpacing(0)
        # 去掉品牌图标徽章：侧栏已经有 8 个图标，再加一个"应用图标"
        # 会让品牌块变成第九个按钮（视觉上）。纯文字就够。
        brandText = QVBoxLayout()
        brandText.setContentsMargins(0, 0, 0, 0)
        brandText.setSpacing(0)
        self.appLabel = text_label(app_name, self, D.TEXT_PRIMARY,
                                   D.FONT_BODY + 1, D.W_SEMI)
        brandText.addWidget(self.appLabel)
        self.versionLabel = text_label(version, self, D.TEXT_TERTIARY, D.FONT_MICRO)
        brandText.addWidget(self.versionLabel)
        brand.addLayout(brandText)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(10)

        self.items = []
        for i, item in enumerate(items):
            icon, text = item[0], item[1]
            hint = item[2] if len(item) > 2 else ""
            nav = NavItem(icon, text, i, self, hint=hint)
            nav.clicked.connect(self._on_clicked)
            lay.addWidget(nav)
            self.items.append(nav)

        lay.addStretch(1)
        lay.addWidget(RailDivider(self))
        lay.addSpacing(4)
        self.themeButton = RailTool(FIF.CONSTRACT, "切换浅色 / 深色主题", self)
        lay.addWidget(self.themeButton)
        self.hintLabel = text_label("", self, D.TEXT_TERTIARY,
                                    D.FONT_MICRO, wrap=True)
        self.hintLabel.setContentsMargins(4, 4, 4, 0)
        lay.addWidget(self.hintLabel)
        # 默认高亮第一项（主窗口启动即展示首页，导航要跟状态一致）
        self.set_current(0)

    # ------------------------------------------------------------- 对外
    def set_current(self, index: int) -> None:
        for i, item in enumerate(self.items):
            item.set_active(i == index)

    def count(self) -> int:
        return len(self.items)

    def set_hint(self, text: str) -> None:
        self.hintLabel.setText(text)

    # ------------------------------------------------------------- 内部
    def _on_clicked(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.currentChanged.emit(index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.BG_PRIMARY))
        # 右侧 1px 竖线：侧栏与画布同色，靠这条线分界
        painter.setPen(col(D.BORDER))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


# --------------------------------------------------------------------- 状态条
class _StatusDot(QWidget):
    """5px 状态点：忙碌时强调色 + 外圈光晕，空闲时中性灰"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.setFixedSize(10, 10)
        on_theme(self)

    def set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.rect().center()) + QPointF(0.5, 0.5)
        if self._busy:
            draw_glow(painter, center, 5.0, accent(), 110)
            color = accent()
        else:
            color = col(D.TEXT_TERTIARY)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, 2.6, 2.6)


class StatusBar(QWidget):
    """窗口底部状态条：左侧常驻信息（后台任务 / 提示），右侧下载目录与版本。

    把「现在在跑什么、东西存哪儿」从各页面抽出来，页面因此能少一行说明文字。
    """

    opened = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击打开下载目录")
        self.dot = _StatusDot(self)
        self.leftLabel = text_label("", self, D.TEXT_TERTIARY, D.FONT_MICRO)
        self.rightLabel = text_label("", self, D.TEXT_TERTIARY, D.FONT_MICRO)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(6)
        lay.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.leftLabel, 1)
        lay.addWidget(self.rightLabel, 0)
        on_theme(self)

    def set_busy(self, busy: bool) -> None:
        """状态点：有后台任务时亮起强调色，空闲时是中性点"""
        self.dot.set_busy(bool(busy))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit()

    def set_message(self, text: str) -> None:
        if text != self.leftLabel.text():
            self.leftLabel.setText(text)

    def set_right(self, text: str, tooltip: str = "") -> None:
        if text != self.rightLabel.text():
            self.rightLabel.setText(text)
        self.rightLabel.setToolTip(tooltip or text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.BG_PRIMARY))
        painter.setPen(col(D.BORDER))
        painter.drawLine(0, 0, self.width(), 0)

