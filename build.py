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
    """生成多尺寸 icon.ico。

    这里直接复用 ``build_icons.build_app_icon()``，它用的是 256×256 的源图。
    注意不要把 64×64 的托盘图拿来当源图——Pillow 保存 ICO 时会**跳过比源图更大的尺寸**，
    那样最终 ico 里只剩 16~64，桌面在大图标视图（125% 缩放下要 96~128）下会找不到合适尺寸，
    直接回退成默认图标。
    """
    try:
        import build_icons
    except Exception as exc:  # noqa: BLE001
        print(f"跳过图标生成（{exc}），将使用 PyInstaller 默认图标")
        return
    build_icons.build_app_icon()


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
        # 图标资源要一起打进去：assets/icons 是界面小图标，icon.ico 是窗口/任务栏图标
        "--add-data",
        f"{ROOT / 'assets' / 'icons'}{os.pathsep}assets/icons",
        "--add-data",
        f"{ICON}{os.pathsep}.",
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
    import applog

    applog.ensure_utf8_stdio()
    raise SystemExit(main())
