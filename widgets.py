"""DeepSeek Harness 风格的自绘控件。

ttk 的原生控件在 Windows 上带系统外观（渐变、圆角、蓝色高亮），跟 DSH 那种
扁平、无彩色、细边框的风格差太远，所以这几个基础控件用 Frame + Label + Canvas 自己拼。

限制说明：tkinter 画不出圆角，所以这里统一用直角 + 1px 细边框来贴近原风格。
主题切换采用"整体重建界面"的方式，控件不在运行时换肤。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Iterable, Sequence

import icons
from theme import Theme

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

SIZE_BODY = 10
SIZE_SMALL = 9
SIZE_TITLE = 11


def _bind_recursive(widget: tk.Misc, sequence: str, callback: Callable[[tk.Event], Any]) -> None:
    widget.bind(sequence, callback)
    for child in widget.winfo_children():
        _bind_recursive(child, sequence, callback)


class Button(tk.Frame):
    """主按钮。

    variant:
      - ``primary`` 品牌色底（浅色主题下是黑底白字）
      - ``ghost``   带边框的次要按钮
      - ``quiet``   无边框纯文字按钮
      - ``danger``  错误色底
    """

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        text: str = "",
        *,
        icon_name: str | None = None,
        command: Callable[[], None] | None = None,
        variant: str = "ghost",
        icon_size: int = 15,
        padx: int = 12,
        pady: int = 6,
        font_size: int = SIZE_BODY,
        width: int | None = None,
    ) -> None:
        self.theme = theme
        self.variant = variant
        self._command = command
        self._enabled = True
        self._hover = False

        bg, fg, border = self._palette(theme, variant)
        super().__init__(
            master,
            bg=bg,
            bd=0,
            highlightthickness=1 if border else 0,
            highlightbackground=border or bg,
            highlightcolor=border or bg,
            cursor="hand2",
        )
        self._bg, self._fg, self._border = bg, fg, border
        self._icon_name = icon_name

        inner = tk.Frame(self, bg=bg)
        inner.pack(padx=padx, pady=pady)

        self._icon_label: tk.Label | None = None
        if icon_name:
            photo = icons.icon(icon_name, icon_size, fg)
            if photo is not None:
                self._icon_label = tk.Label(inner, image=photo, bg=bg, bd=0)
                self._icon_label.image = photo  # type: ignore[attr-defined]
                self._icon_label.pack(side="left", padx=(0, 6) if text else 0)

        self._text_label: tk.Label | None = None
        if text:
            self._text_label = tk.Label(
                inner,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_FAMILY, font_size),
                bd=0,
            )
            self._text_label.pack(side="left")

        if width:
            self.configure(width=width)
            self.pack_propagate(False)

        _bind_recursive(self, "<Enter>", self._on_enter)
        _bind_recursive(self, "<Leave>", self._on_leave)
        _bind_recursive(self, "<Button-1>", self._on_press)
        _bind_recursive(self, "<ButtonRelease-1>", self._on_release)

    @staticmethod
    def _palette(theme: Theme, variant: str) -> tuple[str, str, str]:
        if variant == "primary":
            return theme.brand, theme.on_brand, ""
        if variant == "danger":
            return theme.error, "#ffffff", ""
        if variant == "quiet":
            return theme.bg_base, theme.label_primary, ""
        return theme.bg_layer1, theme.label_primary, theme.border_l2

    # ---------- 状态 ----------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        fg = self._fg if enabled else self.theme.label_tertiary
        if self._text_label is not None:
            self._text_label.configure(fg=fg)
        if self._icon_label is not None:
            name = self._icon_name
            if name:
                photo = icons.icon(name, self._icon_label.winfo_reqwidth() or 15, fg)
                if photo is not None:
                    self._icon_label.configure(image=photo)
                    self._icon_label.image = photo  # type: ignore[attr-defined]

    def set_text(self, text: str) -> None:
        if self._text_label is not None:
            self._text_label.configure(text=text)

    def _paint(self, bg: str) -> None:
        self.configure(bg=bg)
        for child in self.winfo_children():
            child.configure(bg=bg)
            for grandchild in child.winfo_children():
                grandchild.configure(bg=bg)

    def _on_enter(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._hover = True
        if self.variant == "primary":
            self._paint(self.theme.brand_hover)
        else:
            self._paint(self.theme.bg_hover)

    def _on_leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._paint(self._bg)

    def _on_press(self, _event: tk.Event) -> None:
        if self._enabled:
            self._paint(self.theme.bg_selected)

    def _on_release(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._paint(self.theme.brand_hover if self.variant == "primary" else self.theme.bg_hover)
        if self._command is not None:
            self._command()


class IconButton(tk.Frame):
    """纯图标方形按钮，用于标题栏的复制 / 关闭之类。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        icon_name: str,
        *,
        command: Callable[[], None] | None = None,
        size: int = 16,
        padding: int = 6,
        tooltip: str = "",
    ) -> None:
        self.theme = theme
        self._command = command
        self._icon_name = icon_name
        self._size = size

        super().__init__(master, bg=theme.bg_base, bd=0, cursor="hand2")
        self._label = tk.Label(self, bg=theme.bg_base, bd=0)
        self._label.pack(padx=padding, pady=padding)
        self._paint_icon(theme.label_secondary)
        self.tooltip = tooltip

        _bind_recursive(self, "<Enter>", self._on_enter)
        _bind_recursive(self, "<Leave>", self._on_leave)
        _bind_recursive(self, "<Button-1>", self._on_click)

    def _paint_icon(self, color: str) -> None:
        photo = icons.icon(self._icon_name, self._size, color)
        if photo is not None:
            self._label.configure(image=photo)
            self._label.image = photo  # type: ignore[attr-defined]

    def _paint_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._label.configure(bg=color)

    def _on_enter(self, _event: tk.Event) -> None:
        self._paint_bg(self.theme.bg_hover)
        self._paint_icon(self.theme.label_primary)

    def _on_leave(self, _event: tk.Event) -> None:
        self._paint_bg(self.theme.bg_base)
        self._paint_icon(self.theme.label_secondary)

    def _on_click(self, _event: tk.Event) -> None:
        if self._command is not None:
            self._command()


