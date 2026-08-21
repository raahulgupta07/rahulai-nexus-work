"""A deck records WHY it is in the theme it is in.

WHY THIS FILE EXISTS
--------------------
MEASURED across five real decks on this install: the model set ``theme_id``
**zero** times. Every one of those decks therefore fell through to the
``boardroom`` default — and nothing anywhere recorded that a fallthrough had
happened. So a deck somebody deliberately asked to be built in Boardroom and a
deck that nobody styled at all came out byte-identical in the only place the
product looked: they were both simply "boardroom".

That is the defect. Not the default itself — ``boardroom`` is a reasonable
place to land — but the fact that landing there by accident was unobservable.
You cannot measure how often the model chooses a design system if choosing and
not choosing produce the same record.

``resolve_with_reason()`` closes it: the same tier ladder as ``resolve()``,
returning ``(theme, reason)`` so the caller can log it, store it, and count it.
The rules the model is given about naming its design system are ported from
StackBlitz's MIT ``bolt-slides`` agent skill.

★ THE OBSERVABILITY IS ONLY HALF OF IT. Being able to COUNT fallthroughs does
not reduce them, and the reason the count was five out of five is upstream of
the resolver: ``CreateArtifactInput.theme_id``'s own field description told
the model *"Leave this out when you have no preference"*. A model that has not
been given a reason to have a preference reads that as permission to skip the
field — so the schema was issuing a standing invitation to fall through, and
the resolver was faithfully recording the result. This file guards both ends:
the resolver's reason, and the invitation's removal.

★ But omitting must stay POSSIBLE. Two situations are exactly the case where
naming an id is wrong, because the choice was already made deliberately by
somebody else and an id would override it: the report carries a saved theme,
or the organisation's brand should decide. A test demanding "always name a
theme" would pin a worse product than the one that shipped. So the assertions
below anchor on what must be ABSENT and on the structural facts, never on the
new prose — the wording is the deck-prompt author's to tune.

WHAT THIS FILE PINS
-------------------
  * the three reason constants, by exact VALUE — a renamed constant whose
    string quietly changed breaks every consumer that stored the old one, and
    stored reasons outlive the code that wrote them;
  * that a deliberate Boardroom deck and a fallthrough Boardroom deck are
    DISTINGUISHABLE, which is the defect stated as an assertion;
  * that a fallthrough says so in the log, since "nothing recorded it" was the
    original complaint;
  * ★ and, as the positive control that keeps this a widening rather than a
    replacement, that ``resolve()`` still exists, is still callable, and still
    answers with a Theme — NOT a tuple. Every existing caller unpacks nothing.

These are pure calls against a registry built at import. No database, nothing
this directory's no-op ``run_migrations`` could fail to provide.
"""
import inspect
import logging
import typing

import pytest

from app.ai.decks import pptx_themes
from app.ai.tools.schemas.create_artifact import CreateArtifactInput

RESOLVER_LOGGER = "app.ai.decks.pptx_themes"

# The phrase that caused the measured defect. Verbatim, because its removal is
# the contract — a reword that keeps the meaning is a product decision and
# should be made deliberately, not slipped past this file.
THE_STANDING_INVITATION = "Leave this out when you have no preference"


# ═══════════════════════════════════════════════════════════════════════════
# The constants — by value, not merely by existence
# ═══════════════════════════════════════════════════════════════════════════


def test_the_reason_constants_keep_the_exact_strings_consumers_store():
    """A reason is written down and read back later.

    Asserting the NAMES exist is not enough: renaming a constant while
    changing its value is invisible to a name check and silently invalidates
    every reason already recorded against a deck.
    """
    assert pptx_themes.THEME_REASON_EXPLICIT == "explicit"
    assert pptx_themes.THEME_REASON_MATCHED == "grammar_match"
    assert pptx_themes.THEME_REASON_FALLTHROUGH == "fallthrough"


def test_the_three_reasons_are_three_different_answers():
    """Two reasons collapsing onto one string re-creates the original defect
    in a form every value assertion above would still pass."""
    reasons = {
        pptx_themes.THEME_REASON_EXPLICIT,
        pptx_themes.THEME_REASON_MATCHED,
        pptx_themes.THEME_REASON_FALLTHROUGH,
    }
    assert len(reasons) == 3


def test_the_reasons_are_part_of_the_modules_public_surface():
    """They are meant to be imported by name, so they are exported by name."""
    for constant in (
        "THEME_REASON_EXPLICIT",
        "THEME_REASON_MATCHED",
        "THEME_REASON_FALLTHROUGH",
        "resolve",
        "resolve_with_reason",
    ):
        assert constant in pptx_themes.__all__


# ═══════════════════════════════════════════════════════════════════════════
# A deck says why it is where it is
# ═══════════════════════════════════════════════════════════════════════════


