"""一条命令发版：改版本号、提交、推送 main、打 tag、推送 tag。

用法::

    python release.py 1.0.6 "这一版改了什么"

存在的意义：手工发版连续两次出现「tag 落后于 main」——先打了 tag，之后又提交了东西，
于是 Release 的源码和 main 对不上，用户下载到的版本少了修复。
这个脚本把顺序固定死，并且拒绝在脏工作区上操作：

1. 工作区必须干净（有未提交改动就退出，绝不替用户决定提交什么）
2. 改 ``config.py`` 里的 ``APP_VERSION`` 并提交
3. **先推 main，再打 tag**——顺序反了就会出现 tag 指向别人拉不到的提交
4. 推送 tag，剩下的交给 CI 自动构建发布
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.py"
VERSION_RE = re.compile(r'^APP_VERSION = "([^"]+)"$', re.MULTILINE)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：\n{result.stdout}{result.stderr}")
    return result


def current_version() -> str:
    match = VERSION_RE.search(CONFIG.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("在 config.py 里找不到 APP_VERSION")
    return match.group(1)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    version = sys.argv[1].lstrip("v")
    message = sys.argv[2] if len(sys.argv) > 2 else f"v{version}"

    # 1. 工作区必须干净
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        print("工作区不干净，先提交或 stash 再发版：")
        print(dirty)
        return 1
    print("工作区干净")

    # 2. 版本号
    before = current_version()
    if before == version:
        print(f"APP_VERSION 已经是 {version}，跳过改号与提交")
    else:
        CONFIG.write_text(
            VERSION_RE.sub(f'APP_VERSION = "{version}"', CONFIG.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        git("add", "config.py")
        git("commit", "-m", f"chore: 版本号升到 {version}")
        print(f"APP_VERSION {before} -> {version}，已提交")

    # 3. 先推 main（必须早于打 tag）
    git("push", "origin", "main")
    print("main 已推送")

    # 4. tag
    if git("tag", "--list", f"v{version}").stdout.strip():
        print(f"tag v{version} 已存在，跳过")
    else:
        git("tag", "-a", f"v{version}", "-m", message)
        git("push", "origin", f"v{version}")
        print(f"tag v{version} 已推送")

    head = git("rev-parse", "--short", "HEAD").stdout.strip()
    print()
    print(f"完成：main 与 v{version} 都指向 {head}")
    print("CI 会自动构建并把 exe 附到 Release。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
