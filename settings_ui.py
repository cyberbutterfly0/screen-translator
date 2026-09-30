"""主窗口：设置与历史。

外观走 DeepSeek Harness 那套：极简、无彩色强调、细边框、紧凑间距。
标题栏保留系统原生的（保住任务栏和 Alt+Tab 行为），但会通过 DWM 把标题栏
染成和主题一致的颜色，避免浅色标题栏配深色内容区。
"""

from __future__ import annotations

import ctypes
import threading
import tkinter as tk
from typing import Any, Callable

import api_client
import config as cfgmod
import icons
import theme as theme_mod
import widgets as w
from theme import MODE_ICONS, MODE_LABELS, MODES, Theme

MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
    "Scroll_Lock", "ISO_Level3_Shift",
}

MASK_SHIFT = 0x0001
MASK_CTRL = 0x0004
MASK_ALT = 0x20000

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19


def apply_dark_titlebar(win: tk.Misc, dark: bool) -> None:
    """让 Windows 原生标题栏跟随应用主题。"""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        pass


def build_hotkey_string(event: tk.Event) -> str | None:
    """把一次按键事件翻译成 keyboard 库认识的热键字符串。"""
    keysym = event.keysym
    if keysym in MODIFIER_KEYSYMS:
        return None

    parts: list[str] = []
    state = int(event.state)
    if state & MASK_CTRL:
        parts.append("ctrl")
    if state & MASK_ALT:
        parts.append("alt")
    if state & MASK_SHIFT:
        parts.append("shift")

    if keysym.startswith("F") and keysym[1:].isdigit():
        key = keysym.lower()
    elif len(keysym) == 1:
        key = keysym.lower()
    elif not parts and event.char and event.char.strip():
        key = event.char.lower()
    else:
        key = keysym.lower()

    parts.append(key)
    if len(parts) == 1:
        return None  # 至少要有修饰键，避免裸键抢全局输入
    return "+".join(parts)