def test_a_deck_that_nobody_styled_is_recorded_as_a_fallthrough():
    """No request, no saved theme, no brand, no agent default — nothing chose."""
    theme, reason = pptx_themes.resolve_with_reason()

    assert theme.id == pptx_themes.DEFAULT_THEME_ID
    assert reason == pptx_themes.THEME_REASON_FALLTHROUGH


def test_a_deck_saved_with_a_theme_name_is_recorded_as_an_explicit_choice():
    """The report's stored theme is a field somebody set, not conversation."""
    theme, reason = pptx_themes.resolve_with_reason(report_theme_name="boardroom")

    assert theme.id == "boardroom"
    assert reason == pptx_themes.THEME_REASON_EXPLICIT


def test_a_look_asked_for_in_words_is_recorded_as_a_grammar_match():
    """Free text that hits the naming grammar is a match, not an explicit id.

    The distinction matters: a grammar match is an inference over prose and
    has been wrong before (a theme merely MENTIONED in an earlier turn once
    won), so it is worth being able to count them separately from a field
    somebody filled in.
    """
    theme, reason = pptx_themes.resolve_with_reason(
        user_text="please make it in the boardroom style"
    )

    assert theme.id == "boardroom"
    assert reason == pptx_themes.THEME_REASON_MATCHED


def test_the_org_brand_and_the_agent_default_are_explicit_too():
    """Both are configuration fields naming a theme, not prose about one."""
    _, brand_reason = pptx_themes.resolve_with_reason(org_brand={"theme": "telemetry"})
    _, default_reason = pptx_themes.resolve_with_reason(agent_default="telemetry")

    assert brand_reason == pptx_themes.THEME_REASON_EXPLICIT
    assert default_reason == pptx_themes.THEME_REASON_EXPLICIT


# ═══════════════════════════════════════════════════════════════════════════
# ★ The measured defect, stated as an assertion
# ═══════════════════════════════════════════════════════════════════════════


def test_a_deliberate_boardroom_deck_is_not_confusable_with_one_that_never_chose():
    """THE defect, in one test.

    Five real decks landed in `boardroom` because the model never named a
    theme, and the product could not tell them from a deck someone asked to be
    built in Boardroom. Same theme out of both calls — that part is correct
    and must stay — but the reasons have to differ, or nothing downstream can
    ever count how often a design system was actually chosen.
    """
    chosen, chosen_reason = pptx_themes.resolve_with_reason(
        report_theme_name="boardroom"
    )
    fell_through, fallthrough_reason = pptx_themes.resolve_with_reason()

    assert chosen.id == fell_through.id == pptx_themes.DEFAULT_THEME_ID
    assert chosen_reason != fallthrough_reason
    assert chosen_reason == pptx_themes.THEME_REASON_EXPLICIT
    assert fallthrough_reason == pptx_themes.THEME_REASON_FALLTHROUGH


def test_a_fallthrough_says_so_in_the_log(caplog):
    """"Nothing recorded that it happened" was the complaint; this is the
    record."""
    with caplog.at_level(logging.INFO, logger=RESOLVER_LOGGER):
        pptx_themes.resolve_with_reason()

    messages = [r.getMessage() for r in caplog.records if r.name == RESOLVER_LOGGER]
    assert any("fallthrough" in m for m in messages), messages


def test_a_deck_that_chose_does_not_report_a_fallthrough(caplog):
    """The positive control for the log line above.

    Logging on every resolution would satisfy the previous test and make the
    line worthless — a signal that fires always is not a signal.
    """
    with caplog.at_level(logging.INFO, logger=RESOLVER_LOGGER):
        pptx_themes.resolve_with_reason(report_theme_name="telemetry")

    messages = [r.getMessage() for r in caplog.records if r.name == RESOLVER_LOGGER]
    assert not any("fallthrough" in m for m in messages), messages


# ═══════════════════════════════════════════════════════════════════════════
# ★ The cause: the schema stopped inviting the model to skip the field
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def theme_id_field():
    """The `theme_id` field as pydantic actually sees it.

    Read from `model_fields`, not from the source text of
    `schemas/create_artifact.py`. The description is assembled from several
    adjacent string literals, so a text scan measures the source layout; this
    measures the sentence the model is handed.
    """
    fields = CreateArtifactInput.model_fields
    assert "theme_id" in fields, "theme_id has been removed from the tool schema"
    return fields["theme_id"]


def test_the_schema_no_longer_invites_the_model_to_skip_the_theme(theme_id_field):
    """THE cause of five decks out of five falling through.

    "Leave this out when you have no preference" is not neutral guidance. A
    model that was never given a reason to form a preference reads it as
    permission, and the field went unset every single time.
    """
    assert THE_STANDING_INVITATION not in (theme_id_field.description or "")


