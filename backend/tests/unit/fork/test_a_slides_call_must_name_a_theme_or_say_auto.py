"""A deck has to say which design system it is built in.

WHY THIS FILE EXISTS
--------------------
`theme_id` was an optional field with a long, careful description asking the
model to name one on every deck. Measured: it was omitted on EVERY deck, twice
over, and every one of them fell through to a default picked for nobody in
particular. Two rewrites of the description did not move it.

So the requirement moved into the SHAPE. A `mode="slides"` call with no
`theme_id` is now a validation error, and the planner replays a validation
error back to the model — which is why the message itself is part of the
contract and is asserted here: it has to name BOTH legal answers, or the model
learns only that it did something wrong.

There are exactly two situations where the choice has already been made by
somebody else (the report carries a saved theme; the organisation's brand
should decide). Naming an id there would OVERRIDE that decision, so the literal
string `"auto"` is how the model says "someone else has already decided". It is
a real answer, not an absence — that distinction is the whole design, and it is
what the coercion cases below pin.

WHAT IS DELIBERATELY *NOT* ASSERTED
-----------------------------------
That the field is `Optional[str] = None`. It is, and
`test_the_field_itself_is_still_optional` says so — but as a POSITIVE CONTROL
with its reason attached, not as a requirement. The refusal is enforced by the
model validator, not by the field, and a future change that makes the field
itself required would satisfy every refusal case here while quietly breaking
`mode="page"`, whose dashboards have no theme index to choose from.
"""
import pytest
from pydantic import ValidationError

from app.ai.tools.schemas.create_artifact import CreateArtifactInput

# The sentinel, verbatim. A deck that sends it is complete.
AUTO = "auto"


def slides(**overrides):
    """A minimal, otherwise-valid slides call.

    `prompt` is the only other required field (`title`, `file_ids`,
    `visualization_ids` and `theme_id` all carry defaults), so anything that
    fails here failed on the theme and not on scaffolding.
    """
    kwargs = {"prompt": "quarterly revenue review for the board", "mode": "slides"}
    kwargs.update(overrides)
    return CreateArtifactInput(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Omitting it is not an answer
# ═══════════════════════════════════════════════════════════════════════════


def test_a_deck_that_names_no_theme_is_refused():
    with pytest.raises(ValidationError):
        slides()


def test_the_refusal_tells_the_model_about_auto():
    """The message is read by the model, so it is part of the contract.

    A refusal that only says "this field is required" pushes the model to
    invent a theme id in the two cases where the choice was already made
    deliberately by somebody else — which is the outcome `auto` exists to
    prevent.
    """
    with pytest.raises(ValidationError) as caught:
        slides()

    assert AUTO in str(caught.value)


def test_an_explicit_null_is_refused_the_same_way():
    """`{"theme_id": null}` is the shape a model produces when it means "none".

    Coercion answers None for it, so it must arrive at the same refusal as
    omission — otherwise there are two ways to skip the question and only one
    of them is closed.
    """
    with pytest.raises(ValidationError) as caught:
        slides(theme_id=None)

    assert AUTO in str(caught.value)


def test_an_empty_string_is_refused_too():
    """Whitespace is not a design system. Same door, same message."""
    with pytest.raises(ValidationError) as caught:
        slides(theme_id="   ")

    assert AUTO in str(caught.value)


# ═══════════════════════════════════════════════════════════════════════════
# `auto` is an answer, and survives the shapes a model sends it in
# ═══════════════════════════════════════════════════════════════════════════


def test_auto_padded_and_shouted_still_arrives_as_auto():
    """`" AUTO "` is the same answer as `"auto"`, and normalises to it.

    The exact lowercase string matters downstream: a resolver comparing against
    the sentinel by equality would miss `"AUTO"`, and the deck would be built
    against a theme literally named "AUTO", which does not exist — so it would
    silently fall through to the default the sentinel was chosen to avoid.
    """
    assert slides(theme_id=" AUTO ").theme_id == AUTO


def test_auto_sent_as_an_object_still_arrives_as_auto():
    """`{"theme_id": "auto"}` is a shape models genuinely produce.

    ★This repo measured `clarify` failing 79% of live calls purely on argument
    SHAPE. A design choice must degrade to a recognised answer rather than
    throw the whole `create_artifact` call away.
    """
    assert slides(theme_id={"theme_id": AUTO}).theme_id == AUTO


def test_a_real_theme_id_passes_through_untouched():
    """★THE POSITIVE CONTROL for every refusal above.

    A validator that refused everything satisfies all four refusal cases. This
    is the one that fails if the gate is ever widened into a wall.
    """
    assert slides(theme_id="boardroom").theme_id == "boardroom"


def test_a_theme_id_is_not_lowercased_on_its_way_through():
    """Only the sentinel is normalised; an id is copied exactly as sent.

    The field description tells the model to copy the id exactly as the index
    prints it, and a misspelling is deliberately NOT the theme it meant. A
    validator quietly case-folding ids would make that instruction untrue.
    """
    assert slides(theme_id="  Boardroom  ").theme_id == "Boardroom"


# ═══════════════════════════════════════════════════════════════════════════
# A dashboard is not a deck
# ═══════════════════════════════════════════════════════════════════════════


def test_a_dashboard_that_names_no_theme_is_fine():
    """★THE OTHER POSITIVE CONTROL, and the one that keeps this scoped.

    `mode="page"` has no theme index to choose from, so requiring an answer
    there would refuse every dashboard call in the product. A fix that moved
    the requirement onto the FIELD rather than the model validator passes every
    slides case in this file and fails here.
    """
    page = CreateArtifactInput(prompt="sales dashboard", mode="page")

    assert page.theme_id is None


def test_the_default_mode_is_a_dashboard_and_needs_no_theme():
    """`mode` defaults to `page`, so an omitted mode is not a deck."""
    assert CreateArtifactInput(prompt="sales dashboard").theme_id is None


def test_the_field_itself_is_still_optional():
    """★The positive control for the CONTRACT, not for the behaviour.

    The refusal is the model validator's job. If somebody ever "simplifies" it
    by making the field required, every refusal case above still passes and
    `mode="page"` breaks — so this pins where the rule lives, and says why.
    """
    field = CreateArtifactInput.model_fields["theme_id"]

    assert field.default is None
    assert not field.is_required(), (
        "theme_id became a required FIELD; the requirement belongs to the "
        "model validator so that page mode stays unaffected — see "
        "test_a_dashboard_that_names_no_theme_is_fine"
    )
