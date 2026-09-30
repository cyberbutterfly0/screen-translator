"""主窗口：快捷键录入、API 设置、历史记录。

关闭窗口（×）= 直接退出程序；最小化按钮 = 收进系统托盘。
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Callable

import api_client
import config as cfgmod

FONT_FAMILY = "Microsoft YaHei UI"
MUTED = "#6b7280"
OK_GREEN = "#1a7f37"
ERR_RED = "#c0392b"
REC_BG = "#fff6da"

MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
    "Scroll_Lock", "ISO_Level3_Shift",
}

# Windows 上 tkinter 的修饰键位掩码
MASK_SHIFT = 0x0001
MASK_CTRL = 0x0004
MASK_ALT = 0x20000


def _build_hotkey_string(event: tk.Event) -> str | None:
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
        # 没有修饰键时优先用真实字符，这样符号键也能录进来
        key = event.char.lower()
    else:
        key = keysym.lower()

    parts.append(key)
    if len(parts) == 1:
        return None  # 至少要有一个修饰键，避免裸键抢全局输入
    return "+".join(parts)


class HotkeyRecorder(tk.Frame):
    """点一下，然后按下想要的组合键。"""

    def __init__(
        self,
        master: tk.Misc,
        value: str,
        *,
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        **kw: Any,
    ) -> None:
        super().__init__(master, **kw)
        self.value = value
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._recording = False
        self._bind_id: str | None = None
        self.root = self.winfo_toplevel()

        self.entry = tk.Entry(
            self, width=22, font=(FONT_FAMILY, 10), justify="center",
            relief="solid", bd=1, state="readonly", readonlybackground="white",
            cursor="hand2",
        )
        self.entry.pack(side="left")
        self.entry.configure(state="normal")
        self.entry.insert(0, value)
        self.entry.configure(state="readonly")

        self.hint = tk.Label(self, text="点这里，然后按下组合键", fg=MUTED, font=(FONT_FAMILY, 9))
        self.hint.pack(side="left", padx=(8, 0))

        self.entry.bind("<Button-1>", lambda _e: self.start_record())
        self.entry.bind("<FocusIn>", lambda _e: self.start_record())

    def set_value(self, value: str) -> None:
        self.value = value
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.entry.insert(0, value)
        self.entry.configure(state="readonly")

    def start_record(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._on_pause()
        self.entry.configure(state="normal", bg=REC_BG)
        self.entry.delete(0, "end")
        self.entry.insert(0, "请按组合键…")
        self.entry.configure(readonlybackground=REC_BG)
        self.entry.configure(state="readonly")
        self.hint.configure(text="Esc 取消")
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
        self.entry.configure(readonlybackground="white")
        if cancelled:
            self.set_value(self.value)
        self.hint.configure(text="点这里，然后按下组合键")
        self._on_resume()

    def _on_key(self, event: tk.Event) -> str:
        if not self._recording:
            return ""
        if event.keysym == "Escape":
            self.stop_record(cancelled=True)
            return "break"
        hotkey = _build_hotkey_string(event)
        if hotkey is None:
            return "break"  # 只按了修饰键，继续等主键
        self.value = hotkey
        self.stop_record()
        self.set_value(hotkey)
        return "break"


class MainWindow:
    """独立 Toplevel 作为主窗口；root 本身始终隐藏，只当事件循环宿主。"""

    def __init__(
        self,
        root: tk.Tk,
        cfg: dict[str, Any],
        *,
        on_save: Callable[[dict[str, Any]], None],
        on_quit: Callable[[], None],
        call_soon: Callable[[Callable[[], None]], None],
        on_hotkey_pause: Callable[[], None],
        on_hotkey_resume: Callable[[], None],
    ) -> None:
        self.root = root
        self.cfg = cfg
        self._on_save = on_save
        self._on_quit = on_quit
        self._call_soon = call_soon
        self._recorder: HotkeyRecorder | None = None

        win = tk.Toplevel(root)
        self.win = win
        win.title(f"{cfgmod.APP_TITLE} v{cfgmod.APP_VERSION}")
        win.configure(bg="#f7f8fa")
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._on_quit)
        win.bind("<Unmap>", self._on_unmap)

        self._build(on_hotkey_pause, on_hotkey_resume)
        self._center()

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
            self.refresh_history()
        except tk.TclError:
            pass

    def hide(self) -> None:
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def _center(self) -> None:
        """按内容实际需要的尺寸居中。

        不同 DPI 缩放下字体像素高度不同，写死窗口尺寸会把内容裁掉，
        所以这里量一次自然尺寸再定位。
        """
        self.win.update_idletasks()
        w = min(self.win.winfo_reqwidth(), self.win.winfo_screenwidth() - 80)
        h = min(self.win.winfo_reqheight(), self.win.winfo_screenheight() - 120)
        self.win.minsize(w, h)
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

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

    # ---------- 构建 ----------

    def _build(
        self,
        on_hotkey_pause: Callable[[], None],
        on_hotkey_resume: Callable[[], None],
    ) -> None:
        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        notebook.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_history())

        settings = tk.Frame(notebook, bg="#f7f8fa")
        history = tk.Frame(notebook, bg="#f7f8fa")
        notebook.add(settings, text="  设置  ")
        notebook.add(history, text="  历史  ")

        self._build_settings(settings, on_hotkey_pause, on_hotkey_resume)
        self._build_history(history)

        self.status = tk.Label(
            self.win, text="", bg="#f7f8fa", fg=MUTED,
            font=(FONT_FAMILY, 9), anchor="w", padx=14,
        )
        self.status.pack(fill="x", side="bottom", pady=(0, 8))

    def _build_settings(
        self,
        parent: tk.Frame,
        on_hotkey_pause: Callable[[], None],
        on_hotkey_resume: Callable[[], None],
    ) -> None:
        cfg = self.cfg

        api_box = ttk.LabelFrame(parent, text=" API ")
        api_box.pack(fill="x", padx=2, pady=(6, 8))
        api_box.columnconfigure(1, weight=1)

        ttk.Label(api_box, text="API Key").grid(row=0, column=0, sticky="w", padx=(10, 8), pady=6)
        self.var_key = tk.StringVar(value=cfg.get("api_key", ""))
        key_row = tk.Frame(api_box, bg="#f7f8fa")
        key_row.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=6)
        key_row.columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(key_row, textvariable=self.var_key, show="*")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self._show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_row, text="显示", variable=self._show_key,
            command=lambda: self.key_entry.configure(show="" if self._show_key.get() else "*"),
        ).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(api_box, text="Base URL").grid(row=1, column=0, sticky="w", padx=(10, 8), pady=6)
        self.var_base = tk.StringVar(value=cfg.get("base_url", ""))
        ttk.Entry(api_box, textvariable=self.var_base).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=6)

        ttk.Label(api_box, text="模型").grid(row=2, column=0, sticky="w", padx=(10, 8), pady=6)
        self.var_model = tk.StringVar(value=cfg.get("model", ""))
        ttk.Entry(api_box, textvariable=self.var_model).grid(row=2, column=1, sticky="ew", pady=6)

        ttk.Label(api_box, text="超时(秒)").grid(row=3, column=0, sticky="w", padx=(10, 8), pady=6)
        self.var_timeout = tk.StringVar(value=str(cfg.get("timeout", 90)))
        ttk.Entry(api_box, textvariable=self.var_timeout, width=8).grid(row=3, column=1, sticky="w", pady=6)

        test_row = tk.Frame(api_box, bg="#f7f8fa")
        test_row.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 10))
        self.test_btn = ttk.Button(test_row, text="测试连接", command=self._test_connection)
        self.test_btn.pack(side="left")
        self.test_status = tk.Label(test_row, text="", bg="#f7f8fa", fg=MUTED, font=(FONT_FAMILY, 9))
        self.test_status.pack(side="left", padx=(10, 0))

        hot_box = ttk.LabelFrame(parent, text=" 交互 ")
        hot_box.pack(fill="x", padx=2, pady=(0, 8))

        ttk.Label(hot_box, text="截图快捷键").pack(side="left", padx=(10, 8), pady=10)
        self._recorder = HotkeyRecorder(
            hot_box, cfg.get("hotkey", "ctrl+alt+t"),
            on_pause=on_hotkey_pause, on_resume=on_hotkey_resume, bg="#f7f8fa",
        )
        self._recorder.pack(side="left")

        opt_box = ttk.LabelFrame(parent, text=" 输出 ")
        opt_box.pack(fill="x", padx=2, pady=(0, 8))

        self.var_explain = tk.BooleanVar(value=bool(cfg.get("show_explanation", True)))
        ttk.Checkbutton(
            opt_box, text="同时解释代码内容（低强度：整体作用 + 2-3 条关键点）",
            variable=self.var_explain,
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self.var_history = tk.BooleanVar(value=bool(cfg.get("history_enabled", True)))
        ttk.Checkbutton(
            opt_box, text="保存历史记录（只存文本，不存截图）",
            variable=self.var_history,
        ).pack(anchor="w", padx=10, pady=(2, 2))

        self.var_focus = tk.BooleanVar(value=bool(cfg.get("popup_steal_focus", True)))
        ttk.Checkbutton(
            opt_box, text="小窗弹出时抢占焦点（关掉后只能用 Esc 关窗）",
            variable=self.var_focus,
        ).pack(anchor="w", padx=10, pady=(2, 10))

        actions = tk.Frame(parent, bg="#f7f8fa")
        actions.pack(fill="x", padx=2, pady=(4, 0))
        ttk.Button(actions, text="保存设置", command=self._save).pack(side="left")
        ttk.Button(actions, text="最小化到托盘", command=self.hide).pack(side="left", padx=8)

    def _build_history(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg="#f7f8fa")
        top.pack(fill="both", expand=True, padx=2, pady=6)

        left = tk.Frame(top, bg="#f7f8fa")
        left.pack(side="left", fill="y")
        tk.Label(left, text="记录（只存文本）", bg="#f7f8fa", fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w")
        self.hist_list = tk.Listbox(left, width=26, font=(FONT_FAMILY, 9), activestyle="none")
        self.hist_list.pack(fill="y", expand=True)
        self.hist_list.bind("<<ListboxSelect>>", lambda _e: self._show_history_item())

        right = tk.Frame(top, bg="#f7f8fa")
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        tk.Label(right, text="详情", bg="#f7f8fa", fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w")
        self.hist_text = tk.Text(
            right, wrap="word", font=(FONT_FAMILY, 10), bd=1, relief="solid",
            bg="white", padx=8, pady=6, state="disabled",
        )
        self.hist_text.pack(fill="both", expand=True)
        self._hist_items: list[dict[str, Any]] = []

        bottom = tk.Frame(parent, bg="#f7f8fa")
        bottom.pack(fill="x", padx=2, pady=(0, 6))
        ttk.Button(bottom, text="刷新", command=self.refresh_history).pack(side="left")
        ttk.Button(bottom, text="清空历史", command=self._clear_history).pack(side="left", padx=8)

    # ---------- 行为 ----------

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
        return cfg

    def _save(self) -> None:
        cfg = self._collect()
        if self._recorder is not None:
            self._recorder.stop_record(cancelled=False)
        self._on_save(cfg)
        cfgmod.save_config(cfg)
        self.cfg = cfg
        self.set_status("设置已保存。", OK_GREEN)

    def set_status(self, text: str, color: str = MUTED) -> None:
        self.status.configure(text=text, fg=color)

    def _test_connection(self) -> None:
        payload = self._collect()
        self.test_btn.configure(state="disabled")
        self.test_status.configure(text="测试中…", fg=MUTED)

        def work() -> None:
            try:
                msg = api_client.test_connection(payload)
                ok = True
            except Exception as exc:  # noqa: BLE001 - 要把任何失败都展示给用户
                msg = str(exc).replace("\n", " ")
                ok = False
            self._call_soon(lambda: self._test_done(msg, ok))

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, msg: str, ok: bool) -> None:
        self.test_btn.configure(state="normal")
        self.test_status.configure(text=msg[:120], fg=OK_GREEN if ok else ERR_RED)

    def refresh_history(self) -> None:
        self._hist_items = cfgmod.load_history()
        self.hist_list.delete(0, "end")
        for item in self._hist_items:
            when = str(item.get("time", ""))[:19]
            preview = (str(item.get("translation", "")) or "").replace("\n", " ")[:18]
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
        self.hist_text.configure(state="normal")
        self.hist_text.delete("1.0", "end")
        self.hist_text.insert("end", f"时间：{item.get('time', '')}\n")
        if item.get("model"):
            self.hist_text.insert("end", f"模型：{item['model']}\n")
        self.hist_text.insert("end", "\n翻译\n", ("bold",))
        self.hist_text.insert("end", str(item.get("translation", "")) + "\n")
        if item.get("explanation"):
            self.hist_text.insert("end", "\n解释\n", ("bold",))
            self.hist_text.insert("end", str(item["explanation"]) + "\n")
        self.hist_text.tag_configure("bold", font=(FONT_FAMILY, 10, "bold"))
        self.hist_text.configure(state="disabled")

    def _clear_history(self) -> None:
        cfgmod.save_history([], 0)
        self.refresh_history()
        self.set_status("历史已清空。", MUTED)
