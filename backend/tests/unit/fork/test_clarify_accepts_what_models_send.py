"""`clarify` must accept the argument shapes models really send.

Measured on the live instance: `clarify` failed **11 of 14 calls — 79%**, and
its failure is the one a user sees. The person asks something vague, waits
~25s, and is told "Unable to complete task due to repeated tool validation
errors" instead of being asked a question — while the completion is still
recorded `status=success`.

Every payload below is a REAL recorded failure, not an invented one:

  * `questions.0: Input should be a valid dictionary or instance of
    ClarifyQuestion` — 9 times. The model sent a list of plain strings,
    e.g. `["What would you like ranked as the best?"]`, and sometimes a
    JSON-encoded string of the object instead of the object.
  * `questions.0.text: Field required` — 2 times. The model sent
    `[{"question": ..., "options": [...], "multi_select": false}]`: the right
    shape under the name a person would give the field.

The fix widens what the SCHEMA accepts (`app.ai.tools.schemas._lenient`), and
leaves `ClarifyQuestion` itself untouched as the canonical shape — so the last
block here is the positive control that proves the leniency did not quietly
become "accept anything".
"""
import json

import pytest
from pydantic import ValidationError

from app.ai.tools.schemas.clarify import ClarifyInput, ClarifyQuestion


# ---------------------------------------------------------------------------
# 1. the 9-failure shape — a list of bare strings
# ---------------------------------------------------------------------------

BARE_STRING_PAYLOADS = [
    ["What would you like ranked as the best?"],
    ["Which region?", "Which year?"],
]


@pytest.mark.parametrize("questions", BARE_STRING_PAYLOADS)
def test_a_list_of_bare_strings_becomes_questions(questions):
    parsed = ClarifyInput(questions=questions)

    assert [q.text for q in parsed.questions] == questions
    assert all(isinstance(q, ClarifyQuestion) for q in parsed.questions)
    # nothing is invented on the way through
    assert all(q.options is None and q.multi_select is False for q in parsed.questions)


def test_a_json_encoded_question_is_parsed_not_rejected():
    """The same 9-failure class, arriving encoded. Rejecting a correctly shaped
    payload over its transport is the most confusing failure of the set."""
    parsed = ClarifyInput(
        questions=[json.dumps({"text": "Which store?", "options": ["A", "B"]})]
    )

    assert parsed.questions[0].text == "Which store?"
    assert parsed.questions[0].options == ["A", "B"]


# ---------------------------------------------------------------------------
# 2. the 2-failure shape — the field named `question`
# ---------------------------------------------------------------------------


def test_the_field_named_question_maps_to_text():
    """Recorded verbatim: the model got the structure right and the key wrong."""
    parsed = ClarifyInput(
        questions=[
            {
                "question": "Which metric should rank them?",
                "options": ["Revenue", "Units", "Other…"],
                "multi_select": False,
            }
        ]
    )

    assert parsed.questions[0].text == "Which metric should rank them?"
    assert parsed.questions[0].options == ["Revenue", "Units", "Other…"]
    assert parsed.questions[0].multi_select is False


def test_a_payload_that_already_uses_text_is_unchanged():
    """The canonical shape is still the canonical shape — a widened schema that
    changed correct input would be a regression dressed as a fix."""
    parsed = ClarifyInput(
        questions=[{"text": "Which year?", "options": ["2024", "2025"], "multi_select": True}],
        context="user asked for 'the best' with no metric",
    )

    assert parsed.questions[0].text == "Which year?"
    assert parsed.questions[0].options == ["2024", "2025"]
    assert parsed.questions[0].multi_select is True
    assert parsed.context == "user asked for 'the best' with no metric"


def test_both_text_and_question_keeps_text():
    """★The alias must never overwrite the real key. A model that hedges by
    sending both is answered with the field the schema names, not the one it
    guessed — otherwise the alias becomes a way to override the schema."""
    parsed = ClarifyInput(
        questions=[{"text": "Which region?", "question": "something else entirely"}]
    )

    assert parsed.questions[0].text == "Which region?"


# ---------------------------------------------------------------------------
# 3. ★ the positive control — nonsense is still refused
# ---------------------------------------------------------------------------
#
# Without this block every test above is satisfied by deleting the schema.

NONSENSE = [
    pytest.param([123], id="a number is not a question"),
    pytest.param([{}], id="an object with no text at all"),
    pytest.param([{"options": ["A", "B"]}], id="options with nothing to answer"),
    pytest.param([""], id="an empty string is not a question"),
    pytest.param([None], id="a null entry"),
    pytest.param([], id="no questions is not a clarification"),
    pytest.param([{"text":
                   ["a", "list"]}], id="text is not a list"),
]


@pytest.mark.parametrize("questions", NONSENSE)
def test_genuine_nonsense_is_still_rejected(questions):
    with pytest.raises(ValidationError):
        ClarifyInput(questions=questions)


def test_clarify_question_itself_was_not_weakened():
    """The leniency lives on the FIELD. The model stays strict, so anything
    constructing a question directly gets the same guarantees as before."""
    with pytest.raises(ValidationError):
        ClarifyQuestion()
    with pytest.raises(ValidationError):
        ClarifyQuestion(text="")

    q = ClarifyQuestion(text="Which region?")
    assert q.options is None and q.multi_select is False
