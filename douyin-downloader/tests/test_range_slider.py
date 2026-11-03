# -*- coding: utf-8 -*-
"""双滑块时间轴的坐标换算与区间约束测试（纯函数，不需要 GUI）。"""
import unittest

from app.view.range_slider import PADDING, ms_at, x_at


class CoordinateTest(unittest.TestCase):
    def test_left_and_right_edges_map_to_bounds(self):
        width, duration = 520, 60000
        self.assertEqual(ms_at(PADDING, width, duration), 0)
        self.assertEqual(ms_at(width - PADDING, width, duration), duration)

    def test_clamped_outside_track(self):
        width, duration = 520, 60000
        self.assertEqual(ms_at(0, width, duration), 0)
        self.assertEqual(ms_at(width + 50, width, duration), duration)

    def test_roundtrip(self):
        width, duration = 800, 227550
        for ms in (0, 1234, 10000, 12000, 227550):
            self.assertAlmostEqual(ms_at(x_at(ms, width, duration), width, duration), ms, delta=400)

    def test_zero_duration_is_safe(self):
        self.assertEqual(ms_at(300, 520, 0), 0)
        self.assertEqual(x_at(500, 520, 0), float(PADDING))


if __name__ == "__main__":
    unittest.main()
