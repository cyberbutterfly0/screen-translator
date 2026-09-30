"""日志与全局异常钩子。

windowed exe 没有控制台，异常必须落盘，否则用户报"点了没反应"时无从查起。
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import applog
import config as cfgmod


def _reset_logger() -> None:
    """关掉并摘掉 handler，否则临时目录里的日志文件句柄会一直留着。"""
    logger = logging.getLogger(applog.LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
    logger.handlers.clear()


class LoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SCREEN_TRANSLATOR_HOME"] = self.tmp.name
        _reset_logger()

    def tearDown(self) -> None:
        _reset_logger()
        os.environ.pop("SCREEN_TRANSLATOR_HOME", None)
        self.tmp.cleanup()

    def test_writes_message_to_file(self):
        logger = applog.setup_logging()
        logger.info("单元测试消息")
        log_file = cfgmod.log_path()
        self.assertTrue(log_file.exists(), "日志文件应该被创建")
        self.assertIn("单元测试消息", log_file.read_text(encoding="utf-8"))

    def test_setup_is_idempotent(self):
        first = applog.setup_logging()
        handler_count = len(first.handlers)
        second = applog.setup_logging()
        self.assertIs(first, second)
        self.assertEqual(len(second.handlers), handler_count, "重复调用不应重复挂 handler")

    def test_excepthook_records_unhandled_exception(self):
        logger = applog.setup_logging()
        original = sys.excepthook
        try:
            applog.install_excepthooks(logger)
            self.assertIsNot(sys.excepthook, original)
            try:
                raise ValueError("模拟未捕获异常")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            for handler in logger.handlers:
                handler.flush()
            content = cfgmod.log_path().read_text(encoding="utf-8")
            self.assertIn("未捕获异常", content)
            self.assertIn("模拟未捕获异常", content)
        finally:
            sys.excepthook = original

    def test_keyboard_interrupt_is_ignored(self):
        logger = applog.setup_logging()
        original = sys.excepthook
        try:
            applog.install_excepthooks(logger)
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
            for handler in logger.handlers:
                handler.flush()
            content = cfgmod.log_path().read_text(encoding="utf-8") if cfgmod.log_path().exists() else ""
            self.assertNotIn("未捕获异常", content)
        finally:
            sys.excepthook = original


if __name__ == "__main__":
    unittest.main()
