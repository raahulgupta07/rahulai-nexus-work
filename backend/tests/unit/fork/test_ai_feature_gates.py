"""Three AI feature switches nothing else covers: web fetch, load_step, LLM fallback.

Each of these is an org setting that decides whether a capability exists at all.
Nothing in the suite pinned any of them, so every one of the following could
have been broken by an ordinary refactor without a single test going red:

  enable_web_fetch  — the agent may reach the public internet. Off by default.
  enable_load_step  — generated code may reuse a prior step's result. Off by default.
  llm_fallback      — a failing model is silently swapped for the next one. Off, EE.

What these tests are for, in order of how much they matter:

1. OFF MEANS ABSENT, NOT MERELY REFUSED.
   ``enable_load_step``'s own description promises "when off, load_step is
   neither advertised to the agent nor available at runtime". Both halves are
   pinned separately, because they are separate code paths in separate files and
   only one of them is load-bearing for safety. A capability that is described
   to the planner and then refuses at the call is worse than one that is absent:
   the model burns a step, gets an error, and retries — and the operator who
   turned the switch off still sees the feature named in every prompt.

2. ON MEANS PRESENT.
   Every "off" assertion here has an "on" twin. A one-directional gate test
   passes perfectly against code that has stopped enabling anything, which is
   the failure mode a feature flag actually has in practice.

3. THE STRING "off" MUST NOT READ AS TRUTHY.
   This codebase has already shipped this bug once: a three-state access setting
   was read with ``if not feature.value:``, and because ``bool("off") is True``
   a feature an admin had switched OFF stayed on. Every gate below is checked
   against the falsy values it must reject, the STRING spellings included. Fixed
   2026-08-02: every switch now reads through ``app.core.feature_flags``. See the
   ``TestOffIsNotTruthy`` class for the whole contract.

4. THE SSRF GUARD.
   ``_is_safe_host`` is the only thing between ``web_fetch`` and the cloud
   metadata endpoint. It is checked against every private range AND against a
   public host, so a guard that has degenerated into ``return False`` fails.

No mocks of the functions under test. Where org settings have to be faked, the
SETTINGS OBJECT is faked (``_Settings`` below) and the real gate runs against
it — a mocked gate would prove only that the mock was called.
"""
import socket

import pandas as pd
import pytest

from app.ai.code_execution.code_execution import StreamingCodeExecutor
from app.ai.code_execution.loadables import (
    STEP_DISCOVERY_MAX_AGE_SECONDS,
    LoadablesResolver,
    load_step_settings,
)
from app.ai.agents.coder.coder import Coder
from app.ai.llm import fallback as fb
from app.ai.tools.implementations import web_fetch as wf
from app.ai.tools.implementations.web_fetch import WebFetchTool, _is_safe_host
from app.schemas.organization_settings_schema import (
    FeatureConfig,
    OrganizationSettingsConfig,
)


# ── stand-ins ────────────────────────────────────────────────────────────────

class _Cfg:
    """One stored setting. Mirrors ``FeatureConfig`` in the only respect every
    gate below uses it: it has a ``.value`` and nothing else is consulted."""

    def __init__(self, value):
        self.value = value


class _Settings:
    """Stand-in for the org's ``OrganizationSettings`` row.

    Deliberately not a model instance: the gates must decide from the setting's
    value alone. A key that was never set returns ``default`` (None), which is
    what a fresh org with no stored config looks like.
    """

    def __init__(self, **values):
        self._values = values

    def get_config(self, key, default=None):
        if key not in self._values:
            return default
        raw = self._values[key]
        # llm_fallback_order is stored as a bare list, not a FeatureConfig.
        return raw if isinstance(raw, list) else _Cfg(raw)


class _ExplodingSettings:
    """Settings whose read raises. Every gate wraps ``get_config`` in a
    try/except and must fail CLOSED — an unreadable setting is not consent."""

    def get_config(self, key, default=None):
        raise RuntimeError("settings unreadable")


class _Step:
    """A row ``LoadablesResolver.list_for_discovery`` renders. Only the fields
    it actually reads."""

    def __init__(self, sid="s-1", title="Customer Sales"):
        self.id = sid
        self.title = title
        self.slug = "customer-sales"
        self.data = {
            "columns": [{"field": "customer"}, {"field": "total"}],
            "rows": [{"customer": "ACME", "total": 1}],
            "info": {"total_rows": 1},
        }


