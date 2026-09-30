"""主程序的并发行为：旧请求的结果不允许覆盖新请求的。

这一条曾经是真 bug：_busy 在请求发出前就被重置，用户可以在前一个请求
还没回来时再框一次，晚到的旧结果会把新结果盖掉。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_client
import main

ANCHOR = (0, 0, 10, 10)


class StaleResultTests(unittest.TestCase):
    @staticmethod
    def _app(current_seq: int) -> MagicMock:
        app = MagicMock()
        app._request_seq = current_seq
        # 关掉历史写入，避免单元测试碰真实文件
        app.cfg.get.return_value = False
        return app

    def test_stale_result_is_dropped(self):
        app = self._app(current_seq=3)
        main.App._on_result(app, api_client.Result(translation="旧结果"), ANCHOR, 2)
        app.popup.show_result.assert_not_called()

    def test_latest_result_is_shown(self):
        app = self._app(current_seq=3)
        main.App._on_result(app, api_client.Result(translation="新结果"), ANCHOR, 3)
        app.popup.show_result.assert_called_once()

    def test_stale_error_is_dropped(self):
        app = self._app(current_seq=3)
        main.App._on_error(app, "旧错误", ANCHOR, 2)
        app.popup.show_error.assert_not_called()

    def test_latest_error_is_shown(self):
        app = self._app(current_seq=3)
        main.App._on_error(app, "新错误", ANCHOR, 3)
        app.popup.show_error.assert_called_once()


class SelftestIsolationTests(unittest.TestCase):
    """自检的每一项都必须独立，一项炸了不能影响后面的检查。"""

    def test_failing_section_does_not_stop_later_sections(self):
        import selftest

        report = selftest.Report()
        executed: list[str] = []

        def boom(_report):
            raise OSError("模拟屏幕抓取失败")

        report.section("会炸的一项", boom)
        report.section("后面的一项", lambda r: executed.append("ran"))

        self.assertEqual(report.failures, 1)
        self.assertEqual(executed, ["ran"], "前一项抛异常后，后面的检查仍然应该执行")


if __name__ == "__main__":
    unittest.main()
