"""在桌面创建启动快捷方式。

用法::

    python install.py

做两件事：

1. 在桌面生成一个指向 ``pythonw.exe`` 的快捷方式，图标用程序自带的 ``icon.ico``；
2. 顺手清掉旧版本可能留下的、指向打包 exe 的快捷方式。

**为什么用 pythonw 而不是打包的 exe**：``pythonw.exe`` 是 Python 官方签名的程序，Windows 不会对它
做 SmartScreen 检查；而 PyInstaller 打出来的 exe 没有数字签名，双击必然被拦一次，
放在含中文的路径下还会直接启动失败。
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "屏幕翻译"
ENTRY = ROOT / "main.py"
ICON = ROOT / "icon.ico"

#: 旧版本可能留下的快捷方式名字，装新的时一并清掉
STALE_NAMES = ("ScreenTranslator.lnk", "屏幕翻译（源码版）.lnk")


def find_pythonw() -> Path | None:
    """优先用当前解释器同目录的 pythonw.exe。"""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else None


def run_powershell(script: str) -> tuple[bool, str]:
    """执行 PowerShell 脚本，返回 (是否成功, 输出)。

    脚本按 UTF-16LE 编码走 ``-EncodedCommand``，避免中文路径在命令行上传参时被搞坏。
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    return result.returncode == 0, output or error


def build_script(pythonw: Path) -> str:
    """桌面路径交给 Windows 自己解析，兼容桌面被重定向到 OneDrive 的情况。"""
    stale_list = ", ".join(f"'{name}'" for name in STALE_NAMES)
    return f'''
$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
if (-not (Test-Path $desktop)) {{ throw "找不到桌面目录" }}

$sh = New-Object -ComObject WScript.Shell
foreach ($name in @({stale_list})) {{
    $old = Join-Path $desktop $name
    if (Test-Path $old) {{
        Remove-Item $old -Force
        Write-Output "已删除旧快捷方式：$name"
    }}
}}

$target = Join-Path $desktop "{APP_NAME}.lnk"
$lnk = $sh.CreateShortcut($target)
$lnk.TargetPath = "{pythonw}"
$lnk.Arguments = '"{ENTRY}"'
$lnk.WorkingDirectory = "{ROOT}"
$lnk.IconLocation = "{ICON}"
$lnk.Description = "{APP_NAME}：源码方式启动，不触发 SmartScreen 警告"
$lnk.Save()
Write-Output "已创建快捷方式：$target"
'''


def main() -> int:
    if sys.platform != "win32":
        print("这个脚本只适用于 Windows。直接运行 python main.py 即可。")
        return 1

    pythonw = find_pythonw()
    if pythonw is None:
        print(f"在 {Path(sys.executable).parent} 里找不到 pythonw.exe，无法创建快捷方式。")
        print("可以手动运行：python main.py")
        return 1

    if not ENTRY.exists():
        print(f"找不到入口文件 {ENTRY}")
        return 1

    ok, message = run_powershell(build_script(pythonw))
    print(message)
    if not ok:
        print(f'\n创建失败。也可以手动建：目标填 {pythonw}，参数填 "{ENTRY}"')
        return 1

    print()
    print(f"  目标   : {pythonw}")
    print(f'  参数   : "{ENTRY}"')
    print(f"  起始于 : {ROOT}")
    print(f"  图标   : {ICON}")
    print()
    print("双击桌面上的「屏幕翻译」即可启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