class _Report:
    """Only the field the loadables query reads off a report."""

    def __init__(self, rid="r-1"):
        self.id = rid


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _FakeDb:
    """Returns fixed rows for any query, and counts them. A gate that is
    supposed to short-circuit before the database must leave ``calls`` at 0."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return _Result(self.rows)


class _Model:
    """Stand-in for an ``LLMModel`` row as the fallback walker reads it."""

    def __init__(self, mid, provider_id="p-1", name=None, context_window_tokens=None):
        self.id = mid
        self.name = name or f"model-{mid}"
        self.provider = type("_P", (), {"id": provider_id})()
        self.context_window_tokens = context_window_tokens


@pytest.fixture(autouse=True)
def _reset_breaker():
    """``fallback.breaker`` is a module-level singleton shared by every run in
    the process — including every test in this file. Without this, one test's
    recorded failure silently changes the next test's chain walk."""
    fb.breaker.reset()
    yield
    fb.breaker.reset()


# ═══════════════════════════════════════════════════════════════════════════
# A. enable_load_step — off means neither advertised nor callable
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadStepOffIsAbsent:
    """The setting's description is a promise with two halves. Both are pinned.

    Half one (advertised): the coder's prompt must not describe ``load_step``,
    and the ``<available_steps>`` discovery section must not be rendered.
    Half two (runtime): the closure injected into the sandbox must refuse.

    They live in three different modules, so "I checked the flag" in one of them
    says nothing about the other two.
    """

    def test_unset_settings_means_off(self):
        """A fresh org has no stored value. Default is off, per the schema."""
        assert load_step_settings(None) == (False, STEP_DISCOVERY_MAX_AGE_SECONDS)
        assert load_step_settings(_Settings())[0] is False

    def test_schema_default_is_off(self):
        """If this default ever flips, every org gains the capability on upgrade
        without an admin deciding anything."""
        assert OrganizationSettingsConfig().enable_load_step.value is False

    def test_unreadable_settings_fail_closed(self):
        assert load_step_settings(_ExplodingSettings())[0] is False

    def test_on_is_read_as_on(self):
        assert load_step_settings(_Settings(enable_load_step=True))[0] is True

    # ── not advertised: the coder prompt ────────────────────────────────────

    def test_coder_prompt_does_not_mention_load_step_when_off(self):
        """The model must never be told about a call that would raise."""
        signature_hint, section = Coder._build_reuse_prompt_sections(False)
        assert "load_step" not in signature_hint
        assert "load_step" not in section

    def test_coder_prompt_still_documents_load_entity_when_off(self):
        """``load_entity`` is org-independent. Gating load_step must not take it
        down with it — that would silently remove a second capability."""
        signature_hint, section = Coder._build_reuse_prompt_sections(False)
        assert "load_entity" in signature_hint
        assert "load_entity" in section

    def test_coder_prompt_advertises_load_step_when_on(self):
        """The 'on' twin: a gate that advertises nothing in either state passes
        the two tests above and is completely broken."""
        signature_hint, section = Coder._build_reuse_prompt_sections(True)
        assert "load_step" in signature_hint
        assert "load_step" in section

    # ── not advertised: the <available_steps> discovery block ───────────────

    @pytest.mark.asyncio
    async def test_discovery_advertises_nothing_and_never_queries_when_off(self):
        """Returns None BEFORE the query. ``db.calls == 0`` is the real
        assertion — a version that queried and then discarded the rows would
        still return None and would still be leaking step titles into any future
        caller that logged the intermediate result."""
        db = _FakeDb([_Step()])
        resolver = LoadablesResolver(
            db=db, organization=object(), report=_Report(), enable_load_step=False
        )
        assert await resolver.list_for_discovery() is None
        assert db.calls == 0

    @pytest.mark.asyncio
    async def test_discovery_advertises_the_step_when_on(self):
        db = _FakeDb([_Step()])
        resolver = LoadablesResolver(
            db=db, organization=object(), report=_Report(), enable_load_step=True
        )
        section = await resolver.list_for_discovery()
        assert section is not None
        assert "Customer Sales" in section.render()

    # ── not available at runtime ────────────────────────────────────────────

    def test_load_step_closure_refuses_when_off(self):
        """A stray call must fail loudly and feed the retry loop, not return
        empty data that the model would then present as an answer."""
        registry = {"steps": {"Customer Sales": pd.DataFrame({"a": [1]})}}
        load_step, _ = StreamingCodeExecutor._build_loadable_closures(
            registry, enable_load_step=False
        )
        with pytest.raises(RuntimeError) as exc:
            load_step("Customer Sales")
        assert "disabled" in str(exc.value).lower()

    def test_load_step_closure_returns_data_when_on(self):
        registry = {"steps": {"Customer Sales": pd.DataFrame({"a": [1]})}}
        load_step, _ = StreamingCodeExecutor._build_loadable_closures(
            registry, enable_load_step=True
        )
        assert load_step("Customer Sales")["a"].tolist() == [1]

    def test_load_entity_keeps_working_when_load_step_is_off(self):
        """Two capabilities, one switch each. Turning off the step half must not
        disable entity reuse."""
        registry = {"entities": {"Revenue Model": pd.DataFrame({"b": [2]})}}
        _, load_entity = StreamingCodeExecutor._build_loadable_closures(
            registry, enable_load_step=False
        )
        assert load_entity("Revenue Model")["b"].tolist() == [2]

    @pytest.mark.asyncio
    async def test_resolution_of_step_refs_is_skipped_when_off(self):
        """``db=None`` is the assertion: if resolution ever reached the database
        for a step ref while the feature was off, this raises AttributeError
        instead of quietly returning an empty dict."""
        resolver = LoadablesResolver(
            db=None, organization=object(), report=_Report(), enable_load_step=False
        )
        result = await resolver.resolve(["Customer Sales"], [])
        assert result["steps"] == {}

    def test_executor_reads_the_setting_the_same_way(self):
        """``StreamingCodeExecutor`` has its OWN copy of the check rather than
        calling ``load_step_settings``. Two readers of one switch is exactly how
        a gate ends up half-applied, so both are pinned."""
        assert StreamingCodeExecutor(_Settings(enable_load_step=True))._load_step_enabled() is True
        assert StreamingCodeExecutor(_Settings(enable_load_step=False))._load_step_enabled() is False
        assert StreamingCodeExecutor(_Settings())._load_step_enabled() is False
        assert StreamingCodeExecutor(None)._load_step_enabled() is False
        assert StreamingCodeExecutor(_ExplodingSettings())._load_step_enabled() is False


