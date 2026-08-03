"""Unit tests for the Coder's per-prompt current-time injection.

The planner prompts already carry a "Time:" line rendered by
app.ai.agents.planner.clock in the org's timezone/week-start settings. The
Coder's three codegen prompts (data_model_to_code, generate_code,
generate_inspection_code) must carry the same clock so relative date phrases
("today", "last week") resolve consistently between planning and the
generated code.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ai.agents.coder.coder import Coder
from app.ai.llm.types import TextDeltaEvent
from app.ai.schemas.codegen import CodeGenContext
from app.schemas.organization_settings_schema import OrganizationSettingsConfig


class _StubSettings:
    """Mimics the OrganizationSettings DB model's get_config lookup."""

    def __init__(self, config=None):
        self._config = config or {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)


_STUB_CODE = "def generate_df(ds_clients, excel_files):\n    return df"


class _StubLLM:
    """Captures the prompt text instead of calling a provider."""

    def __init__(self):
        self.prompts = []

    def inference(self, text, **kwargs):
        self.prompts.append(text)
        return _STUB_CODE

    async def inference_stream_v2(self, messages, **kwargs):
        self.prompts.append(messages[0].content)
        # Fork note (CityAgent Insights): this used to yield nothing, so the
        # streaming codegen paths saw an empty reply. That was harmless while
        # the output was only regex-stripped, but `extract_generated_code` now
        # compiles what it extracts and raises on a reply containing no Python
        # — a real model returning zero chunks is a failure the retry loop
        # should see, not an empty string handed to exec(). The stub now emits
        # the same code its synchronous twin already returned; every assertion
        # in this file is about the captured prompt and is untouched.
        yield TextDeltaEvent(text=_STUB_CODE)


class _StubCodeContextBuilder:
    async def get_top_successful_snippets_for_data_model(self, data_model):
        return ""

    async def get_top_failed_snippets_for_data_model(self, data_model):
        return ""


def _make_coder(settings) -> Coder:
    coder = Coder.__new__(Coder)
    coder.llm = _StubLLM()
    coder.organization_settings = settings
    coder.enable_llm_see_data = True
    coder.instruction_context_builder = None
    coder.context_hub = None
    return coder


def _codegen_context(**kwargs) -> CodeGenContext:
    base = {"user_prompt": "total sales last week", "schemas_excerpt": "<schemas/>"}
    base.update(kwargs)
    return CodeGenContext(**base)


def test_time_context_uses_org_timezone_and_week_start():
    coder = _make_coder(
        _StubSettings({"timezone": "Asia/Jerusalem", "week_start": "sunday"})
    )
    rendered = coder._time_context()
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d")
    assert today in rendered
    assert "timezone: Asia/Jerusalem" in rendered
    assert "week starts on Sunday" in rendered


def test_time_context_falls_back_to_server_local_when_unset():
    rendered = _make_coder(_StubSettings())._time_context()
    local_today = datetime.now().astimezone().strftime("%Y-%m-%d")
    assert local_today in rendered
    assert "week starts on" in rendered


def test_time_context_reads_plain_schema_attributes():
    """Settings objects without get_config (the pydantic schema) still work."""
    settings = OrganizationSettingsConfig(timezone="America/New_York")
    rendered = _make_coder(settings)._time_context()
    assert "timezone: America/New_York" in rendered


@pytest.mark.asyncio
async def test_generate_code_prompt_includes_current_time():
    coder = _make_coder(_StubSettings({"timezone": "Asia/Jerusalem"}))
    await coder.generate_code(
        data_model=None,
        prompt="total sales last week",
        interpreted_prompt="sum sales for the last 7 days",
        schemas="<schemas/>",
        ds_clients={},
        excel_files=[],
        code_and_error_messages=[],
        memories="",
        previous_messages=[],
        retries=0,
        context=_codegen_context(),
    )
    prompt = coder.llm.prompts[0]
    assert "Current Time:" in prompt
    assert datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d") in prompt
    # The time line explains relative phrases but must NOT invite resolving
    # them into literal dates — that's the stale-on-rerun bug.
    assert "do NOT bake the resolved dates into the code as literals" in prompt


@pytest.mark.asyncio
async def test_data_model_to_code_prompt_includes_current_time():
    coder = _make_coder(_StubSettings({"timezone": "Asia/Jerusalem"}))
    await coder.data_model_to_code(
        data_model={"columns": []},
        prompt="total sales last week",
        schemas="<schemas/>",
        ds_clients={},
        excel_files=[],
        code_and_error_messages=[],
        memories="",
        previous_messages=[],
        retries=0,
        code_context_builder=_StubCodeContextBuilder(),
    )
    prompt = coder.llm.prompts[0]
    assert "Current Time:" in prompt
    assert datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d") in prompt


@pytest.mark.asyncio
async def test_generate_inspection_code_prompt_includes_current_time():
    coder = _make_coder(_StubSettings({"timezone": "Asia/Jerusalem"}))
    await coder.generate_inspection_code(
        prompt="check date ranges",
        schemas="<schemas/>",
        ds_clients={},
        excel_files=[],
        code_and_error_messages=[],
        memories="",
        previous_messages=[],
        retries=0,
        context=_codegen_context(),
    )
    prompt = coder.llm.prompts[0]
    assert "Current Time:" in prompt
    assert datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d") in prompt
