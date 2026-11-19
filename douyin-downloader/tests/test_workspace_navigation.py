"""真实窗口的键盘导航、任务筛选、主题操作和最小宽度回归。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from app.core.config import Config
from app.core.models import DownloadTaskInfo
from app.main_window import MainWindow


class WorkspaceNavigationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_workspace_navigation_")
        self.root = Path(self.temp.name)
        self.config = Config(str(self.root / "config.json"))
        self.config.update({"download_path": str(self.root / "downloads"),
                            "theme": "dark", "reduce_motion": True})
        self.window = MainWindow(self.config)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def assert_page(self, index):
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.view.currentWidget(),
                      self.window._pages[index])
        self.assertTrue(self.window.tabBar.items[index].hasFocus())

    def test_controls_are_named_focusable_and_tabs_follow_tab_order(self):
        bar = self.window.tabBar
        controls = bar.items + [bar.themeButton,
                                self.window.taskInterface.filterTabs]
        for control in controls:
            self.assertEqual(control.focusPolicy(), Qt.FocusPolicy.StrongFocus)
            self.assertTrue(control.accessibleName())
        bar.items[0].setFocus()
        QTest.keyClick(bar.items[0], Qt.Key.Key_Tab)
        self.assertTrue(bar.items[1].hasFocus())
        self.assertIs(self.window.stackedWidget.view.currentWidget(),
                      self.window.homeInterface)

    def test_arrows_home_and_end_switch_all_six_pages(self):
        bar = self.window.tabBar
        self.assertEqual(len(bar.items), 6)
        bar.items[0].setFocus()
        for index in range(1, 6):
            QTest.keyClick(bar.items[index - 1], Qt.Key.Key_Right)
            self.assert_page(index)
        QTest.keyClick(bar.items[5], Qt.Key.Key_Right)
        self.assert_page(0)
        QTest.keyClick(bar.items[0], Qt.Key.Key_Left)
        self.assert_page(5)
        QTest.keyClick(bar.items[5], Qt.Key.Key_Home)
        self.assert_page(0)
        QTest.keyClick(bar.items[0], Qt.Key.Key_End)
        self.assert_page(5)

    def test_enter_and_space_activate_focused_page(self):
        for index, key in ((3, Qt.Key.Key_Return), (4, Qt.Key.Key_Space)):
            tab = self.window.tabBar.items[index]
            tab.setFocus()
            QTest.keyClick(tab, key)
            self.assert_page(index)

    def test_keyboard_filter_changes_visible_tasks_and_emits_activation(self):
        page = self.window.taskInterface
        manager = self.window.ctx.manager
        tasks = []
        for index, status in enumerate(("等待中", "已完成", "失败")):
            info = DownloadTaskInfo(task_id=f"filter-{index}",
                                    url="https://example.invalid/video",
                                    save_path=str(self.root / f"{index}.mp4"),
                                    name=f"{index}.mp4")
            info.status = status
            manager._tasks[info.task_id] = info
            manager.task_added.emit(info)
            tasks.append(info)
        self.window.switchTo(page)
        self.app.processEvents()
        tabs = page.filterTabs
        tabs.setFocus()
        changed = QSignalSpy(tabs.changed)

        def visible():
            return [info.status for info in tasks
                    if not page.table.isRowHidden(page._rows[info.task_id])]

        QTest.keyClick(tabs, Qt.Key.Key_Right)
        self.assertEqual(visible(), ["等待中"])
        QTest.keyClick(tabs, Qt.Key.Key_Right)
        self.assertEqual(visible(), ["已完成"])
        QTest.keyClick(tabs, Qt.Key.Key_End)
        self.assertEqual(visible(), ["失败"])
        QTest.keyClick(tabs, Qt.Key.Key_Left)
        self.assertEqual(visible(), ["已完成"])
        QTest.keyClick(tabs, Qt.Key.Key_Home)
        self.assertEqual(visible(), ["等待中", "已完成", "失败"])
        activations = changed.count()
        QTest.keyClick(tabs, Qt.Key.Key_Return)
        QTest.keyClick(tabs, Qt.Key.Key_Space)
        self.assertEqual(changed.count(), activations + 2)
        self.assertEqual(visible(), ["等待中", "已完成", "失败"])

    def test_theme_button_accepts_space_and_enter(self):
        button = self.window.tabBar.themeButton
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertEqual(self.config.get("theme"), "light")
        self.assertEqual(self.window.settingInterface.themeCombo.currentIndex(), 1)
        QTest.keyClick(button, Qt.Key.Key_Return)
        self.assertEqual(self.config.get("theme"), "dark")
        self.assertEqual(self.window.settingInterface.themeCombo.currentIndex(), 0)

    def test_all_nine_tabs_and_theme_fit_1024_and_1280(self):
        bar = self.window.tabBar
        for width in (1024, 1280):
            with self.subTest(width=width):
                self.window.resize(width, 720)
                self.app.processEvents()
                self.assertEqual(self.window.width(), width)
                for control in bar.items + [bar.themeButton]:
                    bounds = QRect(control.mapTo(bar, QPoint(0, 0)), control.size())
                    self.assertTrue(bar.rect().contains(bounds),
                                    f"{control.accessibleName()} 超出 {width}px 导航栏：{bounds}")
                    self.assertTrue(control.isVisible())
                self.assertLess(bar.items[-1].geometry().right(),
                                bar.themeButton.geometry().left())
                for tab in bar.items:
                    self.assertGreaterEqual(tab.width(), tab.fontMetrics().horizontalAdvance(
                        tab.accessibleName()) + 20)

    def test_focus_ring_changes_each_controls_render(self):
        self.window.switchTo(self.window.taskInterface)
        self.app.processEvents()
        controls = [self.window.tabBar.items[2], self.window.tabBar.themeButton,
                    self.window.taskInterface.filterTabs]
        for control in controls:
            with self.subTest(control=control.accessibleName()):
                control.clearFocus()
                self.app.processEvents()
                without_focus = control.grab().toImage()
                control.setFocus()
                self.app.processEvents()
                self.assertTrue(control.hasFocus())
                self.assertNotEqual(without_focus, control.grab().toImage())

    def test_square_theme_button_centers_icon(self):
        button = self.window.tabBar.themeButton
        self.assertEqual(button.width(), button.height())
        from app.view.workspace import drawIcon
        rectangles = []

        def draw_and_record(icon, painter, rect):
            rectangles.append(QRect(rect))
            drawIcon(icon, painter, rect)

        with patch("app.view.workspace.drawIcon", new=draw_and_record):
            button.grab()
        self.assertEqual(len(rectangles), 1)
        icon_rect = rectangles[0]
        self.assertEqual(icon_rect.size().width(), 16)
        self.assertEqual(icon_rect.center(), button.rect().center())

    def test_converter_tools_moved_to_settings(self):
        names = [item.accessibleName() for item in self.window.tabBar.items]
        self.assertNotIn("WebP 转 MP4", names)
        self.assertNotIn("MP4 转 GIF", names)
        self.assertNotIn("导出原始帧", names)
        self.assertEqual(len(names), 6)

        settings_idx = self.window._pages.index(self.window.settingInterface)
        self.window.switchTo(self.window.settingInterface)
        self.app.processEvents()

        # 从设置页打开 WebP 转 MP4，顶栏仍高亮「设置」，点返回按钮回到设置页
        self.window.settingInterface.webpBtn.click()
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.converterInterface)
        self.assertTrue(self.window.tabBar.items[settings_idx]._active)
        self.window.converterInterface.backBtn.click()
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.settingInterface)

        # 从设置页打开 MP4 转 GIF，点击顶栏「设置」Tab 也能回到设置页
        self.window.settingInterface.gifBtn.click()
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.gifInterface)
        self.assertTrue(self.window.tabBar.items[settings_idx]._active)
        QTest.mouseClick(self.window.tabBar.items[settings_idx], Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.settingInterface)

        # 从设置页打开导出原始帧，顶栏高亮「设置」，点返回按钮回到设置页
        self.window.settingInterface.framesBtn.click()
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.framesInterface)
        self.assertTrue(self.window.tabBar.items[settings_idx]._active)
        self.window.framesInterface.backBtn.click()
        self.app.processEvents()
        self.assertIs(self.window.stackedWidget.currentWidget(),
                      self.window.settingInterface)


if __name__ == "__main__":
    unittest.main()
