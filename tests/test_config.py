"""配置读写、密钥保护，以及"幽灵配置"的自动防护。

幽灵配置指的是：DEFAULTS 里定义了、但全项目没有任何地方消费的键。
它会误导后来维护的人以为某项行为可配，所以这里用测试把它钉死。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as cfgmod


class GhostConfigTests(unittest.TestCase):
    def test_every_default_key_is_actually_used(self):
        sources = [
            path.read_text(encoding="utf-8")
            for path in ROOT.glob("*.py")
            if path.name != "config.py"
        ]
        unused = [
            key
            for key in cfgmod.DEFAULTS
            if not any(f'"{key}"' in text for text in sources)
        ]
        self.assertEqual(unused, [], f"这些配置项定义了但没有任何地方使用：{unused}")

    def test_example_file_matches_defaults(self):
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(example), set(cfgmod.DEFAULTS), "config.example.json 与 DEFAULTS 的字段不一致"
        )
        self.assertEqual(example, cfgmod.DEFAULTS, "config.example.json 的默认值与 DEFAULTS 不一致")


class TempHomeTestCase(unittest.TestCase):
    """把配置目录指到临时目录，避免测试碰真实数据。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SCREEN_TRANSLATOR_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("SCREEN_TRANSLATOR_HOME", None)
        self.tmp.cleanup()


