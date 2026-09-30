"""DeepSeek 视觉模型调用层。

只依赖标准库（urllib），不引入 openai SDK，减小打包体积。

关于多厂商兼容：
协议走的是 OpenAI 兼容的 ``/chat/completions``，所以只要目标服务支持图片输入就能接。
但 DeepSeek 有两个专有参数（``thinking`` 和 ``detail: "original"``），别家不认。
请求被拒时会自动去掉它们重试一次，用户换厂商只需要改 Base URL 和模型名。

模型说明：DeepSeek 目前只有 ``deepseek-flash`` 支持图片输入，
``deepseek-v4-pro`` 不支持视觉，改模型名时请注意。
"""

from __future__ import annotations

import base64
import copy
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: 客户端先做一次保守缩放，避免整屏或多屏截图上传几十 MB。
#: 长边超过这个值才缩放，正常框选的小图不会被处理。
MAX_EDGE = 2000

#: 这些参数只有 DeepSeek 认，换厂商被拒时会自动去掉。
VENDOR_SPECIFIC_KEYS = ("thinking",)

#: 服务端报错里出现这些词，就认定是"不认识某个参数"，触发降级重试。
UNSUPPORTED_HINTS = (
    "unrecognized",
    "unknown",
    "unsupported",
    "not supported",
    "extra",
    "unexpected",
    "invalid_request",
    "不支持",
    "未知参数",
    "无效参数",
)

SYSTEM_PROMPT = """你是一个屏幕内容翻译与代码解释助手。用户会给你一张屏幕截图。

你的任务：
1. 识别截图中的英文文本，翻译成简体中文。
2. 如果截图中包含代码（含配置文件、命令行、SQL、正则等技术内容），简要解释它。

必须输出 json，结构如下：
{
  "translation": "……",
  "explanation": "……"
}

translation 规则：
- 逐段翻译，保留原文的换行与层次结构。
- 函数名、变量名、类名、命令、路径、报错原文、版本号一律不翻译，原样保留。
- 内容是代码时，只翻译其中的注释和字符串里的自然语言，代码本身原样保留。
- 截图中没有可翻译的英文文本时，translation 填"（未识别到可翻译的文本）"。

explanation 规则（低强度、克制）：
- 用 2-3 条要点说明这段内容整体在做什么，每条一句话。
- 只在有坑或反直觉的地方补一句提醒（空值处理、边界条件、副作用、性能陷阱等）。
- 不逐行讲解，不复述代码字面意思，不写与内容无关的客套话。
- 截图里没有代码或技术内容时，explanation 填空字符串。

只输出 json，不要输出其他任何文字。"""

PLAIN_PROMPT = """你是一个屏幕内容翻译与代码解释助手。用户会给你一张屏幕截图。

请识别截图中的英文并翻译成简体中文，如果内容涉及代码则简要解释。

严格按下面的格式输出，不要输出其他任何内容：

===翻译===
（译文；函数名、变量名、命令、路径、报错原文保持原样）
===解释===
（2-3 条要点，说明整体在做什么，只在有坑的地方提醒一句；没有代码就留空）"""

TRANSLATE_ONLY_RULE = """
补充：本次不需要解释代码，explanation 一律填空字符串。"""


class ApiError(Exception):
    """调用失败，message 已经是可直接展示给用户的中文描述。"""


class ApiKeyMissing(ApiError):
    pass


@dataclass
class Result:
    translation: str = ""
    explanation: str = ""
    raw: str = ""
    model: str = ""
    elapsed: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    #: 兼容性降级之类的提示，会显示在小窗底部
    notice: str = ""