def test_omitting_a_theme_is_still_allowed_in_the_cases_where_it_is_right(
    theme_id_field,
):
    """★ The control that keeps this a NARROWING, not a reversal.

    Deleting the invitation and saying nothing else would leave a schema that
    reads "always name a theme" — and that is a WORSE product than the one
    that shipped, because naming an id overrides a report's saved theme and
    the organisation's brand, both of which are somebody's deliberate choice
    already made. So the exception has to survive.

    Anchored on the two tiers themselves and on the presence of some
    exception-signalling word, never on the new wording: the phrasing belongs
    to whoever tunes this prompt, the two tiers belong to the resolver.
    """
    description = (theme_id_field.description or "").lower()

    assert any(
        word in description for word in ("exception", "unless", "except")
    ), "no exception is expressed at all — omitting now reads as always wrong"
    assert "brand" in description, "the organisation-brand exception is gone"
    assert "saved" in description or "already carries" in description, (
        "the saved-report-theme exception is gone"
    )


def test_the_field_is_still_optional_and_still_defaults_to_absent(theme_id_field):
    """★ POSITIVE CONTROL — the structural facts, which the prose cannot fake.

    Instructions do not enforce anything. If the field had been made required,
    or given a default id, every one of the assertions above would still pass
    while the product had lost the ability to defer to a saved theme at all.
    """
    assert theme_id_field.default is None
    assert not theme_id_field.is_required()
    assert theme_id_field.annotation == typing.Optional[str]


def test_a_deck_that_defers_with_auto_still_builds():
    """★RECORDED DECISION (2026-08-20). This test used to pin that OMISSION
    "has to remain a legal input all the way through". Omission was the measured
    defect (7 of 7 decks unthemed by silence), so the schema now rejects it on a
    slides call — and the deferral this test protects lives on as the explicit
    sentinel 'auto', which coerces to None and produces exactly the fallthrough
    this file exists to make visible. The fallback chain is intact; only
    SILENCE stopped being a way to invoke it.
    """
    parsed = CreateArtifactInput(prompt="build me a deck", mode="slides", theme_id="auto")
    assert parsed.theme_id == "auto"

    theme, reason = pptx_themes.resolve_with_reason(agent_default=None)
    assert theme.id == pptx_themes.DEFAULT_THEME_ID
    assert reason == pptx_themes.THEME_REASON_FALLTHROUGH


def test_silence_is_no_longer_a_way_to_skip_the_theme_question():
    import pydantic
    with pytest.raises(pydantic.ValidationError) as err:
        CreateArtifactInput(prompt="build me a deck", mode="slides")
    assert "auto" in str(err.value)

def test_resolve_still_exists_and_still_answers_with_one_theme():
    """Every existing caller does `theme = resolve(...)` and unpacks nothing.

    Turning `resolve` into the two-value function would break all of them at a
    distance — the tuple is truthy, has attributes nobody asked for, and would
    reach python-pptx as a "theme" before anything complained.
    """
    assert callable(pptx_themes.resolve)

    theme = pptx_themes.resolve(user_text="in the telemetry style")

    assert not isinstance(theme, tuple)
    assert isinstance(theme, pptx_themes.Theme)
    assert theme.id == "telemetry"


def test_resolve_takes_the_same_inputs_it_always_did():
    """A signature change is a caller break even when the return type is
    right — every call site in the tree passes these by keyword."""
    params = inspect.signature(pptx_themes.resolve).parameters

    assert set(params) == {
        "user_text",
        "report_theme_name",
        "org_brand",
        "agent_default",
    }
    for name, param in params.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert param.default is None, name


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"user_text": "in the telemetry style"},
        {"report_theme_name": "boardroom"},
        {"org_brand": {"theme": "telemetry"}},
        {"agent_default": "telemetry"},
        {"user_text": "in the telemetry style", "report_theme_name": "boardroom"},
    ],
)
def test_the_two_resolvers_can_never_disagree_about_the_theme(kwargs):
    """`resolve` is documented as a thin wrapper. Two ladders would drift, and
    the drift would show up as a deck rendered in one theme and REPORTED as
    another — worse than no reason at all."""
    assert pptx_themes.resolve(**kwargs) is pptx_themes.resolve_with_reason(**kwargs)[0]


# ═══════════════════════════════════════════════════════════════════════════
# Neither resolver may cost a deck
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "kwargs",
    [
        {"user_text": None},
        {"report_theme_name": "no-such-theme-anywhere"},
        {"org_brand": {}},
        {"org_brand": {"theme": None}},
        {"agent_default": ""},
        {"user_text": 17},
        {"report_theme_name": ["boardroom"]},
        {"org_brand": "not a dict at all"},
    ],
)
def test_junk_inputs_still_produce_a_theme_and_a_reason(kwargs):
    """A theme is a design improvement; failing to pick one must never be the
    reason a deck does not get built."""
    theme, reason = pptx_themes.resolve_with_reason(**kwargs)

    assert isinstance(theme, pptx_themes.Theme)
    assert reason in {
        pptx_themes.THEME_REASON_EXPLICIT,
        pptx_themes.THEME_REASON_MATCHED,
        pptx_themes.THEME_REASON_FALLTHROUGH,
    }
