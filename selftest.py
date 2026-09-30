"""环境自检。

用法::

    python selftest.py          # 只检查本地环境，不联网
    python selftest.py --api    # 额外真实调用一次模型，验证 API Key

每一项都独立捕获异常：任何一项失败或崩溃都不会影响后面的检查，
所以跑一次就能拿到完整报告，而不是在第一个坑上停住。
"""

from __future__ import annotations

import argparse
import traceback

import applog

# 模块导入时就调整 stdout 编码：Report 是可被直接使用的公共类，
# 不能指望调用方一定经过 main()（单元测试就是直接调它的）。
applog.ensure_utf8_stdio()

OK = "[ OK ]"
FAIL = "[FAIL]"


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.index = 0

    def section(self, title: str, check) -> None:
        self.index += 1
        print(f"\n[{self.index}] {title}")
        try:
            check(self)
        except Exception as exc:  # noqa: BLE001 - 单项炸了不该拖垮整份报告
            self.fail(f"这一项自身出错：{type(exc).__name__}: {exc}")
            for line in traceback.format_exc().strip().splitlines()[-2:]:
                print(f"       {line}")

    def ok(self, message: str) -> None:
        print(f"  {OK} {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"  {FAIL} {message}")

    def info(self, message: str) -> None:
        print(f"       {message}")


def check_deps(report: Report) -> None:
    for name in ("PIL", "pystray", "keyboard"):
        try:
            __import__(name)
            report.ok(name)
        except Exception as exc:  # noqa: BLE001
            report.fail(f"{name}: {exc}")


def check_config(report: Report) -> None:
    import config as cfgmod

    cfg = cfgmod.load_config()
    report.ok(f"配置文件: {cfgmod.config_path()}")
    report.info(f"API Key : {cfgmod.mask_key(cfg.get('api_key', ''))}")
    report.info(f"模型    : {cfg.get('model')}")
    report.info(f"快捷键  : {cfg.get('hotkey')}")
    report.info(f"外观    : {cfg.get('theme')}")


def check_screen(report: Report) -> None:
    import tkinter as tk

    import capture as cm

    awareness = cm.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    try:
        vx, vy, vw, vh = cm.virtual_screen_rect()
        report.ok(f"DPI 感知 : {awareness}")
        report.ok(f"tk scaling: {root.tk.call('tk', 'scaling')}")
        report.ok(f"虚拟桌面 : {vw}x{vh} 起点 ({vx},{vy})")

        try:
            image = cm.grab_all_screens()
        except Exception as exc:  # noqa: BLE001
            report.fail(f"屏幕抓取失败：{type(exc).__name__}: {exc}")
            report.info("常见原因：远程桌面会话、显示器休眠、安全软件拦截截屏 API")
            return

        report.ok(f"截图尺寸 : {image.size}")
        if image.size != (vw, vh):
            report.fail("截图尺寸与虚拟桌面不一致，框选坐标会偏移")
        image.close()
    finally:
        root.destroy()


def check_hotkey(report: Report) -> None:
    import config as cfgmod

    cfg = cfgmod.load_config()
    import keyboard

    hotkey = cfg.get("hotkey") or "ctrl+alt+t"
    remover = keyboard.add_hotkey(hotkey, lambda: None, suppress=False)
    remover()
    report.ok(f"可以注册 {hotkey}")


def check_icons(report: Report) -> None:
    import icons

    names = icons.available()
    if not names:
        report.fail(f"找不到图标目录 {icons.asset_dir()}")
        report.info("跑 python build_icons.py 重新生成")
        return
    report.ok(f"{len(names)} 个图标：{', '.join(names[:8])} ...")
    missing = [n for n in ("scan-text", "sun", "moon", "settings", "x", "copy") if n not in names]
    if missing:
        report.fail(f"缺少图标：{', '.join(missing)}")

    icon_file = icons.app_icon_path()
    if icon_file is None:
        report.fail("找不到 icon.ico，窗口和任务栏会退回默认图标")
        report.info("跑 python build_icons.py 重新生成")
    else:
        report.ok(f"程序图标：{icon_file}")


def check_api(report: Report) -> None:
    import api_client
    import config as cfgmod

    cfg = cfgmod.load_config()
    report.info(api_client.test_connection(cfg))


def main() -> int:
    parser = argparse.ArgumentParser(description="屏幕翻译环境自检")
    parser.add_argument("--api", action="store_true", help="额外做一次真实的模型调用")
    args = parser.parse_args()

    report = Report()

    print("=" * 60)
    print("屏幕翻译 自检")
    print("=" * 60)

    report.section("依赖", check_deps)
    report.section("配置", check_config)
    report.section("屏幕与 DPI", check_screen)
    report.section("全局热键", check_hotkey)
    report.section("图标资源", check_icons)
    if args.api:
        report.section("API 调用", check_api)
    else:
        report.index += 1
        print(f"\n[{report.index}] API 调用")
        report.info("加 --api 参数才会真实调用一次模型")

    print("\n" + "=" * 60)
    if report.failures:
        print(f"自检完成，失败项：{report.failures}")
    else:
        print("自检完成，全部通过")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
