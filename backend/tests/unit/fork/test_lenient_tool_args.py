"""The shapes a model actually sends must survive the trip into a tool.

Every tool that declares a `List[SomeModel]` field kept getting a flat list of
strings back instead, and the whole tool call was thrown away — 27 recorded
turns on this instance, 79% of them `clarify`, whose failure the person asking
the question reads as "Unable to complete task due to repeated tool validation
errors".

`app.ai.tools.schemas._lenient` widens what the schema accepts. These are the
two rules it must never break:

* **Anything unrecognised is passed through untouched**, so pydantic still
  reports genuine nonsense in its own words rather than this layer inventing a
  shape that hides the error.
* **A shape fix must not change the meaning.** ★Five table names are five
  tables in ONE grouping, not five groupings of one — and the wrong reading is
  perfectly valid against the schema, so nothing downstream would ever complain.
  `test_many_scalars_describe_one_object_not_many` is the assertion that catches
  it; it is the reason `one_object_from_scalars` exists separately at all.

Both helpers are BEFORE validators, so they are pure functions of the raw
argument and are tested as such — no model is constructed here.
"""

import pytest

from app.ai.tools.schemas._lenient import (
    objects_from_scalars,
    one_object_from_scalars,
)


def _twice(normalise, value):
    """A before-validator can run again over its own output — on a retry, or
    when a model is re-validated. The second pass must be a no-op."""
    return normalise(normalise(value))


# ── objects_from_scalars: one scalar means one object ────────────────────────

QUESTIONS = objects_from_scalars(text_key="text", aliases={"question": "text"})


def test_bare_strings_become_objects_keyed_by_the_schemas_name():
    """The failure that cost 9 clarify turns: `questions.0 is not a dict`."""
    assert QUESTIONS(["What region?", "Which year?"]) == [
        {"text": "What region?"},
        {"text": "Which year?"},
    ]


def test_the_key_a_person_would_have_chosen_is_renamed_to_the_one_the_schema_names():
    """`questions.0.text: Field required` — the model wrote `question`."""
    assert QUESTIONS([{"question": "What region?", "options": ["EMEA"]}]) == [
        {"text": "What region?", "options": ["EMEA"]}
    ]


def test_an_alias_never_clobbers_the_real_key():
    """A payload carrying both keys already said what it meant. Filling from
    the alias here would silently replace an answer the model got right."""
    out = QUESTIONS([{"question": "the alias", "text": "the real one"}])

    assert out[0]["text"] == "the real one"


def test_the_alias_key_does_not_survive_the_rename():
    """★The alias is dropped whether or not it was used. Leaving it behind is
    harmless only while every one of these schemas keeps pydantic's default
    `extra="ignore"`; the day one sets `extra="forbid"` a stray key becomes a
    validation error whose cause is nowhere near where it is raised."""
    out = QUESTIONS([{"question": "the alias", "text": "the real one"}])

    assert out == [{"text": "the real one"}]
    assert "question" not in out[0]


def test_an_alias_only_payload_leaves_no_residue():
    assert QUESTIONS([{"question": "What region?"}]) == [{"text": "What region?"}]


def test_an_alias_for_a_key_that_is_absent_from_this_payload_changes_nothing():
    assert QUESTIONS([{"text": "already right"}]) == [{"text": "already right"}]


def test_a_single_string_where_a_list_was_expected_is_a_list_of_one():
    assert QUESTIONS("What region?") == [{"text": "What region?"}]


def test_a_single_object_where_a_list_was_expected_is_a_list_of_one():
    assert QUESTIONS({"question": "What region?"}) == [{"text": "What region?"}]


def test_an_object_serialised_as_text_is_parsed_not_treated_as_the_text():
    """★Models sometimes send the nested argument correctly and then JSON-encode
    it. Read as prose it becomes a question whose text is a chunk of JSON — a
    shape that validates and is wrong about the request."""
    assert QUESTIONS(['{"question": "What region?", "options": ["EMEA"]}']) == [
        {"text": "What region?", "options": ["EMEA"]}
    ]


def test_a_list_serialised_as_text_is_parsed():
    assert QUESTIONS('["What region?", "Which year?"]') == [
        {"text": "What region?"},
        {"text": "Which year?"},
    ]


def test_a_serialised_object_at_the_top_level_is_parsed_and_wrapped():
    assert QUESTIONS('{"question": "What region?"}') == [{"text": "What region?"}]


def test_nothing_stays_nothing():
    """An optional field left unset must not become a list holding None."""
    assert QUESTIONS(None) is None


