"""主题配色与系统外观检测。

色值取自 DeepSeek Harness 的主题包 ``dsh-client-ui-theme``：light 定义在
``body{}``、dark 定义在 ``body[data-ds-dark-theme]{}``，原始写法是一层
``--dsw-alias-*`` 指向 ``--dsw-static-*`` 的映射，这里把解析后的最终色值固化下来。

设计取向跟 DSH 保持一致：没有彩色强调色，全靠中性灰阶和黑白对比。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- 颜色工具


def _blend(overlay: str, base: str) -> str:
    """把 ``#rrggbbaa`` 叠到不透明底色上，返回 ``#rrggbb``。

    tkinter 不支持带 alpha 的颜色，所以带透明度的边框色必须提前混合。
    """
    over = overlay.lstrip("#")
    under = base.lstrip("#")
    orr, org, orb = (int(over[i : i + 2], 16) for i in (0, 2, 4))
    brr, brg, brb = (int(under[i : i + 2], 16) for i in (0, 2, 4))
    alpha = int(over[6:8], 16) / 255.0 if len(over) == 8 else 1.0
    return "#{:02x}{:02x}{:02x}".format(
        round(brr * (1 - alpha) + orr * alpha),
        round(brg * (1 - alpha) + org * alpha),
        round(brb * (1 - alpha) + orb * alpha),
    )


def _mix(color_a: str, color_b: str, ratio: float) -> str:
    """按比例混合两个不透明色，``ratio`` 是 ``color_b`` 的权重。"""
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")
    return "#{:02x}{:02x}{:02x}".format(
        *(
            round(int(a[i : i + 2], 16) * (1 - ratio) + int(b[i : i + 2], 16) * ratio)
            for i in (0, 2, 4)
        )
    )


# ---------------------------------------------------------------- 主题定义


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    dark: bool

    bg_base: str  # 应用底色
    bg_layer1: str  # 抬升表面：卡片、面板
    bg_layer2: str  # 次级表面：输入框、代码块
    bg_overlay: str  # 浮层：小窗、菜单
    bg_hover: str  # 悬停态
    bg_selected: str  # 选中态

    border_l1: str  # 弱边框
    border_l2: str  # 强边框

    brand: str  # 品牌色，也是主按钮底色
    on_brand: str  # 品牌色上的文字
    brand_hover: str

    label_primary: str
    label_secondary: str
    label_tertiary: str

    error: str
    success: str
    warn: str
    idle: str

    #: 框选遮罩用的纯黑，透明度单独由 apply 时的 -alpha 控制
    mask: str = "#000000"


_DARK_BASE = "#151517"

LIGHT = Theme(
    key="light",
    label="浅色",
    dark=False,
    bg_base="#ffffff",
    bg_layer1="#ffffff",
    bg_layer2="#f9fafb",
    bg_overlay="#ffffff",
    bg_hover="#f1f3f5",
    bg_selected="#ebeef2",
    border_l1=_blend("#0000000a", "#ffffff"),
    border_l2=_blend("#0000001a", "#ffffff"),
    brand="#0f1115",
    on_brand="#f9fafb",
    brand_hover="#2c2c2e",
    label_primary="#0f1115",
    label_secondary="#61666b",
    label_tertiary="#979da6",
    error="#ec1313",
    success="#22c55e",
    warn="#f59e0b",
    idle="#d4d4d4",
)

DARK = Theme(
    key="dark",
    label="深色",
    dark=True,
    bg_base=_DARK_BASE,
    bg_layer1="#232324",
    bg_layer2="#2c2c2e",
    bg_overlay="#1b1b1c",
    bg_hover="#2c2c2e",
    bg_selected="#353638",
    border_l1=_blend("#ffffff0f", _DARK_BASE),
    border_l2=_blend("#ffffff1f", _DARK_BASE),
    brand="#f9fafb",
    on_brand="#0f1115",
    brand_hover="#cfd3d6",
    label_primary="#f9fafb",
    label_secondary="#cfd3d6",
    label_tertiary="#979da6",
    error="#f25a5a",
    success="#22c55e",
    warn="#f59e0b",
    idle="#545557",
)

THEMES = {"light": LIGHT, "dark": DARK}

#: 界面里可选的外观模式，system 会跟随 Windows 的应用主题
MODES = ("light", "dark", "system")
MODE_LABELS = {"light": "浅色", "dark": "深色", "system": "跟随系统"}
MODE_ICONS = {"light": "sun", "dark": "moon", "system": "monitor"}


def system_prefers_dark() -> bool:
    """读注册表判断 Windows 当前是否用深色应用主题。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return False


def resolve(mode: str) -> Theme:
    """把外观模式解析成具体主题。"""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if system_prefers_dark() else LIGHT


def normalize_mode(mode: str | None) -> str:
    return mode if mode in MODES else "light"
