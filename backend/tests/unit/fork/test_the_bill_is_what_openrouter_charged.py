"""0.0.544.1 — the Cost page shows what OpenRouter actually charged.

Measured on production (2026-08-21): 1,420 calls, 71.8M tokens, $0.00 total.
Every llm_models row had NULL rates, the static catalog knows only native
model names (no OpenRouter slugs), so `rate x tokens` was `None x tokens` and
the recorder stamped 0.0 into every record — permanently, because cost is
computed at write time.

The fix stops calculating where the provider will simply TELL us: requests to
an OpenRouter base_url ask for usage accounting (`usage: {include: true}`),
the response's `usage.cost` (actual charged USD, cache discounts included)
rides through LLMUsage.actual_cost_usd, and the recorder stores it as the
record's total. Rate math stays as the fallback for every other provider.

Probe evidence (run live against prod's own key, 2026-08-21): a real call
returned `"cost": 1.38e-06` inline — no rate table involved.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.ai.llm.types import LLMUsage
from app.services.llm_usage_recorder import LLMUsageRecorderService


class _StubDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _model(in_rate, out_rate):
    return SimpleNamespace(
        id="m1",
        model_id="x-ai/grok-4.5",
        provider=SimpleNamespace(provider_type="custom"),
        organization_id="org1",
        input_cost_per_million_tokens_usd=in_rate,
        output_cost_per_million_tokens_usd=out_rate,
        get_input_cost_rate=lambda: float(in_rate) if in_rate is not None else None,
        get_output_cost_rate=lambda: float(out_rate) if out_rate is not None else None,
    )


def _record(**kw):
    db = _StubDb()
    rec = asyncio.run(
        LLMUsageRecorderService(db).record(
            scope="test", scope_ref_id=None, **kw
        )
    )
    return rec


class TestTheActualChargeWins:
    def test_no_rates_plus_actual_cost_is_not_zero(self):
        """★The measured production defect: NULL rates must no longer produce
        a $0 record when the provider reported the real charge."""
        rec = _record(
            llm_model=_model(None, None),
            prompt_tokens=1000, completion_tokens=100,
            actual_cost_usd=0.0123,
        )
        assert float(rec.total_cost_usd) == pytest.approx(0.0123)
        # And neither side is a fabricated zero — split by token share.
        assert float(rec.input_cost_usd) > 0
        assert float(rec.output_cost_usd) > 0
        assert float(rec.input_cost_usd) + float(rec.output_cost_usd) == pytest.approx(0.0123)

    def test_with_rates_the_split_is_kept_but_the_total_is_the_charge(self):
        """Rates say $2/$6 per 1M; the provider charged less (cache discount).
        The stored total must be the charge, in/out scaled proportionally."""
        rec = _record(
            llm_model=_model(2.0, 6.0),
            prompt_tokens=1_000_000, completion_tokens=100_000,
            actual_cost_usd=1.3,  # rate math would say 2.0 + 0.6 = 2.6
        )
        assert float(rec.total_cost_usd) == pytest.approx(1.3)
        assert float(rec.input_cost_usd) == pytest.approx(1.0)   # 2.0 * 0.5
        assert float(rec.output_cost_usd) == pytest.approx(0.3)  # 0.6 * 0.5

    def test_a_free_model_stores_a_real_zero(self):
        """0.0 is a price, not an absence — free-tier calls must store it."""
        rec = _record(
            llm_model=_model(2.0, 6.0),
            prompt_tokens=1000, completion_tokens=100,
            actual_cost_usd=0.0,
        )
        assert float(rec.total_cost_usd) == 0.0

    def test_without_a_reported_charge_rate_math_still_runs(self):
        """The fallback for every non-OpenRouter provider is unchanged."""
        rec = _record(
            llm_model=_model(2.0, 6.0),
            prompt_tokens=1_000_000, completion_tokens=100_000,
        )
        assert float(rec.total_cost_usd) == pytest.approx(2.6)


class TestTheUsageObjectCarriesTheCharge:
    def test_the_field_defaults_to_not_reported(self):
        assert LLMUsage().actual_cost_usd is None

    def test_extract_usage_reads_cost_from_a_dict(self):
        from app.ai.llm.clients.openai_client import OpenAi
        u = OpenAi._extract_usage({
            "prompt_tokens": 6, "completion_tokens": 5, "cost": 1.38e-06,
        })
        assert u.actual_cost_usd == pytest.approx(1.38e-06)

    def test_extract_usage_reads_cost_from_an_sdk_object(self):
        from app.ai.llm.clients.openai_client import OpenAi
        raw = SimpleNamespace(
            prompt_tokens=6, completion_tokens=5,
            prompt_tokens_details=None, cost=2.5e-06,
        )
        assert OpenAi._extract_usage(raw).actual_cost_usd == pytest.approx(2.5e-06)

    def test_a_response_without_cost_stays_not_reported(self):
        from app.ai.llm.clients.openai_client import OpenAi
        u = OpenAi._extract_usage({"prompt_tokens": 6, "completion_tokens": 5})
        assert u.actual_cost_usd is None


class TestOnlyOpenRouterIsAskedForAccounting:
    """Other OpenAI-compatible gateways (LiteLLM, vLLM, Ollama) may 400 on an
    unknown extra_body — the request flag must key off the base_url."""

    def _client(self, base_url):
        from app.ai.llm.clients.openai_client import OpenAi
        c = OpenAi.__new__(OpenAi)  # the documented test-double pattern
        c._reports_actual_cost = "openrouter" in base_url.lower()
        return c

    def test_openrouter_requests_carry_the_flag(self):
        params = self._client("https://openrouter.ai/api/v1")._build_chat_params(
            model_id="x-ai/grok-4.5", prompt="hi"
        )
        assert params.get("extra_body") == {"usage": {"include": True}}

    def test_other_gateways_do_not(self):
        params = self._client("http://localhost:4000/v1")._build_chat_params(
            model_id="gpt-4o", prompt="hi"
        )
        assert "extra_body" not in params

    def test_a_double_built_without_init_does_not_crash(self):
        """Class-level default covers __new__-built doubles, same as
        `temperature` — building params must not AttributeError."""
        from app.ai.llm.clients.openai_client import OpenAi
        c = OpenAi.__new__(OpenAi)
        params = c._build_chat_params(model_id="m", prompt="p")
        assert "extra_body" not in params
