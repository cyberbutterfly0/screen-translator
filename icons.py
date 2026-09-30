"""图标加载与着色。

lucide 的 SVG 由 ``build_icons.py`` 预渲染成"白色 + alpha"的 PNG，
这里在运行时用 alpha 当遮罩重新着色，所以一套 PNG 就能适配浅色和深色主题。

图标资源随包分发：打包成 exe 后 PyInstaller 会把它们解到 ``sys._MEIPASS``。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageTk


def asset_dir() -> Path:
    """返回图标目录，兼容源码运行和 PyInstaller 打包运行。"""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "assets" / "icons"
    return Path(__file__).resolve().parent / "assets" / "icons"


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 8:
        value = value[:6]
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class IconStore:
    """按 (名称, 尺寸, 颜色) 缓存 PhotoImage。

    必须持有引用，否则 tkinter 会把还在显示的图片回收掉。
    """

    def __init__(self) -> None:
        self._masters: dict[str, Image.Image] = {}
        self._cache: dict[tuple[str, int, str], ImageTk.PhotoImage] = {}
        self.missing: set[str] = set()

    def _master(self, name: str) -> Image.Image | None:
        if name in self._masters:
            return self._masters[name]
        path = asset_dir() / f"{name}.png"
        if not path.exists():
            self.missing.add(name)
            return None
        image = Image.open(path).convert("RGBA")
        self._masters[name] = image
        return image

    def get(self, name: str, size: int, color: str) -> ImageTk.PhotoImage | None:
        key = (name, size, color)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        master = self._master(name)
        if master is None:
            return None

        resized = master.resize((size, size), Image.LANCZOS)
        tinted = Image.new("RGBA", resized.size, _rgb(color) + (255,))
        tinted.putalpha(resized.getchannel("A"))
        photo = ImageTk.PhotoImage(tinted)
        self._cache[key] = photo
        return photo

    def clear(self) -> None:
        """主题切换后旧颜色的缓存就没用了，清掉防止堆积。"""
        self._cache.clear()


#: 全局共享一个实例，避免多处重复解码
store = IconStore()


def icon(name: str, size: int, color: str) -> ImageTk.PhotoImage | None:
    return store.get(name, size, color)


def available() -> list[str]:
    directory = asset_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.png"))
