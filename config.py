"""配置与数据文件读写。

配置文件默认位于 ``%APPDATA%\\ScreenTranslator\\config.json``。
开发或便携场景可用环境变量 ``SCREEN_TRANSLATOR_HOME`` 指定目录。

这里不存任何密钥到代码里：api_key 一律由用户在设置界面填写并落盘到本地配置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "ScreenTranslator"
APP_TITLE = "屏幕翻译"
APP_VERSION = "1.0.0"

#: 默认配置。新增字段时只要在这里补一行，旧配置文件会在读取时自动补齐。
DEFAULTS: dict[str, Any] = {
    # —— API ——
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "detail": "original",  # low / high / original / auto
    "timeout": 90,  # 秒
    # —— 交互 ——
    "hotkey": "ctrl+alt+t",
    "show_explanation": True,
    # 小窗弹出时是否抢占焦点。抢焦点能保证 Esc 和 Ctrl+C 立刻可用，
    # 代价是会打断你正在输入的窗口。改成 False 则靠全局 Esc 兜底关闭。
    "popup_steal_focus": True,
    # —— 外观 ——
    "theme": "system",  # light / dark / system（跟随 Windows 应用主题）
    "font_size": 11,
    "popup_opacity": 0.97,
    "popup_width": 560,
    "popup_height": 420,
    # —— 历史 ——
    "history_enabled": True,
    "max_history": 50,
    # —— 其他 ——
    "minimize_to_tray": True,
}


def config_dir() -> Path:
    """返回数据目录（不保证存在）。"""
    env = os.environ.get("SCREEN_TRANSLATOR_HOME")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    return config_dir() / "history.json"


def _ensure_dir() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """读取配置；文件缺失或损坏时回退到默认值，并用默认值补齐缺失字段。"""
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            # 配置损坏不该让程序起不来，直接用默认值继续。
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """写入配置（只保留已知字段，避免脏数据回流）。"""
    _ensure_dir()
    clean = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    config_path().write_text(
        json.dumps(clean, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_history() -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def save_history(items: list[dict[str, Any]], max_items: int = 50) -> None:
    """只保留文本结果，不保存截图。"""
    _ensure_dir()
    trimmed = items[:max_items]
    history_path().write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history(entry: dict[str, Any], max_items: int = 50) -> list[dict[str, Any]]:
    items = load_history()
    items.insert(0, entry)
    items = items[:max_items]
    save_history(items, max_items)
    return items


def mask_key(key: str) -> str:
    """用于界面回显，避免整串密钥直接暴露在屏幕上。"""
    if not key:
        return "(未设置)"
    if len(key) <= 12:
        return key[:3] + "*" * 6
    return f"{key[:6]}...{key[-4:]}"
