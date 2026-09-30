"""屏幕翻译 - 程序入口。

线程模型：
- 主线程：tkinter mainloop，所有窗口操作都在这里
- keyboard 线程：全局热键回调，只往任务队列丢东西，绝不碰 tkinter
- 托盘线程：pystray 消息循环
- 工作线程：网络请求，完成后把结果丢回任务队列
"""

from __future__ import annotations

import ctypes
import io
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from datetime import datetime
from typing import Any, Callable

from PIL import Image, ImageDraw

import api_client
import capture as capture_mod
import config as cfgmod
import icons
import theme as theme_mod
import widgets as w
from capture import virtual_screen_rect
from popup import ResultPopup
from settings_ui import MainWindow

# 用 Local\ 而不是 Global\：Global 命名空间要 SeCreateGlobalPrivilege，
# 普通权限用户下 CreateMutexW 会直接失败，单实例保护会静默失效。
MUTEX_NAME = "Local\\ScreenTranslator_SingleInstance"
ERROR_ALREADY_EXISTS = 183

# 保持互斥体句柄引用，否则会被 GC 关掉，单实例保护就失效了
_mutex_handle: int | None = None


def acquire_single_instance() -> bool:
    """已经有一个实例在跑就返回 False。

    注意：ctypes 默认把返回值当 32 位 int，而 HANDLE 在 64 位下是 64 位指针，
    不显式声明 restype 会被截断；GetLastError 也必须在 use_last_error=True 下
    用 ctypes.get_last_error() 读，否则可能读到被其他调用覆盖过的值。
    """
    global _mutex_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.restype = wintypes.HANDLE
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        handle = create_mutex(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


def system_dpi() -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        try:
            dc = ctypes.windll.user32.GetDC(0)
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(dc, 88))
            ctypes.windll.user32.ReleaseDC(0, dc)
            return dpi
        except Exception:
            return 96