def test_a_sentence_that_merely_opens_with_a_brace_is_still_a_sentence():
    """★Invalid JSON is text, not an error and not a drop. Losing the item here
    would turn a malformed argument into a question that silently never gets
    asked."""
    assert QUESTIONS(["{not really json"]) == [{"text": "{not really json"}]
    assert QUESTIONS("{not really json") == [{"text": "{not really json"}]


def test_an_empty_list_stays_empty():
    assert QUESTIONS([]) == []


def test_a_value_this_layer_cannot_read_reaches_pydantic_untouched():
    """★The point of passing it through: `questions.0 is not a dict` naming the
    int is a better error than anything invented here."""
    assert QUESTIONS([7]) == [7]
    assert QUESTIONS([{"text": "a real one"}, 7]) == [{"text": "a real one"}, 7]


def test_something_that_is_not_a_list_at_all_reaches_pydantic_untouched():
    assert QUESTIONS(7) == 7
    assert QUESTIONS(True) is True


def test_no_aliases_is_a_supported_configuration():
    plain = objects_from_scalars(text_key="name")

    assert plain(["a", {"name": "b"}]) == [{"name": "a"}, {"name": "b"}]


def test_the_caller_gets_a_new_list_not_the_one_they_passed():
    """A before-validator that mutated its argument would edit the raw tool
    call the rest of the run reads back for logging and retries."""
    original = [{"question": "What region?"}]

    QUESTIONS(original)

    assert original == [{"question": "What region?"}]


@pytest.mark.parametrize(
    "value",
    [
        ["What region?", "Which year?"],
        [{"question": "What region?", "options": ["EMEA"]}],
        [{"question": "the alias", "text": "the real one"}],
        [{"question": "What region?"}],
        "What region?",
        {"question": "What region?"},
        '{"question": "What region?"}',
        '["What region?"]',
        ["{not really json"],
        [7],
        [],
        None,
        7,
    ],
)
def test_normalising_an_already_normalised_value_changes_nothing(value):
    """★Idempotence is what makes this safe to sit in front of a model that may
    be validated more than once. A second pass that renamed or re-wrapped
    anything would make a retry behave differently from the first attempt."""
    assert _twice(QUESTIONS, value) == QUESTIONS(value)


# ── one_object_from_scalars: many scalars describe ONE object ────────────────

TABLES = one_object_from_scalars(list_key="tables", extra={"data_source_id": None})


def test_many_scalars_describe_one_object_not_many():
    """★The assertion this file exists for. Five table names are five tables in
    one grouping; per-item collapsing yields five sources holding one table
    each, which is valid against the schema, passes every other test here, and
    is wrong about what was asked. Only the length catches it."""
    out = TABLES(["fact_sales", "dim_product", "dim_date"])

    assert len(out) == 1
    assert out[0]["tables"] == ["fact_sales", "dim_product", "dim_date"]


def test_a_single_scalar_is_still_one_object():
    assert TABLES("fact_sales") == [{"data_source_id": None, "tables": ["fact_sales"]}]


def test_the_defaults_are_merged_into_the_object_this_layer_builds():
    """The grouping the model omitted still has to carry the fields the schema
    requires, or the shape fix trades one validation error for another."""
    out = TABLES(["fact_sales"])

    assert out[0]["data_source_id"] is None
    assert out[0]["tables"] == ["fact_sales"]


def test_defaults_never_overwrite_the_list_the_model_sent():
    collides = one_object_from_scalars(list_key="tables", extra={"tables": []})

    assert collides(["fact_sales"]) == [{"tables": ["fact_sales"]}]


def test_an_already_correct_payload_is_left_exactly_as_it_arrived():
    """Most calls are already right; this layer must be invisible to them."""
    correct = [
        {"data_source_id": 3, "tables": ["fact_sales"]},
        {"data_source_id": 4, "tables": ["dim_product", "dim_date"]},
    ]

    assert TABLES(correct) == correct


def test_loose_strings_beside_real_objects_are_gathered_into_one_extra_object():
    """A model that gets half of it right must not have the other half spread
    across an object each."""
    out = TABLES(
        [{"data_source_id": 3, "tables": ["fact_sales"]}, "dim_product", "dim_date"]
    )

    assert len(out) == 2
    assert out[0] == {"data_source_id": 3, "tables": ["fact_sales"]}
    assert out[1] == {"data_source_id": None, "tables": ["dim_product", "dim_date"]}


def test_no_loose_strings_means_no_extra_object_is_invented():
    """An empty grouping would be a data source the request never named."""
    correct = [{"data_source_id": 3, "tables": ["fact_sales"]}]

    assert TABLES(correct) == correct
    assert TABLES([]) == []


