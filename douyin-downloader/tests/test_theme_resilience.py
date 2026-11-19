"""主题应用要能挺过 qfluentwidgets 的控件回收竞态。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.core.config import Config
from app.main_window import MainWindow


class _Combo:
    def __init__(self):
        self.index = None

    def blockSignals(self, flag):
        return False

    def setCurrentIndex(self, index):
        self.index = index


class ThemeRetryTest(unittest.TestCase):
    """setTheme 遍历全局弱引用表时可能被控件回收打断，重试一次应能恢复。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_tmp_theme_retry_")
        self.addCleanup(self.temp.cleanup)
        self.combo = _Combo()
        self.fake = SimpleNamespace(
            config=Config(os.path.join(self.temp.name, "config.json")),
            settingInterface=SimpleNamespace(themeCombo=self.combo))

    def apply(self, side_effect):
        with patch("app.main_window.setTheme", side_effect=side_effect) as set_theme:
            MainWindow._apply_theme(self.fake)
        return set_theme

    def test_single_gc_race_is_retried(self):
        calls = [RuntimeError("dictionary changed size during iteration"), None]
        set_theme = self.apply(calls)
        self.assertEqual(set_theme.call_count, 2)
        self.assertEqual(self.combo.index, 0)

    def test_persistent_failure_is_not_swallowed(self):
        error = RuntimeError("dictionary changed size during iteration")
        with self.assertRaises(RuntimeError):
            self.apply([error, error])


if __name__ == "__main__":
    unittest.main()
