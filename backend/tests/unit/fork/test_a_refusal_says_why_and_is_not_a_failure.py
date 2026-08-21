"""DEF-017/018 — Phase 4: the product knew why and did not say.

DEF-017  `web_search` writes a plain sentence for every way a search can end —
         "Web access is disabled for this organization. An administrator can
         turn it on with the Web Fetch setting." for the policy refusal, the
         network reason otherwise. `WebSearchTool.vue` rendered NONE of it. The
         member saw a flat orange "Web search failed", and three unrelated
         causes (the setting is off, egress is blocked, the build is old)
         were indistinguishable on screen.

         Worse, the row could not even be OPENED: `expandable` was
         `sources.length || extraQueries.length || (isSuccess && hasSourcesField)`,
         and a refusal has none of those. The explanation was unrendered AND
         unreachable.

         ★And a refusal is not a failure. Calling a deliberate org policy
         "failed" sends the member hunting a fault that does not exist — the
         same shape as the `.543.9` sync button whose `resting` state rendered
         "Synced" for four different situations, three of them false.

DEF-018  `GET /api/reports/{id}/completions` is served by `CompletionV2Schema`;
         the v1 shape moved to `/completions.legacy`. v1's `completion` field
         WAS the answer. So an integration on the documented path reads a
         familiar key, gets `null`, and concludes the turn said nothing while
         the column holds thousands of characters.

★Two of Phase 4's three roadmap items do NOT reproduce on this tree — see
`TestWhatWasMeasuredOnDev`. 8.4's backend half was already correct; only the
screen was silent.
"""
import inspect
import pathlib
import re

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[3]
REPO = BACKEND.parent
APP = BACKEND / "app"
VUE = REPO / "frontend" / "components" / "tools" / "WebSearchTool.vue"


def _vue() -> str:
    return VUE.read_text(encoding="utf-8")


def _vue_no_comments() -> str:
    """★A source scan that reads its own explanation is a mistake this repo has
    made at least three times. The comments below QUOTE the broken form."""
    src = _vue()
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


class TestTheReasonReachesTheScreen:
    def test_the_component_renders_the_error_message(self):
        assert "errorMessage" in _vue_no_comments()
        assert "web-search-error-message" in _vue_no_comments()

    def test_the_message_is_read_from_the_tool_result(self):
        assert "result.value?.error_message" in _vue_no_comments()

    def test_a_refusal_row_can_be_opened(self):
        """★The half that made the message unreachable. A refusal has no
        sources and no extra queries, so `expandable` must consider the error
        message or the explanation cannot be revealed at all."""
        src = _vue_no_comments()
        block = src[src.index("const expandable"):]
        block = block[:block.index(")\n")]
        assert "errorMessage" in block

    def test_the_positive_controls_still_hold(self):
        """A successful search must still expand for its sources — a change
        that made only refusals expandable would break the ordinary case."""
        src = _vue_no_comments()
        block = src[src.index("const expandable"):]
        block = block[:block.index(")\n")]
        assert "sources.value.length" in block
        assert "extraQueries.value.length" in block
        assert "hasSourcesField" in block


class TestARefusalIsNotAFailure:
    def test_the_tool_declares_the_refusal(self):
        from app.ai.tools.schemas.web_search import WebSearchOutput
        assert "blocked_by_policy" in WebSearchOutput.model_fields

    def test_it_defaults_to_false(self):
        """★A default of True would mark every network failure a policy
        refusal, which is this defect with the signs reversed."""
        from app.ai.tools.schemas.web_search import WebSearchOutput
        assert WebSearchOutput().blocked_by_policy is False

    def test_the_policy_branch_sets_it(self):
        from app.ai.tools.implementations import web_search as mod
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        gate = src[src.index('if not setting_enabled(organization_settings, "enable_web_fetch")'):]
        gate = gate[:gate.index("return")]
        assert "blocked_by_policy=True" in gate

    def test_the_network_failure_path_does_not_set_it(self):
        """★Positive control. Only the policy gate may claim a refusal."""
        from app.ai.tools.implementations import web_search as mod
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert src.count("blocked_by_policy=True") == 1

    def test_the_screen_says_turned_off_not_failed(self):
        src = _vue_no_comments()
        assert "Web search is turned off" in src
        assert "web-search-blocked" in src

    def test_the_failure_label_still_exists_for_real_failures(self):
        """★The other positive control. A change that renamed every failure
        'turned off' passes the test above and lies about a network outage."""
        assert "Web search failed" in _vue_no_comments()

    def test_the_state_is_read_from_the_flag_not_the_sentence(self):
        """★A screen that decides what a state MEANS by pattern-matching a
        sentence breaks the moment the sentence is reworded or translated."""
        src = _vue_no_comments()
        assert "result.value?.blocked_by_policy === true" in src
        block = src[src.index("const blockedByPolicy"):]
        block = block[:block.index("\n\n")] if "\n\n" in block else block
        for smell in ("includes(", "indexOf(", "match(", "startsWith("):
            assert smell not in block, f"blockedByPolicy infers from text ({smell})"


class TestTheNullCompletionKeyExplainsItself:
    def test_the_field_carries_a_description(self):
        from app.schemas.completion_v2_schema import CompletionV2Schema
        desc = CompletionV2Schema.model_fields["completion"].description or ""
        assert desc, "the null is unexplained where an integrator looks"
        assert "completion_blocks" in desc, "it must name where the answer IS"

    def test_it_still_defaults_to_none(self):
        """★NOT fixed by populating it. The answer is already in
        `completion_blocks`; filling this for ordinary turns would ship every
        answer's full text twice on a LIST endpoint. The schema's own note says
        so, and this pins that the documentation fix did not quietly become a
        payload change."""
        from app.schemas.completion_v2_schema import CompletionV2Schema
        assert CompletionV2Schema.model_fields["completion"].default is None

    def test_the_documented_path_is_the_v2_one(self):
        """The fact that makes the null confusing: v1 moved aside and kept the
        field name. If this ever flips back, the description is stale."""
        src = (APP / "routes" / "completion.py").read_text(encoding="utf-8")
        assert '"/api/reports/{report_id}/completions.legacy"' in src
        assert '@router.get("/api/reports/{report_id}/completions")' in src


class TestWhatWasMeasuredOnDev:
    """★Recorded so nobody re-diagnoses these from the roadmap text alone.

    Both were measured on dev (`.543.8` out of an image tagged `0.0.543.4`).
    These assertions state what is true HERE; a failure means the defect has
    arrived on this tree and the roadmap entry is live again.
    """

    def test_signing_in_does_kick_off_a_sync(self):
        """10.2 — "nothing triggers a sync after sign-in". It does, with the
        tracker started, so the strip reports it."""
        src = (APP / "routes" / "powerbi_user_signin.py").read_text(encoding="utf-8")
        assert "await prog.start(ds_id, uid, trigger=TRIGGER_SIGNIN)" in src
        assert "await _run_tenant_merge(ds_id, uid, org_id" in src

    def test_the_backend_already_explains_the_refusal(self):
        """8.4 — the sentence was never missing. Only the screen was silent, so
        the fix is the component, not the tool."""
        from app.ai.tools.implementations import web_search as mod
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "Web access is disabled for this organization" in src

    def test_the_sibling_tool_was_already_honest(self):
        """★The asymmetry that made this findable: `web_fetch` rendered its
        reason and `web_search` did not, for the same class of failure."""
        fetch = REPO / "frontend" / "components" / "tools" / "WebFetchTool.vue"
        assert "error_message" in fetch.read_text(encoding="utf-8")