class Input(tk.Frame):
    """带细边框的文本输入框。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        *,
        textvariable: tk.StringVar | None = None,
        show: str = "",
        width: int = 30,
        font_size: int = SIZE_BODY,
    ) -> None:
        self.theme = theme
        super().__init__(
            master,
            bg=theme.bg_layer2,
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.border_l2,
            highlightcolor=theme.brand,
        )
        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            show=show,
            width=width,
            relief="flat",
            bd=0,
            bg=theme.bg_layer2,
            fg=theme.label_primary,
            insertbackground=theme.label_primary,
            font=(FONT_FAMILY, font_size),
            highlightthickness=0,
        )
        self.entry.pack(fill="x", padx=8, pady=6)
        self.entry.bind("<FocusIn>", lambda _e: self.configure(highlightbackground=theme.brand))
        self.entry.bind("<FocusOut>", lambda _e: self.configure(highlightbackground=theme.border_l2))

    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, value)


class Checkbox(tk.Frame):
    """自绘复选框：Canvas 画方框，选中时用图标画勾。"""

    BOX = 16

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        text: str,
        *,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        font_size: int = SIZE_BODY,
    ) -> None:
        self.theme = theme
        self.variable = variable
        self._command = command

        super().__init__(master, bg=theme.bg_base, cursor="hand2")
        self.canvas = tk.Canvas(
            self,
            width=self.BOX,
            height=self.BOX,
            bg=theme.bg_base,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(side="left", pady=1)
        self.label = tk.Label(
            self,
            text=text,
            bg=theme.bg_base,
            fg=theme.label_primary,
            font=(FONT_FAMILY, font_size),
        )
        self.label.pack(side="left", padx=(8, 0))

        self._render()
        for widget in (self, self.canvas, self.label):
            widget.bind("<Button-1>", self._toggle)
        self.canvas.bind("<Enter>", lambda _e: self._hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._hover(False))
        self.label.bind("<Enter>", lambda _e: self._hover(True))
        self.label.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, active: bool) -> None:
        color = self.theme.brand if active or self.variable.get() else self.theme.label_tertiary
        self.canvas.itemconfigure(self._border, outline=color)

    def _render(self) -> None:
        theme = self.theme
        checked = bool(self.variable.get())
        self.canvas.delete("all")
        self._border = self.canvas.create_rectangle(
            1,
            1,
            self.BOX - 1,
            self.BOX - 1,
            outline=theme.brand if checked else theme.label_tertiary,
            fill=theme.brand if checked else theme.bg_base,
            width=1,
        )
        if checked:
            photo = icons.icon("check", self.BOX - 6, theme.on_brand)
            if photo is not None:
                self._check_image = photo
                self.canvas.create_image(self.BOX / 2, self.BOX / 2, image=photo)

    def _toggle(self, _event: tk.Event) -> None:
        self.variable.set(not self.variable.get())
        self._render()
        if self._command is not None:
            self._command()


class Segmented(tk.Frame):
    """分段控件，用来在少量互斥选项间切换（例如外观模式）。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        options: Sequence[tuple[str, str, str | None]],
        *,
        value: str,
        on_change: Callable[[str], None],
        icon_size: int = 14,
    ) -> None:
        self.theme = theme
        self._value = value
        self._on_change = on_change
        # 轨道用悬停底色，选中项是盖在上面的抬升表面 ——
        # 这是 DSH SegmentedControl 的做法：轨道是"地方"，指示器才是"按钮"
        super().__init__(master, bg=theme.bg_hover, bd=0, highlightthickness=0)
        self._cells: dict[str, tuple[tk.Frame, tk.Label, tk.Label | None, str | None]] = {}

        track = tk.Frame(self, bg=theme.bg_hover)
        track.pack(padx=3, pady=3)
        last = len(options) - 1

        for index, (key, label, icon_name) in enumerate(options):
            cell = tk.Frame(track, bg=theme.bg_hover, cursor="hand2")
            cell.pack(side="left", padx=(0, 2 if index != last else 0))
            inner = tk.Frame(cell, bg=theme.bg_hover)
            inner.pack(padx=12, pady=4)

            icon_label = None
            if icon_name:
                photo = icons.icon(icon_name, icon_size, theme.label_secondary)
                if photo is not None:
                    icon_label = tk.Label(inner, image=photo, bg=theme.bg_hover, bd=0)
                    icon_label.image = photo  # type: ignore[attr-defined]
                    icon_label.pack(side="left", padx=(0, 5))

            text_label = tk.Label(
                inner,
                text=label,
                bg=theme.bg_hover,
                fg=theme.label_secondary,
                font=(FONT_FAMILY, SIZE_BODY),
            )
            text_label.pack(side="left")

            self._cells[key] = (cell, text_label, icon_label, icon_name)
            for widget in (cell, inner, text_label) + ((icon_label,) if icon_label else ()):
                widget.bind("<Button-1>", lambda _e, k=key: self._select(k))

        self._repaint()

    def _select(self, key: str) -> None:
        if key == self._value:
            return
        self._value = key
        self._repaint()
        self._on_change(key)

    def _repaint(self) -> None:
        for key, (cell, text_label, icon_label, icon_name) in self._cells.items():
            selected = key == self._value
            bg = self.theme.bg_layer1 if selected else self.theme.bg_hover
            fg = self.theme.label_primary if selected else self.theme.label_secondary
            cell.configure(bg=bg)
            for child in cell.winfo_children():
                child.configure(bg=bg)
            text_label.configure(bg=bg, fg=fg)
            if icon_label is not None and icon_name:
                photo = icons.icon(icon_name, 14, fg)
                if photo is not None:
                    icon_label.configure(image=photo, bg=bg)
                    icon_label.image = photo  # type: ignore[attr-defined]

    @property
    def value(self) -> str:
        return self._value