def test_a_list_serialised_as_text_is_parsed_into_one_object():
    assert TABLES('["fact_sales", "dim_product"]') == [
        {"data_source_id": None, "tables": ["fact_sales", "dim_product"]}
    ]


def test_a_serialised_object_inside_the_list_is_parsed_not_gathered_as_a_name():
    """Read as a scalar it would become a "table" whose name is a JSON blob."""
    assert TABLES(['{"data_source_id": 3, "tables": ["fact_sales"]}']) == [
        {"data_source_id": 3, "tables": ["fact_sales"]}
    ]


def test_nothing_stays_nothing_here_too():
    assert TABLES(None) is None


def test_a_single_grouping_sent_unwrapped_is_a_list_of_one():
    """★The mistake this helper exists for, one level up: the model writes the
    object it was asked for and forgets it belongs in a list. Passing it through
    to pydantic gets "input should be a valid list" — a thrown-away tool call
    for a payload that was otherwise entirely correct."""
    plain = one_object_from_scalars(list_key="tables")

    assert plain({"data_source_id": "abc", "tables": ["fact_sales"]}) == [
        {"data_source_id": "abc", "tables": ["fact_sales"]}
    ]


def test_a_bare_object_and_a_list_holding_it_are_read_the_same_way():
    """The wrapping must be exactly that — no defaults merged in, no gathering,
    nothing that makes the unwrapped spelling mean something different."""
    grouping = {"data_source_id": 3, "tables": ["fact_sales"]}

    assert TABLES(grouping) == TABLES([grouping]) == [grouping]


def test_something_that_is_not_a_list_reaches_pydantic_untouched():
    """A dict is no longer in this set — see the wrapping test above."""
    assert TABLES(7) == 7
    assert TABLES(True) is True


def test_a_value_this_layer_cannot_read_keeps_its_place_in_the_list():
    """Ints are not strings, so they are not names — pydantic names the offender."""
    assert TABLES([7]) == [7]
    assert TABLES([7, "fact_sales"]) == [7, {"data_source_id": None, "tables": ["fact_sales"]}]


def test_a_brace_that_is_not_json_is_a_table_name_not_a_dropped_item():
    assert TABLES(["{not really json"]) == [
        {"data_source_id": None, "tables": ["{not really json"]}
    ]


def test_the_defaults_are_not_shared_between_calls():
    """★A single dict handed out repeatedly would let one tool call's edits show
    up in the next one's arguments."""
    first = TABLES(["fact_sales"])
    first[0]["data_source_id"] = 99

    assert TABLES(["dim_product"])[0]["data_source_id"] is None


def test_the_caller_gets_a_new_list_here_too():
    original = [{"data_source_id": 3, "tables": ["fact_sales"]}, "dim_product"]

    TABLES(original)

    assert original == [{"data_source_id": 3, "tables": ["fact_sales"]}, "dim_product"]


@pytest.mark.parametrize(
    "value",
    [
        ["fact_sales", "dim_product"],
        [{"data_source_id": 3, "tables": ["fact_sales"]}],
        [{"data_source_id": 3, "tables": ["fact_sales"]}, "dim_product"],
        {"data_source_id": 3, "tables": ["fact_sales"]},
        {"tables": ["fact_sales"]},
        "fact_sales",
        '["fact_sales"]',
        ["{not really json"],
        [7],
        [],
        None,
        7,
    ],
)
def test_gathering_an_already_gathered_value_changes_nothing(value):
    """★The dangerous direction is a second pass re-gathering the object this
    layer built — that would nest a grouping inside a grouping."""
    assert _twice(TABLES, value) == TABLES(value)


def test_the_two_helpers_disagree_on_purpose():
    """Same input, two readings, and picking the wrong one is invisible to
    pydantic. Pinned here so the distinction cannot be "simplified" away."""
    scalars = ["fact_sales", "dim_product"]

    assert len(objects_from_scalars(text_key="tables")(scalars)) == 2
    assert len(one_object_from_scalars(list_key="tables")(scalars)) == 1


def test_the_two_helpers_are_meant_to_agree_about_objects():
    """★The distinction is about SCALARS and nothing else. Both helpers have
    always passed objects through unchanged, and both now wrap a bare one — so
    the dict-wrapping is not a place the two could have quietly converged."""
    grouping = {"data_source_id": 3, "tables": ["fact_sales"]}

    assert (
        objects_from_scalars(text_key="tables")(grouping)
        == one_object_from_scalars(list_key="tables")(grouping)
        == [grouping]
    )