def _prepare_png(image_bytes: bytes) -> bytes:
    """长边过大时等比缩小；其余情况原样返回，避免损失小字清晰度。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 是硬依赖
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception:
        return image_bytes

    if max(img.size) <= MAX_EDGE:
        return image_bytes

    scale = MAX_EDGE / max(img.size)
    new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    resized = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _build_payload(
    image_bytes: bytes,
    cfg: dict[str, Any],
    *,
    want_explanation: bool,
    json_mode: bool,
) -> dict[str, Any]:
    b64 = base64.b64encode(_prepare_png(image_bytes)).decode("ascii")
    system = SYSTEM_PROMPT if json_mode else PLAIN_PROMPT
    if not want_explanation and json_mode:
        system += TRANSLATE_ONLY_RULE

    payload: dict[str, Any] = {
        "model": cfg.get("model") or "deepseek-flash",
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请翻译并解释这张截图。"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": cfg.get("detail") or "original",
                        },
                    },
                ],
            },
        ],
        "max_tokens": 4000,
        "stream": False,
        # 翻译+解释属于直读任务，关掉思考模式可以明显降低延迟。
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _looks_like_unsupported_param(message: str) -> bool:
    low = message.lower()
    return any(hint in low for hint in UNSUPPORTED_HINTS)


def _degrade(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉别家厂商可能不认的参数，得到一份更通用的请求体。"""
    slim = copy.deepcopy(payload)
    for key in VENDOR_SPECIFIC_KEYS:
        slim.pop(key, None)
    # "original" 是 DeepSeek 独有的 detail 取值，OpenAI 只认 low/high/auto
    for message in slim.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                image_url = part.get("image_url")
                if isinstance(image_url, dict) and image_url.get("detail") == "original":
                    image_url["detail"] = "high"
    return slim


