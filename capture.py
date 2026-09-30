"""屏幕框选。

设计要点（也是这个程序最容易出错的地方）：

1. **DPI**：进程必须声明 per-monitor DPI aware，否则在 125%/150% 缩放下
   ``ImageGrab`` 截到的物理像素和 tkinter 报告的窗口坐标会对不上，选区偏移。
2. **多屏**：用虚拟桌面（virtual screen）坐标。左上角可能是负数（副屏在主屏左侧
   或上方），``ImageGrab.grab(all_screens=True)`` 返回的图以虚拟桌面左上角为原点，
   和覆盖层窗口的原点一致，所以选区在 canvas 里的坐标可以直接当作裁剪坐标。
3. **实时预览**：底层窗口是半透明黑遮罩（负责压暗屏幕），上层"亮窗"显示选中
   区域的原始像素。亮窗里放的是一整张原图，靠坐标偏移实现裁剪效果，
   拖动时只改窗口位置和图片偏移，不重新编码图片，所以不卡。

若某些机器上双层窗口出现闪烁，把 :data:`LIVE_PREVIEW` 改成 ``False``
即可退化为"单窗口 + 边框"模式。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageGrab, ImageTk

#: 关掉可以退回单窗口边框模式
LIVE_PREVIEW = True

#: 遮罩暗度，0 = 全透明，1 = 全黑
MASK_ALPHA = 0.35

#: 小于这个像素的选区视为误触，按取消处理
MIN_SELECTION = 8

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


@dataclass
class Selection:
    """框选结果。坐标是虚拟桌面坐标，可直接用于裁剪截图。"""

    box: tuple[int, int, int, int]
    image: Image.Image

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]


def enable_dpi_awareness() -> str:
    """把进程设为 per-monitor DPI aware。必须在创建 Tk 之前调用。"""
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """返回虚拟桌面 (x, y, width, height)。"""
    user32 = ctypes.windll.user32
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if w <= 0 or h <= 0:
        # 极端情况下退回主屏
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        x, y = 0, 0
    return x, y, w, h


def grab_all_screens() -> Image.Image:
    """抓取整个虚拟桌面。"""
    return ImageGrab.grab(all_screens=True)


class RegionSelector:
    """模态的框选覆盖层。

    用法::

        selector = RegionSelector(root)
        result = selector.select()
        if result is not None:
            ...
    """

    def __init__(self, root: tk.Misc, on_status: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.on_status = on_status or (lambda _msg: None)

        self.result: Selection | None = None
        self._screen: Image.Image | None = None
        self._vx = self._vy = 0
        self._vw = self._vh = 0

        self._mask: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._live: tk.Toplevel | None = None
        self._live_canvas: tk.Canvas | None = None
        self._live_item: int | None = None

        self._photo: ImageTk.PhotoImage | None = None
        self._rect: int | None = None
        self._hint: int | None = None
        self._start: tuple[int, int] | None = None
        self._current: tuple[int, int] | None = None

    # ---------- 对外入口 ----------

    def select(self) -> Selection | None:
        self._screen = grab_all_screens()
        self._vx, self._vy, self._vw, self._vh = virtual_screen_rect()
        self._build_mask()
        self._build_live()
        try:
            self.root.wait_window(self._mask)
        finally:
            self._teardown()
        return self.result

    # ---------- 构建窗口 ----------

    def _build_mask(self) -> None:
        mask = tk.Toplevel(self.root)
        mask.overrideredirect(True)
        mask.attributes("-topmost", True)
        mask.attributes("-alpha", MASK_ALPHA)
        mask.configure(bg="black")
        mask.geometry(f"{self._vw}x{self._vh}{self._vx:+d}{self._vy:+d}")

        canvas = tk.Canvas(
            mask,
            highlightthickness=0,
            bd=0,
            bg="black",
            cursor="crosshair",
        )
        canvas.pack(fill="both", expand=True)

        mask.bind("<ButtonPress-1>", self._on_press)
        mask.bind("<B1-Motion>", self._on_drag)
        mask.bind("<ButtonRelease-1>", self._on_release)
        mask.bind("<ButtonPress-3>", lambda _e: self._cancel())
        mask.bind("<Escape>", lambda _e: self._cancel())

        self._mask = mask
        self._canvas = canvas

        mask.deiconify()
        mask.lift()
        mask.focus_force()
        mask.grab_set()

    def _build_live(self) -> None:
        if not LIVE_PREVIEW:
            return
        live = tk.Toplevel(self.root)
        live.overrideredirect(True)
        live.attributes("-topmost", True)
        # 初始放到屏幕外，避免拖动前闪一下
        live.geometry(f"1x1{self._vx - 10:+d}{self._vy - 10:+d}")
        live.configure(bg="black")

        canvas = tk.Canvas(live, highlightthickness=0, bd=0, bg="black")
        canvas.pack(fill="both", expand=True)

        self._live = live
        self._live_canvas = canvas

        # 只创建一次全屏 PhotoImage，之后靠坐标偏移显示选区。
        assert self._screen is not None
        self._photo = ImageTk.PhotoImage(self._screen)
        self._live_item = canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # ---------- 事件 ----------

    def _on_press(self, event: tk.Event) -> None:
        self._start = (event.x, event.y)
        self._current = (event.x, event.y)
        if self._rect is not None and self._canvas is not None:
            self._canvas.delete(self._rect)
            self._rect = None

    def _on_drag(self, event: tk.Event) -> None:
        if self._start is None:
            return
        self._current = (event.x, event.y)
        self._update_visuals()

    def _on_release(self, event: tk.Event) -> None:
        if self._start is None:
            return
        self._current = (event.x, event.y)
        box = self._normalized_box()
        if box is None or box[2] - box[0] < MIN_SELECTION or box[3] - box[1] < MIN_SELECTION:
            self._cancel()
            return
        assert self._screen is not None
        cropped = self._screen.crop(box)
        self.result = Selection(box=box, image=cropped)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._mask is not None:
            try:
                self._mask.grab_release()
            except tk.TclError:
                pass
            self._mask.destroy()
            self._mask = None

    def _teardown(self) -> None:
        for win in (self._live, self._mask):
            if win is not None:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
        self._live = None
        self._live_canvas = None
        self._mask = None
        self._canvas = None
        self._photo = None
        # 尽快释放整屏截图内存
        self._screen = None

    # ---------- 绘制 ----------

    def _normalized_box(self) -> tuple[int, int, int, int] | None:
        if self._start is None or self._current is None:
            return None
        x1 = max(0, min(self._start[0], self._current[0]))
        y1 = max(0, min(self._start[1], self._current[1]))
        x2 = min(self._vw, max(self._start[0], self._current[0]))
        y2 = min(self._vh, max(self._start[1], self._current[1]))
        return x1, y1, x2, y2

    def _update_visuals(self) -> None:
        box = self._normalized_box()
        if box is None or self._canvas is None:
            return
        x1, y1, x2, y2 = box

        if self._rect is None:
            self._rect = self._canvas.create_rectangle(
                x1, y1, x2, y2, outline="#4ea1ff", width=2
            )
        else:
            self._canvas.coords(self._rect, x1, y1, x2, y2)

        label = f"{x2 - x1} × {y2 - y1}"
        hint_x = min(max(x2 + 12, 12), self._vw - 140)
        hint_y = y1 - 26 if y1 > 30 else y2 + 8
        if self._hint is None:
            self._hint = self._canvas.create_text(
                hint_x,
                hint_y,
                text=label,
                fill="#ffe066",
                anchor="nw",
                font=("Segoe UI", 13, "bold"),
            )
        else:
            self._canvas.coords(self._hint, hint_x, hint_y)
            self._canvas.itemconfigure(self._hint, text=label)

        if LIVE_PREVIEW and self._live is not None and self._live_canvas is not None:
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            # 窗口位置用屏幕坐标，canvas 坐标 = 屏幕坐标 - 选区左上角
            left = self._vx + x1
            top = self._vy + y1
            self._live.geometry(f"{w}x{h}{left:+d}{top:+d}")
            self._live_canvas.coords(self._live_item, self._vx - left, self._vy - top)
            self._live.lift()


def select_region(root: tk.Misc) -> Selection | None:
    """便捷函数：弹出框选，返回结果或 None。"""
    return RegionSelector(root).select()
