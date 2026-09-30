"""配置读写、密钥保护，以及"幽灵配置"的自动防护。

幽灵配置指的是：DEFAULTS 里定义了、但全项目没有任何地方消费的键。
它会误导后来维护的人以为某项行为可配，所以这里用测试把它钉死。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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


@unittest.skipUnless(sys.platform == "win32", "DPAPI 只在 Windows 上可用")
class SecretProtectionTests(TempHomeTestCase):
    SECRET = "sk-super-secret-value-1234567890"

    def test_roundtrip(self):
        stored = cfgmod.encrypt_secret(self.SECRET)
        self.assertNotEqual(stored, self.SECRET)
        self.assertTrue(stored.startswith(cfgmod.SECRET_PREFIX))
        self.assertEqual(cfgmod.decrypt_secret(stored), self.SECRET)

    def test_legacy_plaintext_still_reads(self):
        """旧版写在文件里的是明文，升级后必须还能读出来。"""
        self.assertEqual(cfgmod.decrypt_secret("sk-legacy-plain"), "sk-legacy-plain")

    def test_empty_value_stays_empty(self):
        self.assertEqual(cfgmod.encrypt_secret(""), "")
        self.assertEqual(cfgmod.decrypt_secret(""), "")

    def test_broken_ciphertext_returns_empty(self):
        self.assertEqual(cfgmod.decrypt_secret("dpapi:@@@不是 base64@@@"), "")

    def test_saved_file_contains_no_plaintext(self):
        cfg = cfgmod.load_config()
        cfg["api_key"] = self.SECRET
        cfgmod.save_config(cfg)
        raw = cfgmod.config_path().read_text(encoding="utf-8")
        self.assertNotIn(self.SECRET, raw, "配置文件里不应该出现明文密钥")
        self.assertIn(cfgmod.SECRET_PREFIX, raw)

    def test_saved_config_reads_back_as_plaintext(self):
        cfg = cfgmod.load_config()
        cfg["api_key"] = self.SECRET
        cfgmod.save_config(cfg)
        self.assertEqual(cfgmod.load_config()["api_key"], self.SECRET)


if __name__ == "__main__":
    unittest.main()
