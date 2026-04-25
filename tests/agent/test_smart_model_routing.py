from agent.smart_model_routing import choose_cheap_model_route, choose_high_value_model_route


_BASE_CONFIG = {
    "enabled": True,
    "cheap_model": {
        "provider": "openrouter",
        "model": "google/gemini-2.5-flash",
    },
    "high_value_model": {
        "provider": "custom",
        "model": "claude-opus-4-7",
        "base_url": "https://api.ccode.vip/v1",
        "api_key_env": "HERMES_OPUS47_API_KEY",
        "keywords": ["按模板", "报价单", "承诺书", "招标", "research"],
    },
}


def test_returns_none_when_disabled():
    cfg = {**_BASE_CONFIG, "enabled": False}
    assert choose_cheap_model_route("what time is it in tokyo?", cfg) is None


def test_routes_short_simple_prompt():
    result = choose_cheap_model_route("what time is it in tokyo?", _BASE_CONFIG)
    assert result is not None
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-2.5-flash"
    assert result["routing_reason"] == "simple_turn"


def test_routes_high_value_template_prompt():
    result = choose_high_value_model_route("按模板生成一份信用承诺书", _BASE_CONFIG)
    assert result is not None
    assert result["provider"] == "custom"
    assert result["model"] == "claude-opus-4-7"
    assert result["routing_reason"] == "high_value_turn"


def test_resolve_turn_route_prefers_high_value_route(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "custom",
            "base_url": "https://api.ccode.vip/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-opus",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
    )
    result = resolve_turn_route(
        "按模板生成一份报价单样稿",
        _BASE_CONFIG,
        {
            "model": "gpt-5.4",
            "provider": "openai-codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses",
            "api_key": "sk-primary",
        },
    )
    assert result["model"] == "claude-opus-4-7"
    assert result["runtime"]["provider"] == "custom"
    assert result["label"] == "smart route → claude-opus-4-7 (custom)"


def test_skips_long_prompt():
    prompt = "please summarize this carefully " * 20
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_code_like_prompt():
    prompt = "debug this traceback: ```python\nraise ValueError('bad')\n```"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_tool_heavy_prompt_keywords():
    prompt = "implement a patch for this docker error"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_resolve_turn_route_falls_back_to_primary_when_route_runtime_cannot_be_resolved(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad route")),
    )
    result = resolve_turn_route(
        "what time is it in tokyo?",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )
    assert result["model"] == "anthropic/claude-sonnet-4"
    assert result["runtime"]["provider"] == "openrouter"
    assert result["label"] is None
