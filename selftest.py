"""环境自检。

用法::

    python selftest.py          # 只检查本地环境，不联网
    python selftest.py --api    # 额外真实调用一次模型，验证 API Key

排查"程序装了但没反应"这类问题时先跑这个。
"""

from __future__ import annotations

import argparse

OK = "[ OK ]"
FAIL = "[FAIL]"


def main() -> int:
    parser = argparse.ArgumentParser(description="屏幕翻译环境自检")
    parser.add_argument("--api", action="store_true", help="额外做一次真实的模型调用")
    args = parser.parse_args()

    failures = 0

    print("=" * 60)
    print("屏幕翻译 自检")
    print("=" * 60)

    print("\n[1] 依赖")
    for name in ("PIL", "pystray", "keyboard"):
        try:
            __import__(name)
            print(f"  {OK} {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  {FAIL} {name}: {exc}")

    print("\n[2] 配置")
    import config as cfgmod

    cfg = cfgmod.load_config()
    print(f"  {OK} 配置文件: {cfgmod.config_path()}")
    print(f"       API Key : {cfgmod.mask_key(cfg.get('api_key', ''))}")
    print(f"       模型    : {cfg.get('model')}")
    print(f"       快捷键  : {cfg.get('hotkey')}")

    print("\n[3] 屏幕与 DPI")
    import tkinter as tk

    import capture as cm

    awareness = cm.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    vx, vy, vw, vh = cm.virtual_screen_rect()
    image = cm.grab_all_screens()
    print(f"  {OK} DPI 感知 : {awareness}")
    print(f"  {OK} tk scaling: {root.tk.call('tk', 'scaling')}")
    print(f"  {OK} 虚拟桌面 : {vw}x{vh} 起点 ({vx},{vy})")
    print(f"  {OK} 截图尺寸 : {image.size}")
    if image.size != (vw, vh):
        failures += 1
        print(f"  {FAIL} 截图尺寸与虚拟桌面不一致，框选坐标会偏移！")
    image.close()
    root.destroy()

    print("\n[4] 全局热键")
    try:
        import keyboard

        hotkey = cfg.get("hotkey") or "ctrl+alt+t"
        remover = keyboard.add_hotkey(hotkey, lambda: None, suppress=False)
        remover()
        print(f"  {OK} 可以注册 {hotkey}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  {FAIL} 注册失败: {exc}")

    if args.api:
        print("\n[5] API 调用")
        import api_client

        try:
            print("  " + api_client.test_connection(cfg))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  {FAIL} {exc}")
    else:
        print("\n[5] API 调用（加 --api 参数才会测）")

    print("\n" + "=" * 60)
    print(f"自检完成，失败项：{failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
