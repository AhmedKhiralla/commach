"""AI providers (any OpenAI-compatible chat API; Qwen on Alibaba Cloud Model Studio by default).

Keys are only ever read from server environment variables; they are never stored in the database or
sent to the browser. Each company picks a provider in Company settings.

Every AI answer in ComMach is a draft: it is stored as a suggestion (model, prompt version, hash of the
raw answer) and only becomes data when a person accepts or corrects it. See app/ai_tasks.py.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("commach.ai")

MAX_IMG_BYTES = 3_800_000          # stay under the 4 MB base64 image limit most providers apply


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    key_env: str
    vision_model: str
    text_model: str
    json_mode: bool = True          # send response_format={"type": "json_object"}

    @property
    def key(self) -> str | None:
        return os.environ.get(self.key_env) or None


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def providers() -> dict[str, Provider]:
    """Built-in providers. Base URLs and models can be changed with environment variables
    without a new release, because providers rename models often."""
    p = [
        Provider("qwen", "Qwen (QwenCloud / Alibaba Cloud Model Studio)",
                 _env("QWEN_BASE_URL", os.environ.get("AI_BASE_URL") or "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"),
                 "QWEN_API_KEY" if os.environ.get("QWEN_API_KEY") else "AI_API_KEY",
                 _env("QWEN_VISION_MODEL", "qwen3-vl-plus"), _env("QWEN_TEXT_MODEL", "qwen-plus")),
        Provider("groq", "Groq",
                 _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"), "GROQ_API_KEY",
                 _env("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
                 _env("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")),
        Provider("anthropic", "Claude (Anthropic)",
                 _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"), "ANTHROPIC_API_KEY",
                 _env("ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5"), _env("ANTHROPIC_TEXT_MODEL", "claude-haiku-5-5"),
                 json_mode=False),
        Provider("gemini", "Gemini (Google)",
                 _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"), "GEMINI_API_KEY",
                 _env("GEMINI_VISION_MODEL", "gemini-2.5-flash"), _env("GEMINI_TEXT_MODEL", "gemini-2.5-flash")),
        Provider("custom", os.environ.get("CUSTOM_AI_LABEL", "Custom OpenAI-compatible"),
                 _env("CUSTOM_AI_BASE_URL", "http://localhost:1/v1"), "CUSTOM_AI_API_KEY",
                 _env("CUSTOM_AI_VISION_MODEL", "vision"), _env("CUSTOM_AI_TEXT_MODEL", "text")),
    ]
    return {x.name: x for x in p}


def available() -> list[dict]:
    return [{"name": p.name, "label": p.label, "vision_model": p.vision_model, "text_model": p.text_model}
            for p in providers().values() if p.key]


def pick(name: str | None) -> Provider | None:
    ps = providers()
    if name and name in ps and ps[name].key:
        return ps[name]
    for p in ps.values():           # first configured provider
        if p.key:
            return p
    return None


def status(features: dict | None) -> dict:
    """Shown in the session payload: which AI functions this company can use."""
    from ..defaults import effective_features
    f = effective_features(features)
    on = bool(available())
    keys = ("ai_nameplate", "ai_import", "ai_certificate", "ai_manual", "ai_impact", "ai_assistant")
    return {"configured": on, **{k[3:]: on and f.get(k, True) for k in keys}}


class AIUnavailable(Exception):
    pass


class AIConfigError(AIUnavailable):
    """The provider refused the request itself (wrong model name, bad key, no access). Retrying cannot help,
    so a run stops calling this provider after the first one."""


class AIModelMissing(AIConfigError):
    """The account or region has no model of that name."""


# When a model name does not exist for the account or region (providers rename models often, and QwenCloud and
# Alibaba Cloud Model Studio use different names), ComMach tries these in order and remembers the one that works.
FALLBACKS = {
    ("qwen", "vision"): ["qwen3-vl-plus", "qwen3.8-flash", "qwen3-vl-flash", "qwen-vl-max", "qwen-vl-plus", "qwen3.7-plus"],
    ("qwen", "text"): ["qwen-plus", "qwen3.8-flash", "qwen3.7-plus", "qwen-flash", "qwen-max", "qwen3.8-max"],
}
_resolved: dict[tuple[str, str], str] = {}


def _http_error_text(provider: "Provider", model: str, code: int, raw: bytes) -> str:
    msg = ""
    try:
        err = json.loads(raw.decode("utf-8", "replace"))
        e = err.get("error", err) if isinstance(err, dict) else {}
        msg = (e.get("message") if isinstance(e, dict) else str(e)) or ""
    except ValueError:
        msg = raw[:200].decode("utf-8", "replace")
    low = msg.lower()
    if _missing(code, low):
        env = provider.name.upper() if provider.name != "custom" else "CUSTOM_AI"
        return (f"{provider.label} has no model named “{model}” for this API key and region (HTTP {code}: {msg.strip()}). "
                f"Set {env}_VISION_MODEL / {env}_TEXT_MODEL on the server to a model your account can use.")
    if code in (401, 403):
        return f"{provider.label} refused the API key (HTTP {code}: {msg.strip()}). Check the key and its region."
    return f"{provider.label} returned HTTP {code}: {msg.strip()}"


def _missing(code: int, low: str) -> bool:
    return code == 404 or ("model" in low and ("not exist" in low or "not found" in low or "does not exist" in low
                                               or "not support" in low or "invalid model" in low))


def img_data_url(img) -> str:
    q = 90
    while True:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=q)
        if buf.tell() <= MAX_IMG_BYTES or q < 40:
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        q -= 12


def _parse_json(s: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (s or "").strip(), flags=re.M)
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0) if m else s)


def chat(provider: Provider, model: str, messages: list, max_tokens: int = 1200, timeout: int = 120) -> dict:
    """One JSON-returning chat call. If the model does not exist for this account or region, the known alternatives
    are tried and the working one is remembered for the next calls."""
    role = "vision" if model == provider.vision_model else "text"
    key = (provider.name, model)
    first = _resolved.get(key, model)
    tried, err = [], None
    for m in [first] + [x for x in [model] + FALLBACKS.get((provider.name, role), []) if x != first]:
        if m in tried:
            continue
        tried.append(m)
        try:
            out = _chat(provider, m, messages, max_tokens, timeout)
            if m != model and _resolved.get(key) != m:
                log.warning("%s: model %s is not available; using %s instead (set %s_%s_MODEL to make it permanent).",
                            provider.label, model, m, provider.name.upper(), role.upper())
            _resolved[key] = m
            return out
        except AIModelMissing as e:
            err = e
            continue
    raise AIConfigError(f"{err} Tried: {', '.join(tried)}.") if err else AIUnavailable("no answer")


def _chat(provider: Provider, model: str, messages: list, max_tokens: int, timeout: int) -> dict:
    """One JSON-returning chat call with retries on rate limits and server errors."""
    if not provider.key:
        raise AIUnavailable(f"{provider.label} is not configured on the server")
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if provider.json_mode:
        body["response_format"] = {"type": "json_object"}
    data = json.dumps(body).encode()
    last = ""
    for attempt in range(5):
        req = urllib.request.Request(provider.base_url.rstrip("/") + "/chat/completions", data=data, method="POST",
                                     headers={"Authorization": f"Bearer {provider.key}", "Content-Type": "application/json",
                                              "x-api-key": provider.key, "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read())
            content = out["choices"][0]["message"].get("content") or ""
            if isinstance(content, list):          # some providers return content parts
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            parsed = _parse_json(content)
            return {"model": out.get("model") or model, "data": parsed, "usage": out.get("usage") or {}}
        except urllib.error.HTTPError as e:
            raw = e.read()[:2000]
            last = _http_error_text(provider, model, e.code, raw)
            if _missing(e.code, raw.decode("utf-8", "replace").lower()) and e.code not in (401, 403):
                raise AIModelMissing(last)
            if e.code in (400, 401, 403, 404, 422):
                raise AIConfigError(last)
            if e.code in (408, 409, 429, 500, 502, 503, 504, 529):
                wait = e.headers.get("retry-after")
                time.sleep(min(30.0, float(wait) if wait and wait.replace('.', '', 1).isdigit() else 2 ** attempt * 2))
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(e)
            time.sleep(2 ** attempt)
        except (ValueError, KeyError, IndexError) as e:
            last = f"Unreadable answer: {e}"
            if attempt >= 1:
                break
    raise AIUnavailable(last or "no answer")


def test(provider: Provider) -> list[dict]:
    """One tiny call per model so an administrator sees at once whether the key, region and model names work."""
    from PIL import Image
    out = []
    img = Image.new("RGB", (64, 24), "white")
    for role, model, content in (
            ("text", provider.text_model, 'Reply with the JSON {"ok": true}.'),
            ("vision", provider.vision_model, [{"type": "text", "text": 'Reply with the JSON {"ok": true}.'},
                                               {"type": "image_url", "image_url": {"url": img_data_url(img)}}])):
        t0 = time.time()
        try:
            r = chat(provider, model, [{"role": "user", "content": content}], max_tokens=20, timeout=40)
            out.append({"role": role, "model": r["model"], "ok": True, "ms": int((time.time() - t0) * 1000)})
        except AIUnavailable as e:
            out.append({"role": role, "model": model, "ok": False, "error": str(e)})
    return out
