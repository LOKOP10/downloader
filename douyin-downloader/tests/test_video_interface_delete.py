# -*- coding: utf-8 -*-
"""本地视频页：卡片右上角删除按钮 + 源文件消失后记录自动清理。

覆盖这几类回归（都踩过）：
  1. 源文件被删掉的记录一直挂在卡片墙上，点开才提示文件不存在；
  2. 删除入口只藏在右键菜单里，用户找不到；
  3. 单击卡片就把播放器弹出来（改成双击才播）；
  4. 右键只能删除，没法像资源管理器那样 Ctrl+C 复制文件。
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.config import Config
from app.core.video_library import VideoLibrary
from app.view import video_interface as VI


class _Manager(QObject):
    task_finished = Signal(object)


class VideoInterfaceDeleteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.videos = self.root / "videos"
        self.videos.mkdir()
        # 只要求文件存在：这些用例不真解码视频
        self.alive = self.videos / "alive.mp4"
        self.alive.write_bytes(b"\x00" * 64)
        self.deleted = self.videos / "deleted.mp4"          # 故意不创建：源文件已删
        self.offline = self.root / "unplugged" / "gone.mp4"  # 目录都不存在：盘没挂上
        library = VideoLibrary(self.root / "video_library.json")
        library.add(str(self.alive), "还在的视频")
        library.add(str(self.deleted), "源文件被删的视频")
        library.add(str(self.offline), "外置盘里的视频")
        library.save()
        self.page = self._make_page()

    def tearDown(self):
        self.page.shutdown()
        self.page.close()
        QTest.qWait(60)

    def _make_page(self):
        config = Config(str(self.root / "config.json"))
        page = VI.VideoInterface(SimpleNamespace(config=config, manager=_Manager()))
        page._ffmpeg = ""          # 不截帧：用例不依赖 ffmpeg，避免外部依赖
        page.resize(900, 620)
        page.show()
        self.wait(lambda: "自动清理" in page.status.text() or "访问不到" in page.status.text(),
                  "启动静默清理没有执行")
        return page

    def wait(self, predicate, message="异步 GUI 操作超时", timeout=8.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate(), message)

    def names(self):
        return sorted(Path(item.path).name for item in self.page.library.videos)

    def card_of(self, name):
        for row in range(self.page.shelf.count()):
            item = self.page.shelf.item(row)
            if Path(item.data(Qt.ItemDataRole.UserRole).path).name == name:
                return item
        return None

    # ---------------------------------------------------------------- 清理
    def test_startup_prune_drops_deleted_but_keeps_offline(self):
        """启动后：源文件被删的记录消失，盘没挂载的记录保留（不能把进度清没了）。"""
        self.assertEqual(self.names(), ["alive.mp4", "gone.mp4"])
        self.assertEqual(self.page.shelf.count(), 2)
        on_disk = [Path(entry["path"]).name for entry in json.loads(
            (self.root / "video_library.json").read_text(encoding="utf-8"))["videos"]]
        self.assertNotIn("deleted.mp4", on_disk)
        self.assertIn("gone.mp4", on_disk)
        self.assertIn("已自动清理 1 条失效记录", self.page.status.text())

    def test_check_files_prunes_new_missing_record(self):
        """「检查文件」：清掉源文件已删的记录，并说明清了几条。

        顺带把「不在下载目录这一层」的记录移出：本用例把下载目录指向 videos/，
        于是外置盘那条（root/unplugged/gone.mp4）属于越界记录。
        """
        ghost = self.videos / "ghost.mp4"
        self.page.library.add(str(ghost), "事后被删的视频")
        self.page.library.save()
        self.page.refresh_shelf()
        self.assertEqual(self.page.shelf.count(), 3)

        self.page.ctx.config.set("download_path", str(self.videos))
        self.page.refresh_files()
        self.wait(lambda: "检查完成" in self.page.status.text())
        self.assertEqual(self.names(), ["alive.mp4"])
        self.assertIn("已清理 1 条失效记录", self.page.status.text())
        self.assertIn("移出 1 个不在下载目录这一层的记录", self.page.status.text())

    def test_check_files_scans_download_folder(self):
        """「检查文件」顺带扫描下载目录这一层：新视频补进库，子文件夹里的不进。"""
        fresh = self.videos / "fresh.mp4"
        fresh.write_bytes(b"\x00" * 64)
        nested_dir = self.videos / "author"
        nested_dir.mkdir()
        (nested_dir / "nested.mp4").write_bytes(b"\x00" * 64)

        self.page.ctx.config.set("download_path", str(self.videos))
        self.page.refresh_files()
        self.wait(lambda: "检查完成" in self.page.status.text())

        names = self.names()
        self.assertIn("fresh.mp4", names, "下载目录这一层的新视频应被收录")
        self.assertNotIn("nested.mp4", names, "子文件夹里的视频不应入库")
        self.assertIn("新收录 1 个视频", self.page.status.text())

    def test_open_missing_card_cleans_record(self):
        """点开源文件已消失的卡片：顺手把记录清掉，不留死条目。"""
        ghost = self.videos / "ghost.mp4"
        self.page.library.add(str(ghost), "事后被删的视频")
        self.page.library.save()
        self.page.refresh_shelf()
        item = self.card_of("ghost.mp4")
        self.assertIsNotNone(item)
        self.page.open_item(item)
        self.assertIsNone(self.card_of("ghost.mp4"))
        self.assertIn("源文件已不存在", self.page.status.text())

    # ------------------------------------------------------------ 卡片按钮
    def test_delete_button_hit_area_matches_geometry(self):
        """按钮画在封面右上角：点它命中该卡片，点卡片中部不会误判。"""
        item = self.page.shelf.item(0)
        cell = self.page.shelf.visualItemRect(item)
        button = VI._delete_rect(cell)
        card = VI._card_rect(cell)
        self.assertGreaterEqual(button.left(), card.left())
        self.assertLessEqual(button.right(), card.right())
        self.assertLessEqual(button.bottom(), card.top() + 128)   # 不压到标题区
        self.assertIs(self.page.shelf._delete_item_at(button.center().toPoint()), item)
        center = QPoint(int(cell.center().x()), int(cell.center().y()))
        self.assertIsNone(self.page.shelf._delete_item_at(center))

    def test_delete_button_click_deletes_without_playing(self):
        """点 × 只触发删除，不会顺带把视频播起来（点击被吃掉）。"""
        item = self.page.shelf.item(0)
        point = VI._delete_rect(self.page.shelf.visualItemRect(item)).center().toPoint()
        deleted, played = [], []
        self.page.shelf.on_delete = deleted.append
        # 卡片的激活链路最后都会落到 _show_player，拦它才是「有没有播起来」的事实。
        self.page._show_player = played.append
        self._press(point)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(played, [], "点删除按钮时不应同时开始播放")

        self.page.shelf.on_delete = None
        cell = self.page.shelf.visualItemRect(item)
        self._press(QPoint(int(cell.center().x()), int(cell.center().y())))
        self.assertEqual(played, [], "单击卡片其它位置也不该直接播放（双击才播）")

    def _press(self, point):
        QTest.mouseClick(self.page.shelf.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(point))
        QTest.qWait(80)

    def _double_click(self, point):
        QTest.mouseDClick(self.page.shelf.viewport(), Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, QPoint(point))
        QTest.qWait(80)

    # -------------------------------------------------------- 双击才播放
    def test_single_click_selects_but_double_click_plays(self):
        """单击只选中卡片；双击才打开播放器（卡片墙上误点不该弹出播放器）。"""
        item = self.card_of("alive.mp4")
        self.assertIsNotNone(item)
        played = []
        self.page._show_player = played.append
        cell = self.page.shelf.visualItemRect(item)
        center = QPoint(int(cell.center().x()), int(cell.center().y()))
        self._press(center)
        self.assertEqual(played, [], "单击卡片不应开始播放")
        selected = [Path(w.data(Qt.ItemDataRole.UserRole).path).name
                    for w in self.page.shelf.selectedItems()]
        self.assertEqual(selected, ["alive.mp4"],
                         "单击应当选中这张卡片（给用户看得见的反馈）")
        self._double_click(center)
        self.assertEqual([Path(v.path).name for v in played], ["alive.mp4"],
                         "双击卡片应当播放；重复播放一次说明激活链路接了两次")

    # ------------------------------------------------- 右键复制到剪贴板
    def _trigger_menu_action(self, item, text):
        """构造真实右键菜单并只触发指定项。

        menu.exec() 会自己开事件循环把测试卡住（QMenu.exec 也打不上补丁），
        所以这里走页面拆出来的 _build_menu：菜单内容与点击效果都还是真的。
        """
        menu = self.page._build_menu(item)
        labels = [a.text() for a in menu.actions() if a.text()]
        action = next((a for a in menu.actions() if a.text() == text), None)
        self.assertIsNotNone(action, "右键菜单里没有「" + text + "」，实际有：" + repr(labels))
        action.trigger()
        QTest.qWait(60)
        menu.deleteLater()
        return labels

    def test_context_menu_copies_video_file_to_clipboard(self):
        """右键「复制视频到剪贴板」：登记文件本身（CF_HDROP）+ 路径文本。"""
        item = self.card_of("alive.mp4")
        labels = self._trigger_menu_action(item, "复制视频到剪贴板")
        self.assertIn("复制视频到剪贴板", labels)
        self.assertIn("播放 / 继续播放", labels)
        self.assertIn("删除…", labels)
        mime = QGuiApplication.clipboard().mimeData()
        self.assertIsNotNone(mime)
        # QUrl 会把反斜杠规范成 /，比较前统一一下
        copied = [os.path.normcase(os.path.normpath(u.toLocalFile())) for u in mime.urls()]
        self.assertEqual(copied, [os.path.normcase(os.path.normpath(str(self.alive)))],
                         "剪贴板里应当是文件本身，粘到文件夹里才能真复制出文件")
        self.assertEqual(mime.text(), str(self.alive), "同时要带路径文本，粘到输入框里不空手")
        self.assertIn("已复制到剪贴板", self.page.status.text())

    def test_copy_missing_file_reports_instead_of_copying(self):
        """源文件不在了就别往剪贴板塞一条死路径，直接说清楚。"""
        item = self.card_of("gone.mp4")
        self.assertIsNotNone(item)
        QGuiApplication.clipboard().clear()
        self._trigger_menu_action(item, "复制视频到剪贴板")
        self.assertIn("源文件已不存在", self.page.status.text())
        mime = QGuiApplication.clipboard().mimeData()
        self.assertTrue(mime is None or not mime.urls(), "文件不在了不该复制进剪贴板")

    # --------------------------------------------------------- 删除三选一
    def test_delete_choices_keep_or_remove_source_file(self):
        """「仅移除记录」不动源文件；「删除记录和源文件」才真删。"""
        original_exec = QMessageBox.exec
        chosen = {"text": "仅移除记录"}

        def fake_exec(box):
            target = next((b for b in box.buttons() if b.text() == chosen["text"]), None)
            if target is not None:
                box.clickedButton = (lambda: target)
            return 0

        QMessageBox.exec = fake_exec
        try:
            self.page._delete_from_card(self.card_of("alive.mp4"))
            QTest.qWait(80)
            self.assertIsNone(self.card_of("alive.mp4"))
            self.assertTrue(self.alive.is_file(), "仅移除记录时不应删掉源文件")

            target = self.videos / "to_remove.mp4"
            target.write_bytes(b"\x00" * 64)
            self.page.library.add(str(target), "待真删的视频")
            self.page.library.save()
            self.page.refresh_shelf()
            chosen["text"] = "删除记录和源文件"
            self.page._delete_from_card(self.card_of("to_remove.mp4"))
            QTest.qWait(80)
            self.assertIsNone(self.card_of("to_remove.mp4"))
            self.assertFalse(target.exists(), "选择连源文件删除时应当删掉文件")

            keep = self.videos / "keep.mp4"
            keep.write_bytes(b"\x00" * 64)
            self.page.library.add(str(keep), "取消删除的视频")
            self.page.library.save()
            self.page.refresh_shelf()
            chosen["text"] = "取消"
            self.page._delete_from_card(self.card_of("keep.mp4"))
            QTest.qWait(80)
            self.assertIsNotNone(self.card_of("keep.mp4"))
            self.assertTrue(keep.is_file())
        finally:
            QMessageBox.exec = original_exec


if __name__ == "__main__":
    unittest.main()
