# -*- coding: utf-8 -*-
"""工作台壳层的组件层。

界面重做的组织方式：**一个窗口 = 侧栏 + 标题栏 + 内容 + 状态条**，页面只负责内容，
不再各自摆页头、不再人人一张同构大卡片。

这里只放「跨页面复用、且必须一眼认出来」的部件：

- `Panel`          内容面板：1px 描边 + 12px 圆角 + 板色底（自绘，绕开 QSS 注册坑）
- `PanelTitle`     面板内标题行（标题 + 右侧工具位）
- `Chip`           状态徽章（小面积色块，语义色只在这里用）
- `NavRail`        左侧导航：图标 + 文字，选中态左侧 2px 强调色竖条
- `StatusBar`      窗口底部状态条：后台任务 / 下载目录 / 版本
- `EmptyState`     空态：图标 + 一句话 + 一条补充
- `SettingRow`     设置行：标题 + 说明 + 右侧控件（替代 SettingCard）
- `SegmentedTabs`  分段控件（任务筛选、转换模式）
- `PageHeading`    页面标题区

配色、间距、字阶全部取 `design`，本文件不写死色值。
所有自绘控件在构造时把 `qconfig.themeChangedFinished` 连到 `update`，
需要重设文字样式的再额外连一个 `_recolor`（**不要覆写 QWidget.update**：
那是 Qt 的强制重绘入口，覆写会让布局与滚动时的重绘静默失效）。
"""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.icon import drawIcon
from qfluentwidgets.common.style_sheet import isDarkTheme, themeColor

from . import design as D


# --------------------------------------------------------------------- 基础
def col(token) -> QColor:
    """(浅色, 深色) 二元组 → 当前主题下的颜色"""
    return QColor(token[1] if isDarkTheme() else token[0])


def accent() -> QColor:
    return themeColor()