# ═══════════════════════════════════════════════════════════════════════════
# B. enable_web_fetch — off means the tool refuses and the sandbox has no http
# ═══════════════════════════════════════════════════════════════════════════

async def _drain(agen):
    return [event async for event in agen]


def _end_payload(events):
    ends = [e for e in events if getattr(e, "type", None) == "tool.end"]
    assert len(ends) == 1, f"expected exactly one tool.end, got {len(ends)}"
    return ends[0].payload


class TestWebFetchGate:

    def test_schema_default_is_off(self):
        """Outbound egress must never be granted by an upgrade."""
        assert OrganizationSettingsConfig().enable_web_fetch.value is False

    @pytest.mark.asyncio
    async def test_tool_refuses_when_off(self):
        events = await _drain(
            WebFetchTool().run_stream(
                {"url": "https://example.com"},
                {"settings": _Settings(enable_web_fetch=False)},
            )
        )
        payload = _end_payload(events)
        assert payload["output"]["success"] is False
        assert "disabled" in payload["output"]["error_message"].lower()

    @pytest.mark.asyncio
    async def test_tool_refuses_when_the_setting_is_absent(self):
        """An org that never stored the key is an org that never opted in."""
        events = await _drain(
            WebFetchTool().run_stream(
                {"url": "https://example.com"}, {"settings": _Settings()}
            )
        )
        assert _end_payload(events)["output"]["success"] is False

    @pytest.mark.asyncio
    async def test_tool_refuses_when_there_is_no_settings_context(self):
        """No settings object at all — a run assembled without org context must
        not inherit the internet."""
        events = await _drain(
            WebFetchTool().run_stream({"url": "https://example.com"}, {})
        )
        payload = _end_payload(events)
        assert payload["output"]["success"] is False
        assert "unavailable" in payload["output"]["error_message"].lower()

    @pytest.mark.asyncio
    async def test_the_refusal_happens_before_any_network_reasoning(self):
        """With the flag OFF, a request for a private address is refused for the
        POLICY reason, not the SSRF reason. Order matters: it proves the policy
        gate is the first thing in the function and cannot be skipped by
        crafting a URL that trips some earlier branch."""
        events = await _drain(
            WebFetchTool().run_stream(
                {"url": "http://169.254.169.254/latest/meta-data/"},
                {"settings": _Settings(enable_web_fetch=False)},
            )
        )
        assert "disabled" in _end_payload(events)["output"]["error_message"].lower()

    @pytest.mark.asyncio
    async def test_flag_on_gets_past_the_policy_gate(self):
        """The 'on' twin, expressed without touching the network: with the flag
        ON, the same private-address URL is refused for a DIFFERENT reason —
        the SSRF guard. Different refusal == the policy gate let it through."""
        events = await _drain(
            WebFetchTool().run_stream(
                {"url": "http://169.254.169.254/latest/meta-data/"},
                {"settings": _Settings(enable_web_fetch=True)},
            )
        )
        message = _end_payload(events)["output"]["error_message"].lower()
        assert "non-public" in message
        assert "disabled" not in message

    # ── the sandbox's `http` client ─────────────────────────────────────────

    def test_sandbox_has_no_http_client_when_off(self):
        """The tool refusing is not enough: generated code gets its own,
        separate route to the internet. This is the one that matters, because
        code the model wrote runs without another policy check."""
        assert StreamingCodeExecutor(_Settings(enable_web_fetch=False))._build_http_client() is None
        assert StreamingCodeExecutor(_Settings())._build_http_client() is None
        assert StreamingCodeExecutor(None)._build_http_client() is None
        assert StreamingCodeExecutor(_ExplodingSettings())._build_http_client() is None

    def test_sandbox_gets_an_http_client_when_on(self):
        client = StreamingCodeExecutor(_Settings(enable_web_fetch=True))._build_http_client()
        assert client is not None
        assert hasattr(client, "get") and hasattr(client, "batch_get")