def make_tray_image() -> Image.Image:
    """托盘图标：和 exe 图标同款的深色圆角底 + lucide 白色图标。"""
    size = 64
    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=15, fill=(21, 21, 23, 255)
    )
    source = icons.asset_dir() / "scan-text.png"
    if source.exists():
        glyph = Image.open(source).convert("RGBA")
        inner = int(size * 0.62)
        glyph = glyph.resize((inner, inner), Image.LANCZOS)
        plate.alpha_composite(glyph, ((size - inner) // 2, (size - inner) // 2))
    return plate


class App:
    def __init__(self) -> None:
        self.cfg = cfgmod.load_config()
        self.theme = theme_mod.resolve(theme_mod.normalize_mode(self.cfg.get("theme")))
        self._tasks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy = False
        #: 每次框选递增；回调时序号对不上，说明期间又框选了一次，这个结果已经过期
        self._request_seq = 0
        self._quitting = False
        self._hotkey_suspended = False
        self._popup_visible = False
        self._hotkey_handles: list[Any] = []
        self.tray: Any = None
        self._tray_image = make_tray_image()

        self.root = tk.Tk()
        self.root.withdraw()
        self._apply_scaling()

        self.popup = ResultPopup(self.root, self.cfg, self.theme)
        self.main_window = MainWindow(
            self.root,
            self.cfg,
            self.theme,
            on_save=self._on_settings_saved,
            on_quit=self.quit,
            call_soon=self.call_soon,
            on_hotkey_pause=self._pause_hotkeys,
            on_hotkey_resume=self._resume_hotkeys,
            on_theme_change=self._on_theme_change,
        )

        self._register_hotkeys()
        self._start_tray()

    # ---------- 基础设施 ----------

    def call_soon(self, func: Callable[[], None]) -> None:
        """线程安全：把回调排进主线程执行。"""
        self._tasks.put(func)

    def _apply_scaling(self) -> None:
        """DPI aware 之后 tk 不再自动放大字体，这里手动对齐系统缩放。"""
        dpi = system_dpi()
        try:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=w.FONT_FAMILY, size=10)
            except tk.TclError:
                pass

    def _pump(self) -> None:
        while True:
            try:
                task = self._tasks.get_nowait()
            except queue.Empty:
                break
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - 单个任务出错不该拖垮主循环
                print(f"[task error] {exc}", file=sys.stderr)
        if not self._quitting:
            try:
                self._popup_visible = self.popup.visible
            except tk.TclError:
                pass
            self.root.after(50, self._pump)

    # ---------- 全局热键 ----------

    def _register_hotkeys(self) -> None:
        self._unregister_hotkeys()
        try:
            import keyboard
        except Exception as exc:  # noqa: BLE001
            self.main_window.set_status(f"全局热键不可用：{exc}", self.theme.error)
            return

        hotkey = self.cfg.get("hotkey") or "ctrl+alt+t"

        def on_hotkey() -> None:
            if self._hotkey_suspended or self._quitting:
                return
            self.call_soon(self._begin_capture)

        def on_escape() -> None:
            # 全局兜底：小窗可见时，即使焦点不在小窗上，Esc 也能关掉它
            if self._popup_visible and not self._quitting and not self._hotkey_suspended:
                self.call_soon(self.popup.close)

        try:
            self._hotkey_handles.append(keyboard.add_hotkey(hotkey, on_hotkey, suppress=False))
        except Exception as exc:  # noqa: BLE001
            self.main_window.set_status(f"快捷键 {hotkey} 注册失败：{exc}", self.theme.error)
            return

        try:
            self._hotkey_handles.append(keyboard.add_hotkey("esc", on_escape, suppress=False))
        except Exception:
            pass  # Esc 兜底失败不影响主流程

        self.main_window.set_status(
            f"就绪。按 {hotkey.upper()} 开始框选，Esc 关闭小窗。", self.theme.success
        )

    def _unregister_hotkeys(self) -> None:
        if not self._hotkey_handles:
            return
        try:
            import keyboard

            for handle in self._hotkey_handles:
                try:
                    # add_hotkey 返回的本身就是它的移除函数，直接调用最稳
                    handle()
                except Exception:
                    try:
                        keyboard.remove_hotkey(handle)
                    except Exception:
                        pass
        except Exception:
            pass
        self._hotkey_handles.clear()

    def _pause_hotkeys(self) -> None:
        self._hotkey_suspended = True

    def _resume_hotkeys(self) -> None:
        self._hotkey_suspended = False

    # ---------- 系统托盘 ----------

    def _start_tray(self) -> None:
        def worker() -> None:
            try:
                import pystray
            except Exception:
                return
            menu = pystray.Menu(
                pystray.MenuItem("开始框选", lambda: self.call_soon(self._begin_capture), default=True),
                pystray.MenuItem("打开设置", lambda: self.call_soon(self.main_window.show)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda: self.call_soon(self.quit)),
            )
            try:
                self.tray = pystray.Icon("ScreenTranslator", self._tray_image, cfgmod.APP_TITLE, menu)
                self.tray.run()
            except Exception as exc:  # noqa: BLE001
                print(f"[tray] {exc}", file=sys.stderr)

        threading.Thread(target=worker, name="tray", daemon=True).start()

    # ---------- 截图 -> 翻译 ----------

    def _begin_capture(self) -> None:
        if self._busy or self._quitting:
            return
        self._busy = True
        self.popup.close()
        if self.main_window.visible:
            self.main_window.hide()
            # 等窗口真正消失再截屏，否则会把自己截进去
            self.root.after(220, self._do_capture)
        else:
            self.root.after(40, self._do_capture)

    def _do_capture(self) -> None:
        try:
            selector = capture_mod.RegionSelector(self.root)
            self._selector = selector
            selection = selector.select()
        except Exception as exc:  # noqa: BLE001
            self._busy = False
            self.popup.show_error(f"截屏失败：{exc}")
            self._popup_visible = True
            return
        self._busy = False
        if selection is None:
            return

        vx, vy, _vw, _vh = virtual_screen_rect()
        x1, y1, x2, y2 = selection.box
        anchor = (vx + x1, vy + y1, vx + x2, vy + y2)

        self.popup.show_loading(anchor)
        self._popup_visible = True

        image = selection.image
        self._request_seq += 1
        seq = self._request_seq
        threading.Thread(
            target=self._request_worker,
            args=(image, anchor, seq),
            name="api",
            daemon=True,
        ).start()

    def _request_worker(
        self, image: Image.Image, anchor: tuple[int, int, int, int], seq: int
    ) -> None:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        png = buffer.getvalue()
        want_explanation = bool(self.cfg.get("show_explanation", True))
        try:
            result = api_client.translate_image(png, self.cfg, want_explanation=want_explanation)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            self.call_soon(lambda m=message: self._on_error(m, anchor, seq))
            return
        self.call_soon(lambda r=result: self._on_result(r, anchor, seq))

    def _on_result(
        self, result: api_client.Result, anchor: tuple[int, int, int, int], seq: int
    ) -> None:
        if seq != self._request_seq:
            # 期间又框选过，这个结果属于上一次，直接丢弃，
            # 否则它会盖掉更新的那一份
            return
        meta_parts: list[str] = []
        if result.elapsed:
            meta_parts.append(f"{result.elapsed:.1f} 秒")
        usage = result.usage or {}
        if usage.get("total_tokens"):
            meta_parts.append(f"{usage['total_tokens']} tokens")
        if result.model:
            meta_parts.append(result.model)
        if result.notice:
            meta_parts.append(result.notice)
        meta = "  ·  ".join(meta_parts)

        self.popup.show_result(
            result.translation, result.explanation, anchor=anchor, meta=meta
        )
        self._popup_visible = True

        if self.cfg.get("history_enabled", True):
            entry = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "translation": result.translation,
                "explanation": result.explanation,
                "model": result.model,
            }
            try:
                cfgmod.append_history(entry, int(self.cfg.get("max_history", 50)))
            except OSError as exc:
                print(f"[history] {exc}", file=sys.stderr)

    def _on_error(self, message: str, anchor: tuple[int, int, int, int], seq: int) -> None:
        if seq != self._request_seq:
            return
        self.popup.show_error(message, anchor)
        self._popup_visible = True

    # ---------- 设置与退出 ----------

    def _on_settings_saved(self, cfg: dict[str, Any]) -> None:
        old_hotkey = self.cfg.get("hotkey")
        self.cfg = cfg
        self.popup.cfg = cfg
        self.main_window.cfg = cfg
        if cfg.get("hotkey") != old_hotkey:
            self._register_hotkeys()
        else:
            self.main_window.set_status(
                f"就绪。按 {(cfg.get('hotkey') or '').upper()} 开始框选。", self.theme.success
            )

    def _on_theme_change(self, mode: str) -> None:
        """外观模式变了：解析成具体主题并重建所有窗口。"""
        self.cfg["theme"] = mode
        try:
            cfgmod.save_config(self.cfg)
        except OSError as exc:
            print(f"[theme] 保存外观设置失败：{exc}", file=sys.stderr)
        self.theme = theme_mod.resolve(mode)
        icons.store.clear()  # 旧颜色的图标缓存没用了
        self.main_window.cfg = self.cfg
        self.main_window.set_theme(self.theme)
        self.popup.set_theme(self.theme)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._unregister_hotkeys()
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        if not (self.cfg.get("api_key") or "").strip():
            self.main_window.set_status("还没填 API Key，填好后点「保存设置」。", self.theme.warn)
        elif not self._hotkey_handles:
            pass
        self.main_window.show()
        self.root.after(50, self._pump)
        self.root.mainloop()


def show_already_running() -> None:
    """提示已经有一个实例在跑。

    用 Windows 原生的 MessageBoxTimeoutW，而不是 tkinter 的 messagebox：
    后者必须等用户点击才返回，用户不理它进程就一直挂着。
    """
    text = "程序已经在运行了，请看任务栏右下角的托盘图标。"
    try:
        MB_ICONINFORMATION = 0x40
        MB_SETFOREGROUND = 0x00010000
        ctypes.windll.user32.MessageBoxTimeoutW(
            0, text, cfgmod.APP_TITLE, MB_ICONINFORMATION | MB_SETFOREGROUND, 0, 3000
        )
    except Exception:
        print("程序已在运行。")


def main() -> int:
    if not acquire_single_instance():
        show_already_running()
        return 0

    # 必须在创建 Tk 之前声明 DPI 感知
    capture_mod.enable_dpi_awareness()

    app = App()
    try:
        app.run()
    finally:
        try:
            import keyboard

            keyboard.unhook_all()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