def tint(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(alpha)
    return out


def blend(a: QColor, b: QColor, t: float) -> QColor:
    """线性混色：t=0 取 a，t=1 取 b"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t))


def with_alpha(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(max(0, min(255, int(alpha))))
    return out


def draw_panel_surface(painter: QPainter, rect: QRectF, radius: float,
                       accent_strength: float = 1.0) -> None:
    """画一块面板底（三档渐变的画法，全项目共用）：

    1. 底色：SURFACE → SURFACE_HI 的斜向微渐变。纯色板在近黑画布上会"塌"，
       一点点渐变就能让边缘和中部拉开层次，且不影响任何文字对比度。
    2. 描边：沿用 BORDER，但**左上角描到强调色**（斜向渐变描边）。这是整套
       界面里最"贵"的一笔：静态、无动画、只在边缘出现，却让面板有方向感。
    3. 顶部高光：内侧 1px 白线（深色 6% / 浅色 90%），模拟受光面。
    """
    base = col(D.SURFACE)
    base2 = col(D.SURFACE_HI)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, blend(base, base2, 0.35))
    grad.setColorAt(1.0, base)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(rect, radius, radius)

    # 渐变描边：从左上的强调色 → 右下的常规描边
    edge = QLinearGradient(rect.topLeft(), rect.bottomRight())
    edge.setColorAt(0.0, with_alpha(accent(), int(150 * accent_strength)))
    edge.setColorAt(0.45, col(D.BORDER))
    edge.setColorAt(1.0, col(D.BORDER))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(edge, 1))
    inset = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.drawRoundedRect(inset, radius, radius)

    # 顶部内高光（只在圆角内画一小段，避免像一条白边）
    hl = with_alpha(QColor("#FFFFFF"),
                    22 if isDarkTheme() else 140)
    inner = rect.adjusted(1.5, 1.5, -1.5, -1.5)
    path = QPainterPath()
    path.addRoundedRect(inner, max(0.0, radius - 1), max(0.0, radius - 1))
    painter.save()
    painter.setClipPath(path)
    painter.setPen(QPen(hl, 1))
    painter.drawLine(QPointF(inner.left() + radius, inner.top()),
                     QPointF(inner.right() - radius, inner.top()))
    painter.restore()


def draw_glow(painter: QPainter, center: QPointF, radius: float,
              color: QColor, alpha: int) -> None:
    """角落光晕：径向渐变，中心不透明度 alpha，边缘归零（无边界）"""
    grad = QRadialGradient(center, radius)
    grad.setColorAt(0.0, with_alpha(color, alpha))
    grad.setColorAt(0.55, with_alpha(color, alpha // 3))
    grad.setColorAt(1.0, with_alpha(color, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(grad)
    painter.drawEllipse(center, radius, radius)


def on_theme(widget: QWidget, *callbacks) -> None:
    """主题切换后重绘 / 重设样式。

    自绘控件必须显式连这个信号，否则换主题后颜色停在上一套（qfulentwidgets
    只会刷它自己注册过样式的控件）。
    """
    qconfig.themeChangedFinished.connect(widget.update)
    for cb in callbacks:
        qconfig.themeChangedFinished.connect(cb)


class Panel(QFrame):
    """内容面板。

    之前的卡片是 `CardWidget`（5% 白叠加），一屏里放五张以上就分不出层级。
    这里把「面板」做成有明确边界的一档：板色 + 1px 描边 + 12px 圆角，
    再往里需要分区时用 `PanelTitle` 与分隔线，而不是套第二层卡片。
    """

    def __init__(self, parent=None, padding=(D.CARD_PAD_H, D.CARD_PAD_V,
                                            D.CARD_PAD_H, D.CARD_PAD_V),
                 spacing: int = D.CARD_GAP, radius: int = D.RADIUS_LG):
        super().__init__(parent)
        self._radius = radius
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.body = QVBoxLayout(self)
        left, top, right, bottom = padding
        self.body.setContentsMargins(left, top, right, bottom)
        self.body.setSpacing(spacing)
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_panel_surface(painter, QRectF(self.rect()), self._radius)


class _Text(QLabel):
    """带 token 色的裸 QLabel：颜色随主题重设。

    裸 QLabel 用 `setStyleSheet` 是安全的（不像 qfluentwidgets 的
    `setCustomStyleSheet` 需要先注册），代价是颜色要自己在主题切换时重设。
    """

    def __init__(self, text: str = "", parent=None, token=D.TEXT_PRIMARY,
                 size: int = D.FONT_BODY, weight=D.W_REGULAR, wrap: bool = False):
        super().__init__(text, parent)
        self._token = token
        self.setFont(D.font(size, weight))
        self.setWordWrap(wrap)
        self._apply()

    def set_token(self, token) -> None:
        self._token = token
        self._apply()

    def _apply(self) -> None:
        self.setStyleSheet(
            f"color:{col(self._token).name()};background:transparent;")

    def setText(self, text: str) -> None:      # noqa: N802 - Qt 命名
        super().setText(text)
        self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply()


def text_label(text: str = "", parent=None, token=D.TEXT_PRIMARY,
               size: int = D.FONT_BODY, weight=D.W_REGULAR, wrap: bool = False) -> _Text:
    """便捷构造：正文 / 说明 / 弱化三级文字，统一走 token"""
    return _Text(text, parent, token, size, weight, wrap)


class PanelTitle(QWidget):
    """面板标题行：左侧标题（14 DemiBold）+ 可选说明，右侧工具位由调用方填"""

    def __init__(self, text: str, parent=None, note: str = ""):
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(D.GAP_SM)
        self.label = text_label(text, self, D.TEXT_PRIMARY, D.FONT_BODY, D.W_SEMI)
        self.row.addWidget(self.label)
        self.note = None
        if note:
            self.note = text_label(note, self, D.TEXT_TERTIARY, D.FONT_CAPTION)
            self.row.addWidget(self.note)
        self.row.addStretch(1)

    def add(self, widget) -> None:
        self.row.addWidget(widget)

    def set_note(self, text: str) -> None:
        if self.note is not None:
            self.note.setText(text)


class Chip(QLabel):
    """状态徽章：小圆角 + 语义色。**语义色只允许出现在这种小面积上**。"""

    def __init__(self, text: str = "", color=None, parent=None):
        super().__init__(text, parent)
        self.setFont(D.font(D.FONT_MICRO, D.W_MEDIUM))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._color = color or D.TEXT_TERTIARY
        self._radius = 7
        self.setMinimumHeight(18)
        self._relayout()
        on_theme(self)

    def set_color(self, color) -> None:
        self._color = color
        self.update()

    def setText(self, text: str) -> None:      # noqa: N802 - Qt 命名
        super().setText(text)
        self._relayout()

    def _relayout(self) -> None:
        # 宽度按文字实测 + 左右内距，避免徽章被布局压成一条
        width = self.fontMetrics().horizontalAdvance(self.text()) + 16
        self.setMinimumWidth(max(34, width))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        base = col(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint(base, 40))
        painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.setPen(base)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class Divider(QFrame):
    """1px 分隔线（辅助色，比主描边更弱）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.HAIRLINE_SOFT))