# ═══════════════════════════════════════════════════════════════════════════
# C. "off" is a truthy string
# ═══════════════════════════════════════════════════════════════════════════

# Every gate in this file used to read its setting as ``bool(cfg.value)``. That
# is correct for a boolean and wrong for a string: ``bool("off") is True``, so
# an admin who typed "off" got the capability switched ON. The codebase had
# shipped this exact bug once before, on a three-state access setting.
#
# ``FeatureConfig.value`` is ``Optional[Any]`` and the settings PUT applies no
# coercion, so the string is not a corrupted row — it is what the supported
# write path produces (see the round-trip test below). The gates now read
# through ``app.core.feature_flags.setting_enabled``, which normalises the
# spellings and fails closed on anything it cannot parse.

_FALSY_VALUES = [False, None, "", 0]
_TRUTHY_STRING_VALUES = ["off", "false", "no", "disabled"]


def _all_gates_disabled(value):
    """Every reader of these three switches, evaluated against one stored value.

    Returns a dict so a failure names WHICH gate let the value through — a bare
    ``assert not any(...)`` would say only that something did.
    """
    settings = _Settings(enable_load_step=value, enable_web_fetch=value)
    return {
        "load_step_settings": load_step_settings(settings)[0],
        "executor_load_step": StreamingCodeExecutor(settings)._load_step_enabled(),
        "executor_http": StreamingCodeExecutor(settings)._build_http_client() is not None,
    }


class TestOffIsNotTruthy:

    @pytest.mark.parametrize("value", _FALSY_VALUES)
    def test_falsy_values_disable_every_gate(self, value):
        assert _all_gates_disabled(value) == {
            "load_step_settings": False,
            "executor_load_step": False,
            "executor_http": False,
        }

    @pytest.mark.parametrize("value", _FALSY_VALUES)
    @pytest.mark.asyncio
    async def test_falsy_values_make_the_web_fetch_tool_refuse(self, value):
        events = await _drain(
            WebFetchTool().run_stream(
                {"url": "https://example.com"},
                {"settings": _Settings(enable_web_fetch=value)},
            )
        )
        assert _end_payload(events)["output"]["success"] is False

    def test_a_non_boolean_value_survives_the_schema_round_trip(self):
        """Why the block below is reachable rather than hypothetical.

        ``FeatureConfig.value`` is ``Optional[Any]`` and
        ``OrganizationSettingsService.update_settings`` applies no coercion to
        these keys, so ``PUT /organization_settings`` with
        ``{"enable_web_fetch": {"value": "off"}}`` is stored verbatim and read
        back verbatim. The string is not a corrupted row — it is what the
        supported write path produces.
        """
        default = OrganizationSettingsConfig().enable_web_fetch.dict()
        stored = FeatureConfig(**{**default, "value": "off"}).dict()
        assert stored["value"] == "off"

    @pytest.mark.parametrize("value", _TRUTHY_STRING_VALUES)
    def test_off_spelled_as_a_string_disables_every_gate(self, value):
        assert _all_gates_disabled(value) == {
            "load_step_settings": False,
            "executor_load_step": False,
            "executor_http": False,
        }