class HotkeyRecorder(tk.Frame):
    """显示当前快捷键，点一下进入录制状态，按下组合键即生效。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        value: str,
        *,
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
    ) -> None:
        self.theme = theme
        self.value = value
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._recording = False
        self._bind_id: str | None = None

        super().__init__(
            master,
            bg=theme.bg_layer2,
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.border_l2,
            cursor="hand2",
        )
        self.root = self.winfo_toplevel()
        self._inner = tk.Frame(self, bg=theme.bg_layer2)
        self._inner.pack(padx=10, pady=6)

        photo = icons.icon("keyboard", 14, theme.label_secondary)
        self._icon = tk.Label(self._inner, bg=theme.bg_layer2, bd=0)
        if photo is not None:
            self._icon.configure(image=photo)
            self._icon.image = photo  # type: ignore[attr-defined]
        self._icon.pack(side="left", padx=(0, 6))

        self._text = tk.Label(
            self._inner,
            text=value,
            bg=theme.bg_layer2,
            fg=theme.label_primary,
            font=(w.MONO_FAMILY, w.SIZE_BODY),
        )
        self._text.pack(side="left")

        for widget in (self, self._inner, self._icon, self._text):
            widget.bind("<Button-1>", lambda _e: self.start_record())

    # ---------- 录制 ----------

    def _paint(self, bg: str, border: str, fg: str | None = None) -> None:
        self.configure(bg=bg, highlightbackground=border)
        for widget in (self._inner, self._icon, self._text):
            widget.configure(bg=bg)
        if fg:
            self._text.configure(fg=fg)

    def start_record(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._on_pause()
        self._paint(self.theme.bg_selected, self.theme.brand)
        self._text.configure(text="请按组合键…", fg=self.theme.label_secondary)
        self._bind_id = self.root.bind("<KeyPress>", self._on_key, add="+")

    def stop_record(self, *, cancelled: bool = False) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._bind_id is not None:
            try:
                self.root.unbind("<KeyPress>", self._bind_id)
            except tk.TclError:
                pass
            self._bind_id = None
        self._paint(self.theme.bg_layer2, self.theme.border_l2)
        self._text.configure(text=self.value, fg=self.theme.label_primary)
        self._on_resume()

    def _on_key(self, event: tk.Event) -> str:
        if not self._recording:
            return ""
        if event.keysym == "Escape":
            self.stop_record(cancelled=True)
            return "break"
        hotkey = build_hotkey_string(event)
        if hotkey is None:
            return "break"  # 只按了修饰键，继续等主键
        self.value = hotkey
        self.stop_record()
        return "break"


class MainWindow:
    """主窗口用独立 Toplevel，root 本身永远隐藏，只当事件循环宿主。"""

    def __init__(
        self,
        root: tk.Tk,
        cfg: dict[str, Any],
        theme: Theme,
        *,
        on_save: Callable[[dict[str, Any]], None],
        on_quit: Callable[[], None],
        call_soon: Callable[[Callable[[], None]], None],
        on_hotkey_pause: Callable[[], None],
        on_hotkey_resume: Callable[[], None],
        on_theme_change: Callable[[str], None],
    ) -> None:
        self.root = root
        self.cfg = cfg
        self.theme = theme
        self._on_save = on_save
        self._on_quit = on_quit
        self._call_soon = call_soon
        self._on_hotkey_pause = on_hotkey_pause
        self._on_hotkey_resume = on_hotkey_resume
        self._on_theme_change = on_theme_change
        self._recorder: HotkeyRecorder | None = None
        self._tab = "settings"
        self._hist_items: list[dict[str, Any]] = []

        win = tk.Toplevel(root)
        self.win = win
        win.title(cfgmod.APP_TITLE)
        win.protocol("WM_DELETE_WINDOW", self._on_quit)
        win.bind("<Unmap>", self._on_unmap)
        win.resizable(False, False)

        self.rebuild()
        self._center()

    # ---------- 外观 ----------

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.rebuild()

    def rebuild(self) -> None:
        """主题变了就整体重建：控件颜色在创建时写死，换肤靠重画最省心。"""
        theme = self.theme
        self.win.configure(bg=theme.bg_base)
        for child in self.win.winfo_children():
            child.destroy()

        apply_dark_titlebar(self.win, theme.dark)

        self.tabs = w.Tabs(
            self.win,
            theme,
            [
                ("settings", "设置", "settings"),
                ("history", "历史", "history"),
            ],
            value=self._tab,
            on_change=self._switch_tab,
        )
        self.tabs.pack(fill="x", padx=16, pady=(12, 0))

        self._container = tk.Frame(self.win, bg=theme.bg_base)
        self._container.pack(fill="both", expand=True)

        self._settings_page = tk.Frame(self._container, bg=theme.bg_base)
        self._history_page = tk.Frame(self._container, bg=theme.bg_base)
        self._build_settings(self._settings_page)
        self._build_history(self._history_page)

        if self._tab == "settings":
            self._settings_page.pack(fill="both", expand=True)
        else:
            self._history_page.pack(fill="both", expand=True)
            self.refresh_history()

        self.status = tk.Label(
            self.win,
            text="",
            bg=theme.bg_base,
            fg=theme.label_secondary,
            font=(w.FONT_FAMILY, w.SIZE_SMALL),
            anchor="w",
        )
        self.status.pack(fill="x", padx=18, pady=(0, 10))
        self._resize()

    def _switch_tab(self, key: str) -> None:
        self._tab = key
        self._settings_page.pack_forget()
        self._history_page.pack_forget()
        if key == "settings":
            self._settings_page.pack(fill="both", expand=True)
        else:
            self._history_page.pack(fill="both", expand=True)
            self.refresh_history()
        self._resize()

    def _resize(self) -> None:
        self.win.update_idletasks()
        width = max(560, self.win.winfo_reqwidth())
        height = max(560, self.win.winfo_reqheight())
        self.win.minsize(width, height)
        x = max(0, (self.win.winfo_screenwidth() - width) // 2)
        y = max(0, (self.win.winfo_screenheight() - height) // 3)
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    # ---------- 窗口状态 ----------

    @property
    def visible(self) -> bool:
        try:
            return self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    def show(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            if self._tab == "history":
                self.refresh_history()
        except tk.TclError:
            pass

    def hide(self) -> None:
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self.win:
            return
        if not self.cfg.get("minimize_to_tray", True):
            return
        try:
            if self.win.state() == "iconic":
                self.win.after(10, self.hide)
        except tk.TclError:
            pass

    def _center(self) -> None:
        self._resize()

    # ---------- 设置页 ----------

    def _field_label(self, parent: tk.Frame, row: int, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=self.theme.bg_base,
            fg=self.theme.label_secondary,
            font=(w.FONT_FAMILY, w.SIZE_BODY),
        ).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)

    def _build_settings(self, parent: tk.Frame) -> None:
        theme = self.theme
        cfg = self.cfg
        page = tk.Frame(parent, bg=theme.bg_base)
        page.pack(fill="both", expand=True, padx=18, pady=(16, 4))

        # —— API ——
        w.SectionTitle(page, theme, "API", icon_name="plug-zap").pack(anchor="w")
        form = tk.Frame(page, bg=theme.bg_base)
        form.pack(fill="x", pady=(10, 0))
        form.columnconfigure(1, weight=1)

        self._field_label(form, 0, "API Key")
        self.var_key = tk.StringVar(value=cfg.get("api_key", ""))
        self.key_input = w.Input(form, theme, textvariable=self.var_key, show="•", width=34)
        self.key_input.grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)

        self._field_label(form, 1, "Base URL")
        self.var_base = tk.StringVar(value=cfg.get("base_url", ""))
        w.Input(form, theme, textvariable=self.var_base, width=34).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=5
        )

        self._field_label(form, 2, "模型")
        self.var_model = tk.StringVar(value=cfg.get("model", ""))
        w.Input(form, theme, textvariable=self.var_model, width=18).grid(
            row=2, column=1, sticky="ew", pady=5
        )
        timeout_box = tk.Frame(form, bg=theme.bg_base)
        timeout_box.grid(row=2, column=2, sticky="e", padx=(10, 0), pady=5)
        tk.Label(
            timeout_box, text="超时(秒)", bg=theme.bg_base, fg=theme.label_secondary,
            font=(w.FONT_FAMILY, w.SIZE_BODY),
        ).pack(side="left", padx=(0, 8))
        self.var_timeout = tk.StringVar(value=str(cfg.get("timeout", 90)))
        w.Input(timeout_box, theme, textvariable=self.var_timeout, width=5).pack(side="left")

        test_row = tk.Frame(page, bg=theme.bg_base)
        test_row.pack(fill="x", pady=(12, 0))
        self.test_btn = w.Button(
            test_row, theme, "测试连接", icon_name="plug-zap", command=self._test_connection
        )
        self.test_btn.pack(side="left")
        self.test_status = tk.Label(
            test_row, text="", bg=theme.bg_base, fg=theme.label_secondary,
            font=(w.FONT_FAMILY, w.SIZE_SMALL),
        )
        self.test_status.pack(side="left", padx=(10, 0))

        w.separator(page, theme)
        tk.Frame(page, height=14, bg=theme.bg_base).pack()

        # —— 交互 ——
        w.SectionTitle(page, theme, "交互", icon_name="keyboard").pack(anchor="w")
        hot_row = tk.Frame(page, bg=theme.bg_base)
        hot_row.pack(fill="x", pady=(10, 0))
        tk.Label(
            hot_row, text="截图快捷键", bg=theme.bg_base, fg=theme.label_secondary,
            font=(w.FONT_FAMILY, w.SIZE_BODY),
        ).pack(side="left", padx=(0, 14))
        self._recorder = HotkeyRecorder(
            hot_row, theme, cfg.get("hotkey", "ctrl+alt+t"),
            on_pause=self._on_hotkey_pause, on_resume=self._on_hotkey_resume,
        )
        self._recorder.pack(side="left")
        tk.Label(
            hot_row, text="点一下，然后按下组合键", bg=theme.bg_base,
            fg=theme.label_tertiary, font=(w.FONT_FAMILY, w.SIZE_SMALL),
        ).pack(side="left", padx=(10, 0))

        w.separator(page, theme)
        tk.Frame(page, height=14, bg=theme.bg_base).pack()

        # —— 输出 ——
        w.SectionTitle(page, theme, "输出", icon_name="code").pack(anchor="w")
        options = tk.Frame(page, bg=theme.bg_base)
        options.pack(fill="x", pady=(10, 0))
        self.var_explain = tk.BooleanVar(value=bool(cfg.get("show_explanation", True)))
        w.Checkbox(
            options, theme, "同时解释代码内容（低强度：整体作用 + 2-3 条关键点）",
            variable=self.var_explain,
        ).pack(anchor="w", pady=3)
        self.var_history = tk.BooleanVar(value=bool(cfg.get("history_enabled", True)))
        w.Checkbox(
            options, theme, "保存历史记录（只存文本，不存截图）", variable=self.var_history
        ).pack(anchor="w", pady=3)
        self.var_focus = tk.BooleanVar(value=bool(cfg.get("popup_steal_focus", True)))
        w.Checkbox(
            options, theme, "小窗弹出时抢占焦点（关掉后只能用 Esc 关窗）", variable=self.var_focus
        ).pack(anchor="w", pady=3)

        w.separator(page, theme)
        tk.Frame(page, height=14, bg=theme.bg_base).pack()

        # —— 外观 ——
        w.SectionTitle(page, theme, "外观", icon_name="palette").pack(anchor="w")
        appearance = tk.Frame(page, bg=theme.bg_base)
        appearance.pack(fill="x", pady=(10, 0))
        self._mode = theme_mod.normalize_mode(cfg.get("theme", "light"))
        self.mode_switch = w.Segmented(
            appearance,
            theme,
            [(mode, MODE_LABELS[mode], MODE_ICONS[mode]) for mode in MODES],
            value=self._mode,
            on_change=self._change_theme,
        )
        self.mode_switch.pack(side="left")

        # —— 操作 ——
        actions = tk.Frame(page, bg=theme.bg_base)
        actions.pack(fill="x", pady=(20, 6))
        w.Button(
            actions, theme, "保存设置", icon_name="save", variant="primary", command=self._save
        ).pack(side="left")
        w.Button(
            actions, theme, "最小化到托盘", icon_name="minimize-2", command=self.hide
        ).pack(side="left", padx=(8, 0))

    # ---------- 历史页 ----------

    def _build_history(self, parent: tk.Frame) -> None:
        theme = self.theme
        page = tk.Frame(parent, bg=theme.bg_base)
        page.pack(fill="both", expand=True, padx=18, pady=(16, 4))

        body = tk.Frame(page, bg=theme.bg_base)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=theme.bg_base)
        left.pack(side="left", fill="y")
        w.SectionTitle(left, theme, "记录", icon_name="history").pack(anchor="w")
        list_holder = tk.Frame(
            left, bg=theme.bg_layer2, highlightthickness=1, highlightbackground=theme.border_l2
        )
        list_holder.pack(fill="y", expand=True, pady=(8, 0))
        self.hist_list = tk.Listbox(
            list_holder,
            width=28,
            font=(w.FONT_FAMILY, w.SIZE_SMALL),
            bg=theme.bg_layer2,
            fg=theme.label_primary,
            selectbackground=theme.bg_selected,
            selectforeground=theme.label_primary,
            activestyle="none",
            relief="flat",
            bd=0,
            highlightthickness=0,
            exportselection=False,
        )
        self.hist_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.hist_list.bind("<<ListboxSelect>>", lambda _e: self._show_history_item())

        right = tk.Frame(body, bg=theme.bg_base)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        w.SectionTitle(right, theme, "详情", icon_name="scan-line").pack(anchor="w")
        text_holder = tk.Frame(
            right, bg=theme.bg_layer2, highlightthickness=1, highlightbackground=theme.border_l2
        )
        text_holder.pack(fill="both", expand=True, pady=(8, 0))
        self.hist_text = tk.Text(
            text_holder,
            wrap="word",
            width=1,  # 宽度交给 pack 决定；Text 默认 80 字符会把窗口撑得很宽
            font=(w.FONT_FAMILY, w.SIZE_BODY),
            bg=theme.bg_layer2,
            fg=theme.label_primary,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            state="disabled",
            cursor="arrow",
        )
        self.hist_text.pack(fill="both", expand=True)

        bottom = tk.Frame(page, bg=theme.bg_base)
        bottom.pack(fill="x", pady=(12, 0))
        w.Button(bottom, theme, "刷新", icon_name="loader-circle", command=self.refresh_history).pack(
            side="left"
        )
        w.Button(bottom, theme, "清空历史", icon_name="x", command=self._clear_history).pack(
            side="left", padx=(8, 0)
        )

    # ---------- 行为 ----------

    def _change_theme(self, mode: str) -> None:
        self._mode = mode
        self._on_theme_change(mode)

    def _collect(self) -> dict[str, Any]:
        cfg = dict(self.cfg)
        cfg["api_key"] = self.var_key.get().strip()
        cfg["base_url"] = self.var_base.get().strip() or "https://api.deepseek.com"
        cfg["model"] = self.var_model.get().strip() or "deepseek-flash"
        try:
            cfg["timeout"] = max(10, int(float(self.var_timeout.get())))
        except ValueError:
            cfg["timeout"] = 90
        cfg["hotkey"] = (self._recorder.value if self._recorder else cfg.get("hotkey")) or "ctrl+alt+t"
        cfg["show_explanation"] = bool(self.var_explain.get())
        cfg["history_enabled"] = bool(self.var_history.get())
        cfg["popup_steal_focus"] = bool(self.var_focus.get())
        cfg["theme"] = self._mode
        return cfg

    def _save(self) -> None:
        cfg = self._collect()
        if self._recorder is not None:
            self._recorder.stop_record(cancelled=False)
        self._on_save(cfg)
        cfgmod.save_config(cfg)
        self.cfg = cfg
        self.set_status("设置已保存。", self.theme.success)

    def set_status(self, text: str, color: str | None = None) -> None:
        if not hasattr(self, "status"):
            return
        self.status.configure(text=text, fg=color or self.theme.label_secondary)

    def _test_connection(self) -> None:
        payload = self._collect()
        self.test_btn.set_enabled(False)
        self.test_status.configure(text="测试中…", fg=self.theme.label_secondary)

        def work() -> None:
            try:
                message = api_client.test_connection(payload)
                ok = True
            except Exception as exc:  # noqa: BLE001 - 任何失败都要展示给用户
                message = str(exc).replace("\n", " ")
                ok = False
            self._call_soon(lambda: self._test_done(message, ok))

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, message: str, ok: bool) -> None:
        self.test_btn.set_enabled(True)
        self.test_status.configure(
            text=message[:110], fg=self.theme.success if ok else self.theme.error
        )

    def refresh_history(self) -> None:
        if not hasattr(self, "hist_list"):
            return
        self._hist_items = cfgmod.load_history()
        self.hist_list.delete(0, "end")
        for item in self._hist_items:
            when = str(item.get("time", ""))[11:19] or str(item.get("time", ""))[:19]
            preview = (str(item.get("translation", "")) or "").replace("\n", " ")[:20]
            self.hist_list.insert("end", f"{when}  {preview}")
        self.hist_text.configure(state="normal")
        self.hist_text.delete("1.0", "end")
        self.hist_text.configure(state="disabled")

    def _show_history_item(self) -> None:
        selection = self.hist_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self._hist_items):
            return
        item = self._hist_items[index]
        theme = self.theme
        self.hist_text.configure(state="normal")
        self.hist_text.delete("1.0", "end")
        self.hist_text.tag_configure("head", foreground=theme.label_secondary,
                                     font=(w.FONT_FAMILY, w.SIZE_SMALL))
        self.hist_text.tag_configure("section", foreground=theme.brand,
                                     font=(w.FONT_FAMILY, w.SIZE_BODY, "bold"))
        self.hist_text.insert("end", f"{item.get('time', '')}   {item.get('model', '')}\n", "head")
        self.hist_text.insert("end", "\n翻译\n", "section")
        self.hist_text.insert("end", str(item.get("translation", "")) + "\n")
        if item.get("explanation"):
            self.hist_text.insert("end", "\n解释\n", "section")
            self.hist_text.insert("end", str(item["explanation"]) + "\n")
        self.hist_text.configure(state="disabled")

    def _clear_history(self) -> None:
        cfgmod.save_history([], 0)
        self.refresh_history()
        self.set_status("历史已清空。")
