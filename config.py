"""配置与数据文件读写。

配置文件默认位于 ``%APPDATA%\\ScreenTranslator\\config.json``。
开发或便携场景可用环境变量 ``SCREEN_TRANSLATOR_HOME`` 指定目录。

这里不存任何密钥到代码里：api_key 一律由用户在设置界面填写并落盘到本地配置。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_NAME = "ScreenTranslator"
APP_TITLE = "屏幕翻译"
APP_VERSION = "1.0.4"

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


def log_path() -> Path:
    return config_dir() / "app.log"


# ---------------------------------------------------------------- 密钥保护

#: 加密后的密钥在文件里的前缀。没有这个前缀的值按明文处理（兼容旧配置）。
SECRET_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


#: 最近一次 DPAPI 调用的 Win32 错误码，供自检和排查用
_dpapi_last_error: int = 0


def dpapi_last_error() -> int:
    """返回最近一次 DPAPI 失败的错误码（0 表示没失败过）。"""
    return _dpapi_last_error


def _dpapi(data: bytes, decrypt: bool) -> bytes | None:
    """调用 Windows DPAPI。失败返回 None，错误码留在 :func:`dpapi_last_error`。

    CryptProtectData 加密出来的数据绑定当前 Windows 账户：
    文件被拷到别的机器、或被别的用户读到，都解不开。

    这里用 ``WinDLL(..., use_last_error=True)`` 而不是 ``ctypes.windll``：
    后者的 ``GetLastError()`` 可能已经被其他调用覆盖，读出来是个没意义的数字，
    排查 DPAPI 失败时会被误导。
    """
    global _dpapi_last_error
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # buffer 必须活到 API 调用结束，这里靠局部变量持有引用
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()

        func = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        ok = func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok or not blob_out.pbData:
            _dpapi_last_error = ctypes.get_last_error()
            return None

        _dpapi_last_error = 0
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        _dpapi_last_error = -1
        return None


def is_encrypted(stored: str) -> bool:
    """判断存盘的值到底是不是 DPAPI 密文。"""
    return bool(stored) and stored.startswith(SECRET_PREFIX)


def encryption_available() -> bool:
    """探测 DPAPI 在当前环境里能不能用。

    受限环境（沙箱、服务账户、部分企业策略）下 CryptProtectData 会失败。
    调用方应该先问这个函数再决定要不要让明文落盘——
    绝不能像早期版本那样加密失败还一声不吭地存明文。
    """
    return _dpapi(b"screen-translator-probe", decrypt=False) is not None


def encrypt_secret(value: str) -> str:
    """加密 API Key。

    DPAPI 不可用时返回**明文**（保证程序仍能用），但这种结果不带
    ``SECRET_PREFIX``，调用方必须用 :func:`is_encrypted` 检查并在写盘前告知用户。
    """
    if not value:
        return ""
    blob = _dpapi(value.encode("utf-8"), decrypt=False)
    if blob is None:
        return value
    return SECRET_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(stored: str) -> str:
    """解密 API Key。不带前缀的值是旧版写的明文，直接返回。"""
    if not stored:
        return ""
    if not stored.startswith(SECRET_PREFIX):
        return stored
    try:
        blob = base64.b64decode(stored[len(SECRET_PREFIX) :])
    except Exception:
        return ""
    plain = _dpapi(blob, decrypt=True)
    if plain is None:
        return ""
    return plain.decode("utf-8", errors="replace")


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
    cfg["api_key"] = decrypt_secret(str(cfg.get("api_key") or ""))
    return cfg


def backup_path() -> Path:
    return config_dir() / "config.backup.json"


def save_config(cfg: dict[str, Any]) -> None:
    """写入配置（只保留已知字段，避免脏数据回流）。

    覆盖前先把现有配置备份成 ``config.backup.json``：
    万一误把 API Key 清空了，还能从备份里找回。
    """
    _ensure_dir()
    path = config_path()
    if path.exists():
        try:
            backup_path().write_bytes(path.read_bytes())
        except OSError:
            pass  # 备份失败不该阻止保存

    clean = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    clean["api_key"] = encrypt_secret(str(clean.get("api_key") or ""))
    path.write_text(
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
