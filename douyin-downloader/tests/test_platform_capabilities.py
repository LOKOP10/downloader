"""站点能力契约：界面不再写死站名，能力表与实际来源保持一致。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.models import VideoInfo
from app.core.platform_registry import (DEFAULT_CAPABILITIES, PLATFORM_CAPABILITIES,
                                        PLATFORM_ROUTES, capabilities_for, is_comic,
                                        open_label_for)

APP_DIR = Path(__file__).resolve().parent.parent / "app"


class CapabilityTableTest(unittest.TestCase):
    def test_every_route_declares_capabilities(self):
        self.assertEqual(set(PLATFORM_ROUTES), set(PLATFORM_CAPABILITIES))

    def test_unknown_and_empty_sources_fall_back_to_defaults(self):
        for source in ("", "   ", "local", "telegram", None):
            with self.subTest(source=source):
                self.assertEqual(capabilities_for(source), DEFAULT_CAPABILITIES)
                self.assertFalse(is_comic(source))
                self.assertEqual(open_label_for(source), "分享")

    def test_aliases_normalize_to_the_same_capabilities(self):
        self.assertTrue(is_comic("jm"))
        self.assertTrue(is_comic("18comic"))
        self.assertEqual(open_label_for("yt"), "观看")
        self.assertEqual(capabilities_for("IG"), capabilities_for("instagram"))

    def test_only_comic_platform_reports_comic(self):
        comics = sorted(key for key in PLATFORM_CAPABILITIES if is_comic(key))
        self.assertEqual(comics, ["jmcomic"])
        self.assertEqual(sorted(key for key in PLATFORM_CAPABILITIES if open_label_for(key) == "观看"),
                         ["youtube"])

    def test_default_video_info_is_a_share_style_video(self):
        info = VideoInfo()
        self.assertEqual(info.source, "douyin")
        self.assertFalse(is_comic(info.source))
        self.assertEqual(open_label_for(info.source), "分享")


class ViewLayerHasNoSiteNamesTest(unittest.TestCase):
    """界面层不应再出现 `source == "站点名"` 这类写死判断。"""

    def test_view_modules_do_not_compare_source_to_literal_site(self):
        offenders = []
        for path in sorted((APP_DIR / "view").glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "source ==" in line or "source !=" in line:
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "站点差异应写进 platform_registry 的能力表")


class PreviewUsesCapabilitiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def release(self, dialog):
        """立即销毁对话框，避免它的信号环在后续用例里被 GC 打断主题遍历。"""
        dialog.reject()
        dialog.deleteLater()
        self.app.processEvents()

    def test_comic_source_opens_the_reader_path(self):
        from app.view.preview_dialog import PreviewDialog
        info = VideoInfo(is_image=True, source="jmcomic", image_urls=["jmcover://album/1"])
        with patch.object(PreviewDialog, "_setup_jm") as setup:
            dialog = PreviewDialog(info)
            try:
                setup.assert_called_once()
            finally:
                self.release(dialog)

    def test_image_gallery_uses_paging_controls(self):
        from app.view.preview_dialog import PreviewDialog
        info = VideoInfo(is_image=True, source="instagram",
                         image_urls=["https://example.invalid/1.png", "https://example.invalid/2.png"])
        with patch.object(PreviewDialog, "_setup_jm") as setup, \
                patch.object(PreviewDialog, "_load_image"):
            dialog = PreviewDialog(info)
            try:
                setup.assert_not_called()
                self.assertIsNotNone(dialog.counter)
                dialog._change_image(1)
                self.assertEqual(dialog.index, 1, "非漫画图集应能前后翻页")
                dialog._change_image(1)
                self.assertEqual(dialog.index, 0, "翻页应循环回到第一张")
            finally:
                self.release(dialog)


if __name__ == "__main__":
    unittest.main()