def _post(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body)
            detail = str(parsed.get("error", {}).get("message") or parsed)[:300]
        except Exception:
            detail = ""
        raise ApiError(_friendly_http_error(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ApiError(f"网络连接失败：{reason}\n（如果本机需要代理才能访问，请先设置好系统代理）") from exc
    except TimeoutError as exc:
        raise ApiError(f"请求超时（超过 {timeout:.0f} 秒），可能是网络慢或图片过大。") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(f"服务端返回了无法解析的内容：{raw[:300]}") from exc


def _post_with_fallback(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
    notes: list[str],
) -> dict[str, Any]:
    """先按完整参数请求；若被判定为"不认识某个参数"，去掉专有参数再试一次。

    这样换到 OpenAI / 通义 / 智谱 等厂商时，用户只需要改 Base URL 和模型名。
    """
    try:
        return _post(url, payload, api_key, timeout)
    except ApiError as exc:
        message = str(exc)
        if not _looks_like_unsupported_param(message):
            raise
        notes.append("该服务不接受 DeepSeek 专有参数，已自动降级重试")
        return _post(url, _degrade(payload), api_key, timeout)


def _friendly_http_error(code: int, detail: str) -> str:
    base = {
        400: "请求被拒绝（400），通常是参数或图片格式问题",
        401: "API Key 无效或已过期（401），请在设置里重新填写",
        402: "账户余额不足（402），请到对应平台充值",
        403: "没有访问权限（403）",
        404: "接口地址不存在（404），请检查 Base URL 是否填错",
        422: "请求参数有误（422）",
        429: "请求过于频繁或超出限额（429），稍后再试",
        500: "服务端错误（500），稍后重试",
        502: "网关错误（502），稍后重试",
        503: "服务暂时不可用（503），稍后重试",
    }.get(code, f"请求失败（HTTP {code}）")
    return f"{base}\n{detail}" if detail else base


def _extract_content(resp: dict[str, Any]) -> str:
    try:
        choices = resp.get("choices") or []
        if not choices:
            raise KeyError("choices")
        content = choices[0].get("message", {}).get("content")
    except (KeyError, AttributeError, IndexError, TypeError) as exc:
        raise ApiError(f"服务端返回结构异常：{json.dumps(resp, ensure_ascii=False)[:300]}") from exc
    return (content or "").strip()


def _parse_plain(text: str) -> Result:
    """解析纯文本模式的分隔符输出。"""
    result = Result(raw=text)
    if "===翻译===" in text:
        rest = text.split("===翻译===", 1)[1]
        if "===解释===" in rest:
            translation, explanation = rest.split("===解释===", 1)
            result.translation = translation.strip()
            result.explanation = explanation.strip()
        else:
            result.translation = rest.strip()
    else:
        result.translation = text.strip()
    return result


def _parse_json_mode(text: str) -> Result:
    result = Result(raw=text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    result.translation = str(data.get("translation") or "").strip()
    result.explanation = str(data.get("explanation") or "").strip()
    return result


def _extract_json_text(text: str) -> str | None:
    """从可能带 markdown 围栏或前后废话的回复里抠出 JSON 对象。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()
        if stripped[:4].lower() == "json":
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _parse_json_with_repair(text: str, notes: list[str]) -> Result | None:
    """先直接解析，失败再试着把 JSON 抠出来。

    抠得出来就不重发请求了 —— 重发会把整张图再计一次 token，
    为了一个格式问题付双份钱不划算。
    """
    try:
        return _parse_json_mode(text)
    except (ValueError, json.JSONDecodeError):
        pass
    candidate = _extract_json_text(text)
    if candidate is None:
        return None
    try:
        result = _parse_json_mode(candidate)
    except (ValueError, json.JSONDecodeError):
        return None
    notes.append("模型输出带了额外包裹，已自动提取 JSON")
    return result


def translate_image(
    image_bytes: bytes,
    cfg: dict[str, Any],
    *,
    want_explanation: bool = True,
) -> Result:
    """把截图交给模型，返回翻译与（可选的）代码解释。

    失败时抛 :class:`ApiError`，message 可直接展示给用户。
    """
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise ApiKeyMissing("还没有填写 API Key，请先在设置里填写并保存。")

    base_url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = f"{base_url}/chat/completions"
    timeout = float(cfg.get("timeout") or 90)

    started = time.monotonic()
    errors: list[str] = []
    notes: list[str] = []

    def finish(result: Result, resp: dict[str, Any], model: str) -> Result:
        result.model = str(resp.get("model") or model)
        result.usage = resp.get("usage") or {}
        result.elapsed = time.monotonic() - started
        result.notice = "；".join(notes)
        return result

    # 第一轮：JSON 模式，解析最可靠。
    try:
        payload = _build_payload(
            image_bytes, cfg, want_explanation=want_explanation, json_mode=True
        )
        resp = _post_with_fallback(url, payload, api_key, timeout, notes)
        text = _extract_content(resp)
        if text:
            parsed = _parse_json_with_repair(text, notes)
            if parsed is None:
                errors.append("JSON 解析失败")
            else:
                result = finish(parsed, resp, payload["model"])
                if result.translation:
                    return result
                errors.append("模型返回的 translation 为空")
        else:
            # 官方文档明确提示 JSON 模式偶尔会返回空 content。
            errors.append("JSON 模式返回了空内容")
    except ApiError:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"JSON 解析失败：{exc}")

    # 第二轮：退回到分隔符文本模式。
    try:
        payload = _build_payload(
            image_bytes, cfg, want_explanation=want_explanation, json_mode=False
        )
        resp = _post_with_fallback(url, payload, api_key, timeout, notes)
        text = _extract_content(resp)
        if text:
            result = finish(_parse_plain(text), resp, payload["model"])
            if not want_explanation:
                result.explanation = ""
            if result.translation:
                return result
            errors.append("文本模式也没有返回译文")
        else:
            errors.append("文本模式返回了空内容")
    except ApiError:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"文本解析失败：{exc}")

    raise ApiError("模型没有返回有效结果：\n" + "\n".join(f"- {e}" for e in errors))


def test_connection(cfg: dict[str, Any]) -> str:
    """设置界面里的"测试连接"：发一个极小的文本请求，验证 key 和网络。"""
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise ApiKeyMissing("还没有填写 API Key。")

    base_url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": cfg.get("model") or "deepseek-flash",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    notes: list[str] = []
    resp = _post_with_fallback(url, payload, api_key, float(cfg.get("timeout") or 90), notes)
    model = resp.get("model") or payload["model"]
    usage = resp.get("usage") or {}
    tokens = usage.get("total_tokens", "?")
    suffix = f"（{'；'.join(notes)}）" if notes else ""
    return f"连接正常，模型 {model} 已响应（本次消耗 {tokens} tokens）{suffix}"
