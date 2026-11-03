# -*- coding: utf-8 -*-
"""「选择视频」对话框起始目录的选择逻辑。"""
import os
import tempfile
import unittest

from app.view.frames_interface import pick_start_dir


class PickStartDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "clip.mp4")
        open(self.video, "wb").close()

    def test_prefers_current_video_folder(self):
        download = os.path.join(self.tmp, "downloads")
        os.makedirs(download, exist_ok=True)
        # 已经选过视频时，继续用那个目录（连续处理同一批素材）
        self.assertEqual(pick_start_dir(self.video, download), self.tmp)

    def test_falls_back_to_download_path(self):
        self.assertEqual(pick_start_dir("", self.tmp), self.tmp)

    def test_falls_back_to_home(self):
        self.assertEqual(pick_start_dir("", os.path.join(self.tmp, "missing")),
                         os.path.expanduser("~"))

    def test_missing_video_uses_download_path(self):
        self.assertEqual(
            pick_start_dir(os.path.join(self.tmp, "gone.mp4"), self.tmp), self.tmp)


if __name__ == "__main__":
    unittest.main()