class IconBadge(QWidget):
    """圆角底板里放一个图标（空态、品牌块、设置行通用）"""

    def __init__(self, icon, parent=None, size: int = 40, icon_size: int = 16):
        super().__init__(parent)
        self._icon = icon
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        on_theme(self)

    def set_icon(self, icon) -> None:
        self._icon = icon
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        # 图标底板：强调色 → 内层底的柔和对角渐变，让图标块看起来是"发光的小方块"
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, with_alpha(accent(), 64 if isDarkTheme() else 40))
        grad.setColorAt(1.0, with_alpha(col(D.SURFACE_HI), 235))
        painter.setPen(QPen(with_alpha(accent(), 70), 1))
        painter.setBrush(grad)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                D.RADIUS_LG, D.RADIUS_LG)
        side = self._icon_size
        drawIcon(self._icon, painter,
                 QRect((self.width() - side) // 2, (self.height() - side) // 2,
                       side, side))


class EmptyState(QWidget):
    """空态：一个图标、一句主文案、一段说明。空页面不该是一片灰。"""

    def __init__(self, icon, title: str, body: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 20, 0, 20)
        lay.setSpacing(D.GAP_SM)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.iconWidget = IconBadge(icon, self, 40, 18)
        lay.addWidget(self.iconWidget, 0, Qt.AlignmentFlag.AlignHCenter)

        self.titleLabel = text_label(title, self, D.TEXT_SECONDARY,
                                     D.FONT_BODY, D.W_MEDIUM)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.titleLabel)

        self.bodyLabel = None
        if body:
            self.bodyLabel = text_label(body, self, D.TEXT_TERTIARY,
                                        D.FONT_CAPTION, wrap=True)
            self.bodyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # 居中布局不会给换行文本一个自然宽度，显式给上下限，
            # 否则 wordWrap 会被压成一列一两个字（中文没有空格，会在任意字间断行）
            self.bodyLabel.setMinimumWidth(260)
            self.bodyLabel.setMaximumWidth(460)
            lay.addWidget(self.bodyLabel, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_text(self, title: str, body: str = "") -> None:
        self.titleLabel.setText(title)
        if self.bodyLabel is not None:
            self.bodyLabel.setText(body)

    def set_body_visible(self, on: bool) -> None:
        if self.bodyLabel is not None:
            self.bodyLabel.setVisible(bool(on))


class SegmentedTabs(QWidget):
    """分段控件：同一位置切换视图，不跳页。

    比一排等权按钮更省横排空间，也明确「这些选项是互斥的」。
    """

    changed = Signal(int)

    def __init__(self, items, parent=None, height: int = 30, min_width: int = 78):
        super().__init__(parent)
        self._items = list(items)
        self._current = 0
        self._hover = -1
        self.setFixedHeight(height)
        self.setMinimumWidth(min_width * len(self._items))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        on_theme(self)

    def current(self) -> int:
        return self._current

    def set_current(self, index: int, emit: bool = False) -> None:
        if 0 <= index < len(self._items) and index != self._current:
            self._current = index
            self.update()
            if emit:
                self.changed.emit(index)

    def set_item_text(self, index: int, text: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index] = text
            self.update()

    def mouseMoveEvent(self, event):
        index = self._index_at(event.position().x(), event.position().y())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._index_at(event.position().x(), event.position().y())
            if index >= 0:
                self.set_current(index, emit=True)

    def _index_at(self, x: float, y: float) -> int:
        if not self.rect().contains(int(x), int(y)):
            return -1
        step = self.width() / max(1, len(self._items))
        return min(len(self._items) - 1, int(x // step))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(col(D.BORDER), 1))
        painter.setBrush(col(D.BG_TERTIARY))
        painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)

        step = rect.width() / max(1, len(self._items))
        for i, text in enumerate(self._items):
            cell = QRectF(rect.left() + i * step + 2, rect.top() + 2,
                          step - 4, rect.height() - 4)
            if i == self._current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(col(D.SURFACE))
                painter.drawRoundedRect(cell, D.RADIUS_SM, D.RADIUS_SM)
                painter.setPen(col(D.TEXT_PRIMARY))
                font = D.font(D.FONT_CAPTION, D.W_SEMI)
            else:
                painter.setPen(col(D.TEXT_SECONDARY if i == self._hover
                                   else D.TEXT_TERTIARY))
                font = D.font(D.FONT_CAPTION)
            painter.setFont(font)
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, text)


class SettingRow(QWidget):
    """设置页的一行：左侧两行文字（标题 + 说明），右侧控件。

    不继承 `SettingCard`：那个控件的图标、内距与卡片圆角是给"卡片流"设计的，
    放进面板里会显得套娃。这里只画一条底部分隔线，行与行靠线分，不靠卡。
    """

    def __init__(self, title: str, desc: str = "", parent=None, control=None,
                 icon=None):
        super().__init__(parent)
        self.setMinimumHeight(D.ROW_H)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 9, 0, 9)
        row.setSpacing(D.GAP_MD)

        self.iconWidget = None
        if icon is not None:
            self.iconWidget = IconBadge(icon, self, 28, 14)
            row.addWidget(self.iconWidget, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.titleLabel = text_label(title, self, D.TEXT_PRIMARY, D.FONT_BODY)
        text.addWidget(self.titleLabel)
        self.descLabel = None
        if desc:
            self.descLabel = text_label(desc, self, D.TEXT_TERTIARY,
                                        D.FONT_CAPTION, wrap=True)
            text.addWidget(self.descLabel)
        row.addLayout(text, 1)

        self.control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def set_desc(self, text: str) -> None:
        if self.descLabel is not None:
            self.descLabel.setText(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(col(D.HAIRLINE_SOFT), 1))
        y = self.rect().bottom()
        painter.drawLine(self.rect().left(), y, self.rect().right(), y)


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
        rect = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self._active:
            # 选中项：横向渐变底（强调色 14% → 透明），左侧 2px 强调色竖条
            fill = QLinearGradient(rect.topLeft(), rect.topRight())
            fill.setColorAt(0.0, with_alpha(accent(), 46 if isDarkTheme() else 30))
            fill.setColorAt(0.7, with_alpha(accent(), 16 if isDarkTheme() else 10))
            fill.setColorAt(1.0, with_alpha(accent(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)
            bar = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
            bar.setColorAt(0.0, with_alpha(accent(), 200))
            bar.setColorAt(1.0, with_alpha(accent(), 90))
            painter.setBrush(bar)
            painter.drawRoundedRect(
                QRectF(rect.left(), rect.top() + 7, 2, rect.height() - 14), 1, 1)
        elif self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.BG_HOVER))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)

        side = 16
        icon_x = int(rect.left()) + 12
        icon_y = int(rect.top()) + (int(rect.height()) - side) // 2
        drawIcon(self._icon, painter, QRect(icon_x, icon_y, side, side))

        painter.setPen(col(D.TEXT_PRIMARY if self._active else D.TEXT_SECONDARY))
        painter.setFont(D.font(D.FONT_BODY,
                               D.W_MEDIUM if self._active else D.W_REGULAR))
        text_rect = QRect(icon_x + side + 10, int(rect.top()),
                          max(0, int(rect.width()) - side - 40),
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
        on_theme(self)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col(D.BG_HOVER))
            painter.drawRoundedRect(rect, D.RADIUS_MD, D.RADIUS_MD)
        drawIcon(self._icon, painter, QRect(12, (self.height() - 16) // 2, 16, 16))
        painter.setPen(col(D.TEXT_TERTIARY))
        painter.setFont(D.font(D.FONT_CAPTION))
        painter.drawText(QRect(38, 0, max(0, self.width() - 44), self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self._text)


class RailDivider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), col(D.HAIRLINE_SOFT))


class NavRail(QWidget):
    """左侧导航栏：品牌块 + 页面列表 + 底部工具。

    换掉原来的顶部标签条：标签条横向吃宽度（6 个标签在 1180px 窗口下已经排满），
    而左侧竖排里每个名字都完整，且给内容区留出稳定的最大宽度。
    宽度固定 208px，与内容区之间只有一条 1px 竖线。
    """

    currentChanged = Signal(int)
    WIDTH = 208

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
        brand.setSpacing(9)
        self.brandIcon = IconBadge(FIF.VIDEO, self, 30, 16)
        brand.addWidget(self.brandIcon, 0, Qt.AlignmentFlag.AlignVCenter)
        brandText = QVBoxLayout()
        brandText.setContentsMargins(0, 0, 0, 0)
        brandText.setSpacing(0)
        self.appLabel = text_label(app_name, self, D.TEXT_PRIMARY,
                                   D.FONT_BODY, D.W_SEMI)
        brandText.addWidget(self.appLabel)
        self.versionLabel = text_label(version, self, D.TEXT_TERTIARY, D.FONT_MICRO)
        brandText.addWidget(self.versionLabel)
        brand.addLayout(brandText)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(14)
        lay.addWidget(text_label("工作区", self, D.TEXT_TERTIARY, D.FONT_MICRO))
        lay.addSpacing(2)

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
        if 0 <= index < len(self.items) and not self.items[index]._active:
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


# --------------------------------------------------------------------- 页头
class _AccentBar(QWidget):
    """页头左侧的渐变小竖条（3×18）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(3, 18)
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(QPointF(0, 0), QPointF(0, self.height()))
        grad.setColorAt(0.0, with_alpha(accent(), 255))
        grad.setColorAt(1.0, with_alpha(accent(), 90))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)


class GradientTitle(QLabel):
    """渐变标题文字：主色 → 主色与强调色的混合。

    标题是每屏唯一的"大字"，给它一点点色彩过渡就够撑起整页的气质；
    再往上（发光、描边、动画）就变成营销页了，工具界面不需要。
    """

    def __init__(self, text: str, parent=None, size: int = D.FONT_TITLE,
                 weight=D.W_SEMI):
        super().__init__(text, parent)
        self._size = size
        self.setFont(D.font(size, weight))
        on_theme(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.font()
        painter.setFont(font)
        rect = self.rect()
        grad = QLinearGradient(QPointF(rect.left(), 0), QPointF(rect.right(), 0))
        grad.setColorAt(0.0, col(D.TEXT_PRIMARY))
        grad.setColorAt(1.0, blend(col(D.TEXT_PRIMARY), accent(), 0.75))
        painter.setPen(QPen(grad, 1))
        metrics = painter.fontMetrics()
        text = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                  rect.width())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft |
                                   Qt.AlignmentFlag.AlignVCenter), text)


class PageHeading(QWidget):
    """页面标题区（18px 半粗 + 一行 12px 说明）。

    上一轮用 24px 做页标题，深色下和窗口标题抢镜，还占掉近 80px 纵向空间。
    内容区本来就窄（760 高扣掉标题栏后约 700），页头收到 18px + 说明，
    省下的空间给内容。
    """

    def __init__(self, title: str, meta: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        # 页头左侧 3px 渐变小竖条：与侧栏选中条同一种语言，
        # 让"这一页属于哪个工作区"在视线起点就有落点
        self.bar = _AccentBar(self)
        lay.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
        self.titleLabel = GradientTitle(title, self, D.FONT_TITLE, D.W_SEMI)
        text.addWidget(self.titleLabel)
        self.metaLabel = None
        if meta:
            self.metaLabel = text_label(meta, self, D.TEXT_TERTIARY, D.FONT_CAPTION)
            text.addWidget(self.metaLabel)
        lay.addLayout(text, 1)

    def set_meta(self, text: str) -> None:
        if self.metaLabel is not None:
            self.metaLabel.setText(text)


def page_body(host: QWidget, pad: int = 28, spacing: int = 16) -> QVBoxLayout:
    """内容区根布局：统一页边距与区块间距"""
    box = QVBoxLayout(host)
    box.setContentsMargins(pad, 20, pad, 16)
    box.setSpacing(spacing)
    return box