class ConfigIOTests(TempHomeTestCase):
    def test_missing_file_yields_defaults(self):
        self.assertEqual(cfgmod.load_config(), dict(cfgmod.DEFAULTS))

    def test_save_load_roundtrip(self):
        cfg = cfgmod.load_config()
        cfg["api_key"] = "test-key"
        cfg["hotkey"] = "ctrl+shift+x"
        cfgmod.save_config(cfg)
        again = cfgmod.load_config()
        self.assertEqual(again["api_key"], "test-key")
        self.assertEqual(again["hotkey"], "ctrl+shift+x")

    def test_new_keys_are_backfilled(self):
        """旧版配置文件缺字段时，应该用默认值补齐而不是报错。"""
        cfgmod.config_dir().mkdir(parents=True, exist_ok=True)
        cfgmod.config_path().write_text(json.dumps({"api_key": "x"}), encoding="utf-8")
        loaded = cfgmod.load_config()
        self.assertEqual(loaded["api_key"], "x")
        self.assertEqual(loaded["model"], cfgmod.DEFAULTS["model"])
        self.assertEqual(loaded["theme"], cfgmod.DEFAULTS["theme"])

    def test_unknown_keys_are_dropped(self):
        cfgmod.config_dir().mkdir(parents=True, exist_ok=True)
        cfgmod.config_path().write_text(
            json.dumps({"api_key": "x", "not_a_real_key": 1}), encoding="utf-8"
        )
        self.assertNotIn("not_a_real_key", cfgmod.load_config())

    def test_corrupted_file_falls_back_to_defaults(self):
        cfgmod.config_dir().mkdir(parents=True, exist_ok=True)
        cfgmod.config_path().write_text("{ 这不是合法 json", encoding="utf-8")
        self.assertEqual(cfgmod.load_config(), dict(cfgmod.DEFAULTS))

    def test_history_prepends_and_trims(self):
        for index in range(5):
            cfgmod.append_history({"time": str(index), "translation": f"t{index}"}, max_items=3)
        items = cfgmod.load_history()
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["translation"], "t4")

    def test_corrupted_history_returns_empty(self):
        cfgmod.config_dir().mkdir(parents=True, exist_ok=True)
        cfgmod.history_path().write_text("不是 json", encoding="utf-8")
        self.assertEqual(cfgmod.load_history(), [])

    def test_mask_key_hides_secret(self):
        self.assertEqual(cfgmod.mask_key(""), "(未设置)")
        masked = cfgmod.mask_key("sk-abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("ghijklmnop", masked)
        self.assertTrue(masked.startswith("sk-abc"))

    def test_save_backs_up_previous_config(self):
        """覆盖配置前留一份备份：万一误清空了 API Key 还能找回。"""
        cfg = cfgmod.load_config()
        cfg["api_key"] = "first-key"
        cfgmod.save_config(cfg)
        self.assertFalse(cfgmod.backup_path().exists(), "首次保存没有旧文件，不该产生备份")

        cfg["api_key"] = "second-key"
        cfgmod.save_config(cfg)
        self.assertTrue(cfgmod.backup_path().exists(), "覆盖前应该生成备份")

        backup = json.loads(cfgmod.backup_path().read_text(encoding="utf-8"))
        self.assertEqual(cfgmod.decrypt_secret(backup["api_key"]), "first-key")


class SecretFallbackTests(TempHomeTestCase):
    """不依赖真实 DPAPI 的密钥处理测试，任何环境都能跑。

    失败路径用 mock 构造——受限 Windows 环境里 CryptProtectData 真的会失败，
    那些机器上不该因为这些测试永远报红。
    """

    SECRET = "sk-super-secret-value-1234567890"

    def test_legacy_plaintext_still_reads(self):
        """旧版写在文件里的是明文，升级后必须还能读出来。"""
        self.assertEqual(cfgmod.decrypt_secret("sk-legacy-plain"), "sk-legacy-plain")

    def test_empty_value_stays_empty(self):
        self.assertEqual(cfgmod.encrypt_secret(""), "")
        self.assertEqual(cfgmod.decrypt_secret(""), "")

    def test_broken_ciphertext_returns_empty(self):
        self.assertEqual(cfgmod.decrypt_secret("dpapi:@@@不是 base64@@@"), "")

    def test_plaintext_fallback_is_detectable(self):
        """DPAPI 失败时必须能被调用方察觉，不能悄悄存明文。"""
        with mock.patch.object(cfgmod, "_dpapi", return_value=None):
            self.assertFalse(cfgmod.encryption_available(), "探测应报告加密不可用")
            stored = cfgmod.encrypt_secret(self.SECRET)
            self.assertEqual(stored, self.SECRET, "加密不可用时原样返回")
            self.assertFalse(cfgmod.is_encrypted(stored), "调用方必须能看出这是明文")

    def test_availability_reflects_reality(self):
        """探测结果必须和实际加密行为一致，否则界面上的判断就是在骗人。"""
        self.assertEqual(
            cfgmod.encryption_available(),
            cfgmod.is_encrypted(cfgmod.encrypt_secret(self.SECRET)),
        )

    def test_saved_config_reads_back_as_plaintext(self):
        """不管加密可不可用，存进去的 Key 都要能原样读回来。"""
        cfg = cfgmod.load_config()
        cfg["api_key"] = self.SECRET
        cfgmod.save_config(cfg)
        self.assertEqual(cfgmod.load_config()["api_key"], self.SECRET)

    def test_plaintext_fallback_roundtrips_through_disk(self):
        """降级路径也要验证「存盘 → 读回」。

        mock 掉 DPAPI 之后，落盘的应当是明文（调用方已被告知），
        并且读回来仍是原来的值。
        """
        with mock.patch.object(cfgmod, "_dpapi", return_value=None):
            cfg = cfgmod.load_config()
            cfg["api_key"] = self.SECRET
            cfgmod.save_config(cfg)

            raw = cfgmod.config_path().read_text(encoding="utf-8")
            self.assertIn(self.SECRET, raw, "降级时确实是明文写进了文件")
            self.assertNotIn(cfgmod.SECRET_PREFIX, raw)
            self.assertEqual(cfgmod.load_config()["api_key"], self.SECRET)


@unittest.skipUnless(sys.platform == "win32", "DPAPI 只在 Windows 上可用")
class SecretProtectionTests(TempHomeTestCase):
    """这些测试要求 DPAPI **真的能用**。

    受限 Windows 环境（沙箱、受限账户、部分企业策略）里 CryptProtectData 会失败，
    这时应当跳过而不是报红——"是 Windows"并不等于"DPAPI 可用"。
    """

    SECRET = "sk-super-secret-value-1234567890"

    def setUp(self):
        super().setUp()
        if not cfgmod.encryption_available():
            self.skipTest(f"当前环境 DPAPI 不可用（错误码 {cfgmod.dpapi_last_error()}）")

    def test_roundtrip(self):
        stored = cfgmod.encrypt_secret(self.SECRET)
        self.assertNotEqual(stored, self.SECRET)
        self.assertTrue(stored.startswith(cfgmod.SECRET_PREFIX))
        self.assertEqual(cfgmod.decrypt_secret(stored), self.SECRET)

    def test_saved_file_contains_no_plaintext(self):
        cfg = cfgmod.load_config()
        cfg["api_key"] = self.SECRET
        cfgmod.save_config(cfg)
        raw = cfgmod.config_path().read_text(encoding="utf-8")
        self.assertNotIn(self.SECRET, raw, "配置文件里不应该出现明文密钥")
        self.assertIn(cfgmod.SECRET_PREFIX, raw)

    def test_encrypted_value_is_recognizable(self):
        stored = cfgmod.encrypt_secret(self.SECRET)
        self.assertTrue(cfgmod.is_encrypted(stored))


class VersionTests(unittest.TestCase):
    @staticmethod
    def _parse(text: str) -> tuple[int, ...]:
        parts: list[int] = []
        for piece in text.split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    def test_version_is_not_behind_latest_tag(self):
        """APP_VERSION 不能落后于最新 tag。

        写成"必须相等"会在还没打 tag 的开发期误报，所以这里只禁止落后——
        正好覆盖"发了新版却忘了改版本号"这个真实发生过的问题。
        """
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("取不到 git 信息")

        if result.returncode != 0 or not result.stdout.strip():
            self.skipTest("不在 git 仓库里，或者还没有 tag")

        tag = result.stdout.strip().lstrip("v")
        self.assertGreaterEqual(
            self._parse(cfgmod.APP_VERSION),
            self._parse(tag),
            f"config.py 里 APP_VERSION={cfgmod.APP_VERSION} 落后于最新 tag v{tag}，发版时忘了同步",
        )


if __name__ == "__main__":
    unittest.main()