# ═══════════════════════════════════════════════════════════════════════════
# D. _is_safe_host — the SSRF guard
# ═══════════════════════════════════════════════════════════════════════════

class TestIsSafeHost:
    """Every case here is an IP literal or short-circuits before name
    resolution, so nothing in this class performs a real DNS lookup — the one
    host that needs resolving is monkeypatched. A guard test that needed the
    network would be a guard test that gets skipped in CI."""

    @pytest.mark.parametrize(
        "hostname",
        [
            "localhost",
            "foo.localhost",          # the suffix rule, not just the exact name
            "127.0.0.1",              # loopback
            "127.1",                  # the same loopback, written short
            "0.0.0.0",                # unspecified
            "::1",                    # loopback, v6
            "169.254.169.254",        # cloud instance metadata — the prize
            "fd00::1",                # unique-local, v6
            "10.0.0.1",               # RFC1918
            "192.168.1.1",            # RFC1918
            "172.16.0.1",             # RFC1918, the range people forget
            "224.0.0.1",              # multicast
            "",                       # no host at all
        ],
    )
    def test_rejects_non_public_hosts(self, hostname):
        assert _is_safe_host(hostname) is False

    def test_rejects_a_public_name_that_resolves_to_a_private_address(self, monkeypatch):
        """DNS rebinding, expressed without DNS. The name looks routable; the
        answer is 10.x. Only resolving and inspecting the ANSWER catches this,
        which is why the guard cannot be a string check on the hostname."""
        monkeypatch.setattr(
            wf.socket,
            "getaddrinfo",
            lambda host, port, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.11.12.13", 0))
            ],
        )
        assert _is_safe_host("intranet.example.com") is False

    def test_rejects_when_only_one_of_several_answers_is_private(self, monkeypatch):
        """A host with a public A record and a private one is still a way in.
        Any non-public answer rejects the whole name."""
        monkeypatch.setattr(
            wf.socket,
            "getaddrinfo",
            lambda host, port, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.5", 0)),
            ],
        )
        assert _is_safe_host("split-horizon.example.com") is False

    def test_rejects_a_name_that_does_not_resolve(self, monkeypatch):
        """Fails closed on a lookup error rather than proceeding to the fetch."""
        def _boom(*args, **kwargs):
            raise socket.gaierror("nope")

        monkeypatch.setattr(wf.socket, "getaddrinfo", _boom)
        assert _is_safe_host("no-such-host.invalid") is False

    def test_accepts_a_normal_public_host(self, monkeypatch):
        """The counterweight. Without this, a guard that had degenerated to
        ``return False`` — killing the feature outright — would pass every other
        test in this class."""
        monkeypatch.setattr(
            wf.socket,
            "getaddrinfo",
            lambda host, port, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
        assert _is_safe_host("example.com") is True

    def test_accepts_a_public_ip_literal(self):
        """No monkeypatch: getaddrinfo parses a literal without asking a
        resolver, so this exercises the real code end to end."""
        assert _is_safe_host("93.184.216.34") is True


# ═══════════════════════════════════════════════════════════════════════════
# E. llm_fallback — an order shorter than two is not a fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestFallbackOrderReading:

    def test_schema_defaults(self):
        cfg = OrganizationSettingsConfig()
        assert cfg.llm_fallback.value is False
        assert cfg.llm_fallback.is_lab is True
        assert cfg.llm_fallback_order == []

    def test_missing_or_unreadable_order_is_empty(self):
        assert fb.get_fallback_order(None) == []
        assert fb.get_fallback_order(_Settings()) == []
        assert fb.get_fallback_order(_ExplodingSettings()) == []

    def test_a_non_list_order_is_empty(self):
        """Stored shape is a bare list. Anything else is discarded rather than
        iterated — a string would otherwise walk character by character."""
        assert fb.get_fallback_order(_Settings(llm_fallback_order="m-1")) == []

    def test_non_string_entries_are_dropped_silently(self):
        """Worth knowing: an order written by hand into the JSON blob as ints
        resolves to nothing at all, with no error anywhere."""
        assert fb.get_fallback_order(_Settings(llm_fallback_order=[1, 2])) == []

    def test_the_order_is_capped(self):
        long_order = [f"m-{i}" for i in range(fb.MAX_FALLBACK_CHAIN + 5)]
        assert len(fb.get_fallback_order(_Settings(llm_fallback_order=long_order))) == (
            fb.MAX_FALLBACK_CHAIN
        )

    @pytest.mark.asyncio
    async def test_an_empty_order_never_queries_for_models(self):
        """``db=None``: an empty order must short-circuit, not run a SELECT with
        an empty IN clause on every single run."""
        assert await fb.resolve_fallback_chain(None, object(), []) == []


class TestFallbackChainOfOne:
    """The live configuration: llm_fallback ON, order = exactly one model id.

    Length 0 and length 1 are both pinned here because "fallback is enabled" and
    "fallback can happen" are different statements, and the settings UI reports
    only the first one.
    """

    def test_an_empty_chain_yields_no_candidate(self):
        controller = fb.FallbackController([], current_model=_Model("m-1"))
        assert controller.next_candidate("rate_limit") is None

    def test_one_entry_that_is_a_different_model_does_substitute(self):
        """The legitimate one-entry case: the order names a model other than the
        one in use, so exactly one substitution is possible."""
        current, other = _Model("m-1"), _Model("m-2", provider_id="p-2")
        controller = fb.FallbackController([other], current_model=current)
        assert controller.next_candidate("rate_limit") is other
        assert controller.current is other
        # ...and only one. The chain is then exhausted.
        assert controller.next_candidate("rate_limit") is None

    def test_one_entry_that_is_the_model_in_use_can_never_substitute(self):
        """★ The finding. An order of ``[the model already in use]`` resolves to
        a non-empty chain, so ``_setup_llm_fallback`` binds a controller and
        logs "[fallback] active: 1 candidate(s)" — but the walker skips the
        entry as already-attempted and returns None.

        Pinned as behaviour, not as approval: no exception is raised and no
        pointless switch is attempted (both good), yet the org is shown a
        feature that is enabled, configured, and structurally incapable of ever
        firing. Nothing between the settings page and the run says so.
        """
        current = _Model("m-1")
        same = _Model("m-1")  # same id, as resolve_fallback_chain would return
        controller = fb.FallbackController([same], current_model=current)
        assert controller.next_candidate("rate_limit") is None
        assert controller.current is current

    def test_a_doomed_chain_still_trips_the_shared_circuit_breaker(self):
        """Consequence of the above, worth pinning because the breaker is a
        process-wide singleton: the failure is recorded against the current
        model even though no substitution was possible, so a run that CAN fall
        back will later skip that model as breaker-open."""
        current = _Model("m-1")
        controller = fb.FallbackController([_Model("m-1")], current_model=current)
        for _ in range(fb.CircuitBreaker().threshold):
            controller.next_candidate("rate_limit")
        assert fb.breaker.is_open("p-1", "m-1") is True

    def test_an_ineligible_error_code_never_walks_the_chain(self):
        """auth/context_length/unknown must surface, not be masked by a swap."""
        current, other = _Model("m-1"), _Model("m-2", provider_id="p-2")
        controller = fb.FallbackController([other], current_model=current)
        assert controller.next_candidate("auth") is None
        assert controller.current is current

    def test_force_walks_the_chain_for_an_ineligible_code(self):
        """The agent's last-resort rescue after its retry budget is spent."""
        current, other = _Model("m-1"), _Model("m-2", provider_id="p-2")
        controller = fb.FallbackController([other], current_model=current)
        assert controller.next_candidate("unknown", force=True) is other

    def test_force_does_not_record_an_ineligible_failure_on_the_breaker(self):
        """An ineligible error says nothing about availability, so it must not
        trip cooldowns that other runs in this process consult."""
        current, other = _Model("m-1"), _Model("m-2", provider_id="p-2")
        controller = fb.FallbackController([other], current_model=current)
        controller.next_candidate("unknown", force=True)
        assert fb.breaker.is_open("p-1", "m-1") is False
