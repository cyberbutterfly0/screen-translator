"""结果悬浮小窗。

无边框、置顶、可拖动。翻译在上、解释在下，内容超出可滚动，Esc 关闭。
配色跟随主题，图标用 lucide。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from typing import Any

import icons
import widgets as w
from capture import virtual_screen_rect
from theme import Theme

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

    def __init__(self, root: tk.Misc, cfg: dict[str, Any], theme: Theme) -> None:
        self.root = root
        self.cfg = cfg
        self.theme = theme
        self._translation = ""
        self._explanation = ""
        self._drag_origin: tuple[int, int] | None = None
        self._win_origin: tuple[int, int] | None = None
        self._built = False
        self.win: tk.Toplevel | None = None
        self.text: tk.Text | None = None
        self.status: tk.Label | None = None
        self._copy_tr: w.IconButton | None = None
        self._copy_ex: w.IconButton | None = None

    # ---------- 状态 ----------

    @property
    def visible(self) -> bool:
        if not self._built or self.win is None:
            return False
        try:
            return bool(self.win.winfo_exists()) and self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    # ---------- 构建 / 换肤 ----------

    def _ensure_window(self) -> None:
        if self.win is not None:
            return
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", float(self.cfg.get("popup_opacity", 0.97)))
        win.bind("<Escape>", lambda _e: self.close())
        self.win = win
        self._built = True

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        if self.win is not None:
            self._build_body()
            if self.visible:
                self._show()

    def _build_body(self) -> None:
        assert self.win is not None
        theme = self.theme
        self.win.configure(bg=theme.border_l2)
        for child in self.win.winfo_children():
            child.destroy()

        outer = tk.Frame(self.win, bg=theme.border_l2)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(outer, bg=theme.bg_overlay)
        body.pack(fill="both", expand=True)

        # —— 标题栏 ——
        bar = tk.Frame(body, bg=theme.bg_overlay, height=38)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        logo = icons.icon("languages", 16, theme.label_primary)
        title_holder = tk.Frame(bar, bg=theme.bg_overlay)
        title_holder.pack(side="left", padx=(12, 0))
        if logo is not None:
            logo_label = tk.Label(title_holder, image=logo, bg=theme.bg_overlay, bd=0)
            logo_label.image = logo  # type: ignore[attr-defined]
            logo_label.pack(side="left", padx=(0, 7))
        title = tk.Label(
            title_holder,
            text="屏幕翻译",
            bg=theme.bg_overlay,
            fg=theme.label_primary,
            font=(w.FONT_FAMILY, w.SIZE_BODY, "bold"),
        )
        title.pack(side="left")

        close_btn = w.IconButton(
            bar, theme, "x", command=self.close, size=15, padding=5, tooltip="关闭"
        )
        close_btn.pack(side="right", padx=(0, 6))

        self._copy_ex = w.IconButton(
            bar, theme, "copy", command=lambda: self._copy(self._explanation, "解释"),
            size=15, padding=5, tooltip="复制解释",
        )
        self._copy_ex.pack(side="right")

        self._copy_tr = w.IconButton(
            bar, theme, "copy", command=lambda: self._copy(self._translation, "翻译"),
            size=15, padding=5, tooltip="复制翻译",
        )
        self._copy_tr.pack(side="right")

        # 拖动整条标题栏
        for widget in (bar, title_holder, title):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        tk.Frame(body, height=1, bg=theme.border_l1).pack(fill="x")

        # —— 内容 ——
        content = tk.Frame(body, bg=theme.bg_overlay)
        content.pack(fill="both", expand=True)

        size = int(self.cfg.get("font_size", 11))
        self.text = tk.Text(
            content,
            wrap="word",
            bd=0,
            highlightthickness=0,
            bg=theme.bg_overlay,
            fg=theme.label_primary,
            font=(w.FONT_FAMILY, size),
            padx=16,
            pady=12,
            spacing2=4,
            spacing3=8,
            cursor="arrow",
            insertwidth=0,
        )
        # 宽度和配色都调过：原来 width=10 且沿用 Tk 默认灰，在深色底上几乎看不出能拖。
        # 槽用浮层底色（视觉上"隐形"），滑块用强边框色，悬停再提亮一档。
        scroll = tk.Scrollbar(
            content,
            command=self.text.yview,
            width=14,
            troughcolor=theme.bg_overlay,
            background=theme.label_tertiary,
            activebackground=theme.label_secondary,
            borderwidth=0,
            relief="flat",
            highlightthickness=0,
        )
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self.text.tag_configure(
            "h1", foreground=theme.label_secondary,
            font=(w.FONT_FAMILY, size - 1, "bold"), spacing3=6,
        )
        self.text.tag_configure(
            "h2", foreground=theme.label_secondary,
            font=(w.FONT_FAMILY, size - 1, "bold"), spacing1=14, spacing3=6,
        )
        self.text.tag_configure("body", foreground=theme.label_primary)
        self.text.tag_configure("err", foreground=theme.error)
        self.text.tag_configure(
            "meta", foreground=theme.label_tertiary, font=(w.FONT_FAMILY, size - 2), spacing1=14
        )
        self.text.configure(state="disabled")
        self.text.bind("<Escape>", lambda _e: self.close())

        self.status = tk.Label(
            body,
            text="",
            bg=theme.bg_overlay,
            fg=theme.label_tertiary,
            anchor="w",
            font=(w.FONT_FAMILY, size - 2),
            padx=16,
            pady=4,
        )
        self.status.pack(fill="x")

        self._render_current()

    # ---------- 对外接口 ----------

    def show_loading(self, anchor: tuple[int, int, int, int] | None = None) -> None:
        self._ensure_window()
        self._build_body()
        self._translation = ""
        self._explanation = ""
        self._render([("正在识别屏幕内容…", "body"), ("\n请稍候。", "meta")])
        self._set_status("")
        self._place(anchor)
        self._show()

    def show_error(self, message: str, anchor: tuple[int, int, int, int] | None = None) -> None:
        self._ensure_window()
        self._build_body()
        self._translation = ""
        self._explanation = ""
        self._render([("出错了", "h1"), ("\n" + message, "err")])
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
        self._ensure_window()
        self._build_body()
        self._translation = (translation or "").strip()
        self._explanation = (explanation or "").strip()
        self._render_current(meta)
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

    def _render_current(self, meta: str = "") -> None:
        if self.text is None:
            return
        parts: list[tuple[str, str]] = []
        if self._translation:
            parts.append(("翻译", "h1"))
            parts.append(("\n" + self._translation, "body"))
        elif self._explanation:
            parts.append(("没有识别到可翻译的文本", "h1"))
        if self._explanation:
            parts.append(("\n\n解释", "h2"))
            parts.append(("\n" + self._explanation, "body"))
        if meta:
            parts.append(("\n\n" + meta, "meta"))
        self._render(parts)

    def _render(self, parts: list[tuple[str, str]]) -> None:
        if self.text is None:
            return
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for content, tag in parts:
            self.text.insert("end", content, tag)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)

    def _show(self) -> None:
        assert self.win is not None
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        if self.cfg.get("popup_steal_focus", True):
            self.win.focus_force()
        _round_corners(self.win)

    def _set_status(self, text: str) -> None:
        if self.status is not None:
            self.status.configure(text=text)

    def _copy(self, content: str, label: str) -> None:
        if not content:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update()  # 让 Tk 真正写进系统剪贴板
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
        self.win.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")
