"""结果悬浮小窗。

无边框、置顶、可拖动。翻译在上、解释在下，内容超出时可滚动。
Esc 关闭（窗口内绑定 + 主程序里的全局兜底，双保险）。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from typing import Any

from capture import virtual_screen_rect

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

BG = "#ffffff"
BAR_BG = "#f2f4f7"
BORDER = "#c9ced6"
FG = "#1f2328"
MUTED = "#6b7280"
BLUE = "#1a56db"
ORANGE = "#b45309"
RED = "#c0392b"
ACCENT = "#4ea1ff"

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def _round_corners(win: tk.Toplevel) -> None:
    """Win11 圆角；老系统或不支持时静默跳过。"""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


class ResultPopup:
    """复用同一个 Toplevel，避免每次框选都新建窗口。"""

    def __init__(self, root: tk.Misc, cfg: dict[str, Any]) -> None:
        self.root = root
        self.cfg = cfg
        self._translation = ""
        self._explanation = ""
        self._drag_origin: tuple[int, int] | None = None
        self._win_origin: tuple[int, int] | None = None
        self._built = False
        self.win: tk.Toplevel | None = None
        self.text: tk.Text | None = None
        self.status: tk.Label | None = None
        self._copy_tr: tk.Label | None = None
        self._copy_ex: tk.Label | None = None

    # ---------- 状态 ----------

    @property
    def visible(self) -> bool:
        if not self._built or self.win is None:
            return False
        try:
            return bool(self.win.winfo_exists()) and self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    # ---------- 构建 ----------

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", float(self.cfg.get("popup_opacity", 0.96)))
        win.configure(bg=BORDER)

        outer = tk.Frame(win, bg=BORDER)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)

        bar = tk.Frame(body, bg=BAR_BG, height=36)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        title = tk.Label(
            bar, text="屏幕翻译", bg=BAR_BG, fg="#3d4450",
            font=(FONT_FAMILY, 10, "bold"),
        )
        title.pack(side="left", padx=(12, 0))

        close = tk.Label(
            bar, text="✕", bg=BAR_BG, fg=MUTED,
            font=(FONT_FAMILY, 11), cursor="hand2", padx=10,
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())

        self._copy_ex = tk.Label(
            bar, text="复制解释", bg=BAR_BG, fg=MUTED,
            font=(FONT_FAMILY, 9), cursor="hand2", padx=8,
        )
        self._copy_ex.pack(side="right")
        self._copy_ex.bind("<Button-1>", lambda _e: self._copy(self._explanation, "解释"))

        self._copy_tr = tk.Label(
            bar, text="复制翻译", bg=BAR_BG, fg=MUTED,
            font=(FONT_FAMILY, 9), cursor="hand2", padx=8,
        )
        self._copy_tr.pack(side="right")
        self._copy_tr.bind("<Button-1>", lambda _e: self._copy(self._translation, "翻译"))

        # 拖动整个标题栏
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        content = tk.Frame(body, bg=BG)
        content.pack(fill="both", expand=True)

        size = int(self.cfg.get("font_size", 11))
        text = tk.Text(
            content,
            wrap="word",
            bd=0,
            highlightthickness=0,
            bg=BG,
            fg=FG,
            font=(FONT_FAMILY, size),
            padx=14,
            pady=10,
            spacing1=1,
            spacing2=4,
            spacing3=6,
            cursor="arrow",
            insertwidth=0,
        )
        scroll = tk.Scrollbar(content, command=text.yview, width=11)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure("h1", foreground=BLUE, font=(FONT_FAMILY, size + 1, "bold"), spacing1=2, spacing3=6)
        text.tag_configure("h2", foreground=ORANGE, font=(FONT_FAMILY, size + 1, "bold"), spacing1=14, spacing3=6)
        text.tag_configure("body", foreground=FG, lmargin1=2, lmargin2=2, spacing2=5)
        text.tag_configure("code", font=(MONO_FAMILY, size), foreground="#24292f", background="#f6f8fa")
        text.tag_configure("meta", foreground=MUTED, font=(FONT_FAMILY, size - 2), spacing1=14)
        text.tag_configure("err", foreground=RED, font=(FONT_FAMILY, size))
        text.configure(state="disabled")

        text.bind("<Escape>", lambda _e: self.close())
        win.bind("<Escape>", lambda _e: self.close())

        self.win = win
        self.text = text
        self.status = tk.Label(
            body, text="", bg=BG, fg=MUTED, anchor="w",
            font=(FONT_FAMILY, size - 2), padx=14, pady=4,
        )
        self.status.pack(fill="x")
        self._built = True

    # ---------- 对外接口 ----------

    def show_loading(self, anchor: tuple[int, int, int, int] | None = None) -> None:
        if not self._built:
            self._build()
        assert self.win is not None
        self._translation = ""
        self._explanation = ""
        self._render([("正在识别屏幕内容…\n", "body"), ("请稍候。", "meta")])
        self._set_buttons(False)
        self._set_status("")
        self._place(anchor)
        self._show()

    def show_error(self, message: str, anchor: tuple[int, int, int, int] | None = None) -> None:
        if not self._built:
            self._build()
        assert self.win is not None
        self._translation = ""
        self._explanation = ""
        self._render([("出错了\n", "h1"), (message, "err")])
        self._set_buttons(False)
        self._set_status("")
        if not self.visible:
            self._place(anchor)
        self._show()

    def show_result(
        self,
        translation: str,
        explanation: str,
        anchor: tuple[int, int, int, int] | None = None,
        meta: str = "",
    ) -> None:
        if not self._built:
            self._build()
        assert self.win is not None
        self._translation = (translation or "").strip()
        self._explanation = (explanation or "").strip()

        parts: list[tuple[str, str]] = []
        if self._translation:
            parts.append(("翻译\n", "h1"))
            parts.append((self._translation + "\n", "body"))
        else:
            parts.append(("没有识别到可翻译的文本\n", "h1"))

        if self._explanation:
            parts.append(("\n解释\n", "h2"))
            parts.append((self._explanation + "\n", "body"))

        if meta:
            parts.append(("\n" + meta, "meta"))

        self._render(parts)
        self._set_buttons(bool(self._translation), bool(self._explanation))
        self._set_status(meta)
        if not self.visible:
            self._place(anchor)
        self._show()

    def close(self) -> None:
        if self._built and self.win is not None:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass

    # ---------- 内部 ----------

    def _show(self) -> None:
        assert self.win is not None
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        if self.cfg.get("popup_steal_focus", True):
            self.win.focus_force()
        _round_corners(self.win)

    def _render(self, parts: list[tuple[str, str]]) -> None:
        assert self.text is not None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for content, tag in parts:
            self.text.insert("end", content, tag)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)

    def _set_buttons(self, copy_tr: bool, copy_ex: bool = False) -> None:
        for widget, enabled in ((self._copy_tr, copy_tr), (self._copy_ex, copy_ex)):
            if widget is None:
                continue
            widget.configure(fg=MUTED if enabled else "#c4c8ce", cursor="hand2" if enabled else "arrow")

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.configure(text=text)

    def _copy(self, content: str, label: str) -> None:
        if not content:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update()  # 让 Tk 真正把内容写进系统剪贴板
        except tk.TclError:
            return
        self._set_status(f"已复制{label}到剪贴板")

    def _place(self, anchor: tuple[int, int, int, int] | None) -> None:
        assert self.win is not None
        width = int(self.cfg.get("popup_width", 560))
        height = int(self.cfg.get("popup_height", 420))
        vx, vy, vw, vh = virtual_screen_rect()

        if anchor is not None:
            ax1, ay1, ax2, ay2 = anchor
            x = ax2 + 14
            y = ay1
            if x + width > vx + vw:
                x = ax1 - width - 14
            if x < vx:
                x = max(vx, vx + vw - width - 8)
            if y + height > vy + vh:
                y = max(vy, vy + vh - height - 8)
            if y < vy:
                y = vy + 8
        else:
            px, py = self.win.winfo_pointerxy()
            x = min(max(vx + 8, px + 16), vx + vw - width - 8)
            y = min(max(vy + 8, py + 16), vy + vh - height - 8)

        self.win.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _drag_start(self, event: tk.Event) -> None:
        if self.win is None:
            return
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        if self.win is None or self._drag_origin is None or self._win_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x = self._win_origin[0] + dx
        y = self._win_origin[1] + dy
        self.win.geometry(f"+{x}+{y}")
