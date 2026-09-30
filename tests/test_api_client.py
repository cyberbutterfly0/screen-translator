"""api_client 的纯逻辑测试：输出解析、参数降级、错误映射。

这里不碰网络，全部是可以在 CI 上秒过的确定行为。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_client as api


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json_passes_through(self):
        text = '{"translation": "你好", "explanation": ""}'
        self.assertEqual(api._extract_json_text(text), text)

    def test_fenced_with_language_tag(self):
        self.assertEqual(
            api._extract_json_text('```json\n{"translation": "你好"}\n```'),
            '{"translation": "你好"}',
        )

    def test_fenced_without_language_tag(self):
        self.assertEqual(api._extract_json_text('```\n{"a": 1}\n```'), '{"a": 1}')

    def test_surrounded_by_prose(self):
        text = '好的，结果如下：\n{"translation": "你好"}\n希望有帮助。'
        self.assertEqual(api._extract_json_text(text), '{"translation": "你好"}')

    def test_returns_none_without_json(self):
        self.assertIsNone(api._extract_json_text("完全不是 JSON"))
        self.assertIsNone(api._extract_json_text(""))


class ParseRepairTests(unittest.TestCase):
    def test_direct_parse_does_not_add_note(self):
        notes: list[str] = []
        result = api._parse_json_with_repair(
            '{"translation": "你好", "explanation": "说明"}', notes
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.translation, "你好")
        self.assertEqual(result.explanation, "说明")
        self.assertEqual(notes, [])

    def test_repairs_without_extra_request(self):
        notes: list[str] = []
        result = api._parse_json_with_repair('```json\n{"translation": "你好"}\n```', notes)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.translation, "你好")
        self.assertTrue(notes, "自动提取后应该留下一条说明")

    def test_unrepairable_returns_none(self):
        notes: list[str] = []
        self.assertIsNone(api._parse_json_with_repair("模型什么 JSON 都没给", notes))


class DegradeTests(unittest.TestCase):
    @staticmethod
    def _payload() -> dict:
        return {
            "model": "m",
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "content": [
                        {"type": "image_url", "image_url": {"url": "x", "detail": "original"}}
                    ]
                }
            ],
        }

    def test_removes_vendor_specific_keys(self):
        degraded = api._degrade(self._payload())
        self.assertNotIn("thinking", degraded)
        self.assertEqual(
            degraded["messages"][0]["content"][0]["image_url"]["detail"], "high"
        )

    def test_does_not_mutate_original_payload(self):
        payload = self._payload()
        api._degrade(payload)
        self.assertIn("thinking", payload)
        self.assertEqual(
            payload["messages"][0]["content"][0]["image_url"]["detail"], "original"
        )


class UnsupportedParamTests(unittest.TestCase):
    def test_detects_english_message(self):
        self.assertTrue(
            api._looks_like_unsupported_param("Unrecognized request argument: thinking")
        )

    def test_detects_chinese_message(self):
        self.assertTrue(api._looks_like_unsupported_param("不支持参数 thinking"))

    def test_ignores_unrelated_error(self):
        self.assertFalse(api._looks_like_unsupported_param("image too large"))


class PlainParseTests(unittest.TestCase):
    def test_both_sections(self):
        result = api._parse_plain("===翻译===\n你好\n===解释===\n说明")
        self.assertEqual(result.translation, "你好")
        self.assertEqual(result.explanation, "说明")

    def test_translation_only(self):
        result = api._parse_plain("===翻译===\n只有译文")
        self.assertEqual(result.translation, "只有译文")
        self.assertEqual(result.explanation, "")

    def test_no_markers_falls_back_to_whole_text(self):
        self.assertEqual(api._parse_plain("随便一段话").translation, "随便一段话")


class FriendlyErrorTests(unittest.TestCase):
    def test_known_code_is_explained(self):
        self.assertIn("API Key", api._friendly_http_error(401, ""))

    def test_unknown_code_is_still_reported(self):
        self.assertIn("418", api._friendly_http_error(418, ""))

    def test_detail_is_appended(self):
        self.assertIn("detail here", api._friendly_http_error(400, "detail here"))


if __name__ == "__main__":
    unittest.main()