class Tabs(tk.Frame):
    """顶部标签栏：选中项加深文字，底部一条品牌色指示线。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        tabs: Sequence[tuple[str, str, str | None]],
        *,
        value: str,
        on_change: Callable[[str], None],
        icon_size: int = 15,
    ) -> None:
        self.theme = theme
        self._value = value
        self._on_change = on_change
        super().__init__(master, bg=theme.bg_base)
        self._tabs: dict[str, tuple[tk.Label, tk.Frame, tk.Label | None, str | None]] = {}

        row = tk.Frame(self, bg=theme.bg_base)
        row.pack(fill="x")
        for key, label, icon_name in tabs:
            holder = tk.Frame(row, bg=theme.bg_base, cursor="hand2")
            holder.pack(side="left", padx=(0, 4))
            inner = tk.Frame(holder, bg=theme.bg_base)
            inner.pack(padx=12, pady=(8, 7))

            icon_label = None
            if icon_name:
                photo = icons.icon(icon_name, icon_size, theme.label_secondary)
                if photo is not None:
                    icon_label = tk.Label(inner, image=photo, bg=theme.bg_base, bd=0)
                    icon_label.image = photo  # type: ignore[attr-defined]
                    icon_label.pack(side="left", padx=(0, 6))

            text_label = tk.Label(
                inner,
                text=label,
                bg=theme.bg_base,
                fg=theme.label_secondary,
                font=(FONT_FAMILY, SIZE_BODY),
            )
            text_label.pack(side="left")

            underline = tk.Frame(holder, height=2, bg=theme.bg_base)
            underline.pack(fill="x")

            self._tabs[key] = (text_label, underline, icon_label, icon_name)
            for widget in (holder, inner, text_label) + ((icon_label,) if icon_label else ()):
                widget.bind("<Button-1>", lambda _e, k=key: self._select(k))

        tk.Frame(self, height=1, bg=theme.border_l1).pack(fill="x")
        self._repaint()

    def _select(self, key: str) -> None:
        if key == self._value:
            return
        self._value = key
        self._repaint()
        self._on_change(key)

    def _repaint(self) -> None:
        for key, (text_label, underline, icon_label, icon_name) in self._tabs.items():
            selected = key == self._value
            fg = self.theme.label_primary if selected else self.theme.label_secondary
            text_label.configure(fg=fg)
            underline.configure(bg=self.theme.brand if selected else self.theme.bg_base)
            if icon_label is not None and icon_name:
                photo = icons.icon(icon_name, 15, fg)
                if photo is not None:
                    icon_label.configure(image=photo)
                    icon_label.image = photo  # type: ignore[attr-defined]

    @property
    def value(self) -> str:
        return self._value


class SectionTitle(tk.Frame):
    """带图标的区块小标题。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        text: str,
        *,
        icon_name: str | None = None,
        icon_size: int = 14,
    ) -> None:
        super().__init__(master, bg=theme.bg_base)
        if icon_name:
            photo = icons.icon(icon_name, icon_size, theme.label_secondary)
            if photo is not None:
                label = tk.Label(self, image=photo, bg=theme.bg_base, bd=0)
                label.image = photo  # type: ignore[attr-defined]
                label.pack(side="left", padx=(0, 6))
        tk.Label(
            self,
            text=text,
            bg=theme.bg_base,
            fg=theme.label_secondary,
            font=(FONT_FAMILY, SIZE_SMALL, "bold"),
        ).pack(side="left")


def separator(master: tk.Misc, theme: Theme) -> tk.Frame:
    line = tk.Frame(master, height=1, bg=theme.border_l1)
    line.pack(fill="x")
    return line
