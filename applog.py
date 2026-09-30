"""日志与全局异常捕获。

打包后的 exe 是 windowed 模式，没有控制台——`print` 出去的东西没人看得见。
所以异常必须落到文件里，否则用户报"点了没反应"时只能盲猜。

日志只记事件与异常，不记录截图内容或 API Key。
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

import config as cfgmod

LOGGER_NAME = "screen_translator"
_MAX_BYTES = 512 * 1024
_BACKUPS = 2


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def ensure_utf8_stdio() -> None:
    """把 stdout / stderr 切成 UTF-8。

    Windows 英文版的默认控制台代码页是 cp1252，直接 print 中文会抛
    UnicodeEncodeError —— 自检脚本在那种机器上会崩在打印标题的第一行，
    什么有用信息都看不到。命令行脚本开头都该调这个。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 改不了也不该影响主流程
            pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置日志。重复调用安全（不会重复挂 handler）。"""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    try:
        cfgmod.config_dir().mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            cfgmod.log_path(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logger.addHandler(handler)
    except OSError:
        # 日志写不进去不该阻止程序启动
        logger.addHandler(logging.NullHandler())
    return logger


def install_excepthooks(logger: logging.Logger) -> None:
    """让主线程和子线程的未捕获异常都落盘。"""

    def main_hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        name = args.thread.name if args.thread else "unknown"
        logger.error(
            "线程 %s 内未捕获异常",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = main_hook
    threading.excepthook = thread_hook
