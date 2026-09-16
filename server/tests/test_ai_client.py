import json

import httpx
import pytest

from app.config import ModelConfig, Settings
from app.infra.ai.client import AiClient, AiRequestError
from app.infra.ai.json_collect import extract_json
from app.infra.ai.logging import log_ai_failure


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('\'\'\'{"a": 1}\'\'\'', {"a": 1}),
        ('前言 {"a": {"b": 2}} 后语', {"a": {"b": 2}}),
        ("没有 JSON", None),
        ("", None),
        ('{"a": 1', None),
    ],
)
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_log_ai_failure_prints_single_line(capsys):
    log_ai_failure("主体印章图片识别", {"type": "http-error", "status": 429, "count": 7})

    out = capsys.readouterr().out.strip().splitlines()

    assert len(out) == 1
    assert out[0].startswith("ai_failure scope=主体印章图片识别")
    assert "type=http-error" in out[0]
    assert "status=429" in out[0]


def make_settings():
    return Settings(
        _env_file=None,
        text_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                               model_name="text-model"),
        vision_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                                 model_name="vision-model"),
        ai_max_concurrency=2,
    )


def make_client(handler):
    return AiClient(make_settings(), transport=httpx.MockTransport(handler))


async def test_chat_posts_openai_compatible_payload_and_returns_content():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "结果文本"}}],
        })

    client = make_client(handler)
    text = await client.chat([{"role": "user", "content": "你好"}])
    await client.aclose()

    assert text == "结果文本"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-a"
    assert captured["body"]["model"] == "text-model"
    assert captured["body"]["messages"] == [{"role": "user", "content": "你好"}]


async def test_chat_uses_vision_model_when_requested():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(handler)
    await client.chat([{"role": "user", "content": "看图"}], use_vision=True)
    await client.aclose()

    assert captured["body"]["model"] == "vision-model"


async def test_http_error_raises_ai_request_error_with_status():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    assert info.value.status_code == 429
    assert "429" in str(info.value)


async def test_collect_json_response_parses_content():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '```json\n{"findings": [1, 2]}\n```'}}],
        })

    client = make_client(handler)
    payload = await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert payload == {"findings": [1, 2]}


async def test_collect_json_response_repairs_invalid_json():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "这不是 JSON"}}],
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
        })

    client = make_client(handler)
    payload = await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert payload == {"ok": True}
    assert calls["count"] == 2


async def test_collect_json_response_raises_when_repair_also_fails():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "仍然不是 JSON"}}],
        })

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.collect_json_response([{"role": "user", "content": "x"}])
    await client.aclose()

    assert "JSON" in str(info.value)


async def test_missing_base_url_raises():
    settings = Settings(_env_file=None, text_model=ModelConfig())
    client = AiClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    assert "Base URL" in str(info.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"choices": None}, id="choices-null"),
        pytest.param({"foo": 1}, id="no-choices-key"),
    ],
)
async def test_chat_shape_error_raises_ai_request_error_and_logs(body, capsys):
    # R18: 任意畸形响应结构都必须以 AiRequestError 结束，且必须打到
    # 常开诊断通道（log_ai_failure，打印 type=shape-error）。
    # {"choices": None} -> None[0] 抛 TypeError（原 except 元组遗漏，会漏出去）；
    # {"foo": 1}        -> ["choices"] 抛 KeyError。
    def handler(request):
        return httpx.Response(200, json=body)

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    # 必须是 AiRequestError，而不是裸的 TypeError / KeyError / IndexError / ValueError 逃逸
    assert not isinstance(info.value, (TypeError, KeyError, IndexError, ValueError))
    assert "模型响应结构" in str(info.value)

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "type=shape-error" in out[0]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param([{"type": "text", "text": "..."}], id="parts-list"),
        pytest.param({"a": 1}, id="dict"),
    ],
)
async def test_chat_rejects_non_str_content_and_logs(content, capsys):
    # R21: chat 的返回契约是 str，且每个失败都必须经 log_ai_failure 落地。
    # 真值非 str 的 content 两条都违反：修复前它被 `or ""` 原样返回，随后在
    # collect_json_response -> extract_json -> _strip_wrappers 的 text.strip()
    # 抛裸 AttributeError 逃逸，且常开日志缺失。
    # parts-list 是真实形状：多段内容网关/兼容代理把 content 返回成 parts 数组。
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = make_client(handler)
    with pytest.raises(AiRequestError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    # 必须是 AiRequestError，而不是裸的 AttributeError / TypeError 等逃逸
    assert not isinstance(
        info.value, (AttributeError, TypeError, KeyError, IndexError, ValueError)
    )
    assert "模型响应结构" in str(info.value)

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "type=shape-error" in out[0]


async def test_chat_returns_empty_string_when_content_is_null():
    # R21: content 为 null 是合法形状（tool calls、被过滤的响应），
    # OpenAI 兼容 API 会这样返回，必须仍然得到 ""，不得被过严的修复改成错误。
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    client = make_client(handler)
    text = await client.chat([{"role": "user", "content": "x"}])
    await client.aclose()

    assert text == ""


async def test_vision_request_uses_vision_timeout_not_text_timeout():
    # R19: 每个模型必须用自己的 timeout_seconds。
    # 注意：httpx.MockTransport 不施加真实 socket 超时（time.sleep 不会触发超时），
    # 因此这里用确定性方式——读取 httpx 贴在请求上的有效 timeout 扩展——来证明
    # 哪一份 timeout_seconds 被应用到了请求上。这正是裁定允许「更确定的判定方式」。
    settings = Settings(
        _env_file=None,
        text_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                               model_name="text-model", timeout_seconds=0.01),
        vision_model=ModelConfig(base_url="https://api.example.com/v1", api_key="sk-a",
                                 model_name="vision-model", timeout_seconds=5.0),
        ai_max_concurrency=2,
    )

    captured = []

    def handler(request):
        timeout = request.extensions.get("timeout")
        captured.append(timeout["connect"] if timeout else None)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = AiClient(settings, transport=httpx.MockTransport(handler))

    await client.chat([{"role": "user", "content": "x"}], use_vision=False)  # text timeout
    await client.chat([{"role": "user", "content": "x"}], use_vision=True)   # vision timeout
    await client.aclose()

    # 文本请求用 text_model.timeout_seconds，视觉请求用 vision_model.timeout_seconds
    assert captured[0] == 0.01
    assert captured[1] == 5.0
