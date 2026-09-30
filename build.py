"""打包成单文件 exe。

用法::

    pip install pyinstaller
    python build.py

产物：dist/ScreenTranslator.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "icon.ico"
NAME = "ScreenTranslator"


def make_icon() -> None:
    """用和托盘图标一致的图案生成多尺寸 ico。"""
    try:
        from main import make_tray_image
    except Exception as exc:  # noqa: BLE001
        print(f"跳过图标生成（{exc}），将使用 PyInstaller 默认图标")
        return
    image = make_tray_image()
    image.save(
        ICON,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"图标已生成：{ICON}")


def main() -> int:
    make_icon()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        NAME,
        # pystray 的后端是按平台动态选择的，PyInstaller 静态分析看不到
        "--hidden-import",
        "pystray._win32",
        # keyboard 通过 ctypes 调 Win32 钩子，同样需要显式声明
        "--hidden-import",
        "keyboard",
        # lucide 图标资源要一起打进去，运行时会从 sys._MEIPASS 读
        "--add-data",
        f"{ROOT / 'assets' / 'icons'}{os.pathsep}assets/icons",
    ]
    if ICON.exists():
        command += ["--icon", str(ICON)]
    command.append(str(ROOT / "main.py"))

    print("执行:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode == 0:
        print(f"\n完成，产物：{ROOT / 'dist' / (NAME + '.exe')}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
