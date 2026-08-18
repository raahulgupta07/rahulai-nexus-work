"""Accept the argument shapes models actually produce.

★Every tool here declares a `List[SomeModel]` field, and models keep sending a
list of plain strings instead — or a dict keyed the way a person would name it
rather than the way the schema does. Measured on this instance:

    clarify        questions.0 is not a dict            9 failures
                   questions.0.text: Field required     2   (model wrote `question`)
    create_data    tables_by_source.0 is not a dict    11
                   tables_by_source.0.tables required   4
    create_prompt  parameters.0 is not a dict           1

That is 27 turns where the tool call was thrown away. `clarify` fails 79% of the
time, and its failure is user-visible: the person asks something vague, waits,
and gets "Unable to complete task due to repeated tool validation errors"
instead of a question.

★The durable fix is here, not in the prompt. Instructions telling a model to
nest its arguments do not reliably stick — the same schema keeps getting the
same flat list back. Widening what the schema accepts costs nothing and removes
the failure permanently, and the strict shape stays the canonical one so nothing
downstream has to change.

★Both helpers are BEFORE validators: they normalise, then hand over to the real
model, which still rejects genuine nonsense. Anything unrecognised is passed
through untouched so pydantic produces its own error rather than a confusing one
from here.
"""

import json
from typing import Any, Callable, Dict, Optional


def _maybe_json(value: Any) -> Any:
    """Unwrap a JSON document that arrived as a string.

    ★Models sometimes serialise a nested argument and send it as text —
    `'{"question": "...", "options": [...]}'` — which fails the same way a bare
    sentence does but for a completely different reason. Parsing first means a
    correctly-shaped payload is not rejected over its encoding.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return value


def objects_from_scalars(
    *, text_key: str, aliases: Optional[Dict[str, str]] = None
) -> Callable[[Any], Any]:
    """For a list whose items are each their own object.

    ``["What region?", "Which year?"]``
        → ``[{text_key: "What region?"}, {text_key: "Which year?"}]``

    ``[{"question": "...", "options": [...]}]`` with ``aliases={"question": text_key}``
        → ``[{text_key: "...", "options": [...]}]``

    Use where one string means one item — clarify questions, prompt parameters.
    """
    alias_map = aliases or {}

    def _normalise(value: Any) -> Any:
        value = _maybe_json(value)
        if value is None:
            return value
        # A single item where a list was expected is a list of one.
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, list):
            return value

        out = []
        for item in value:
            item = _maybe_json(item)
            if isinstance(item, str):
                out.append({text_key: item})
                continue
            if isinstance(item, dict):
                renamed = dict(item)
                for wrong, right in alias_map.items():
                    if wrong not in renamed:
                        continue
                    # ★Only FILL from the alias when the real key is absent, so a
                    # payload carrying both keeps the one the schema names — but
                    # drop the alias key either way. Leaving it behind is
                    # harmless only for as long as every one of these schemas
                    # keeps pydantic's default `extra="ignore"`; the day one sets
                    # `extra="forbid"` the stray key becomes a validation error,
                    # and the cause would be very hard to find from here.
                    value_ = renamed.pop(wrong)
                    renamed.setdefault(right, value_)
                out.append(renamed)
                continue
            out.append(item)  # let pydantic report it
        return out

    return _normalise


def one_object_from_scalars(
    *,
    list_key: str,
    extra: Optional[Dict[str, Any]] = None,
    aliases: Optional[Dict[str, str]] = None,
) -> Callable[[Any], Any]:
    """For a list of scalars that together describe ONE object.

    ``["fact_sales", "dim_product"]``
        → ``[{list_key: ["fact_sales", "dim_product"]}]``

    ★Deliberately different from :func:`objects_from_scalars`. Five table names
    are five tables in one grouping, not five separate groupings — collapsing
    them per-item would produce five sources each holding one table, which is
    valid against the schema and wrong about the request. A shape fix that
    changes the meaning is worse than the error it replaced.

    Mixed input (some strings, some objects) keeps the objects as they are and
    gathers the loose strings into a single additional grouping.

    ★``aliases`` renames the keys models reach for instead of the schema's own.
    Gathering loose strings was only half the problem: the other half is a dict
    that IS the right shape but names its list something else. Observed live
    after the first fix shipped — ``[{"connection_id": …, "table_names":
    ["fact_sales"]}]`` — which sailed past a normaliser that only looked at
    strings and was rejected by the strict model as ``tables: Field required``.
    """
    defaults = extra or {}
    alias_map = aliases or {}

    def _normalise(value: Any) -> Any:
        value = _maybe_json(value)
        if value is None:
            return value
        # ★A single grouping sent unwrapped is the SAME mistake this helper
        # exists for, one level up: the model writes the object it was asked
        # for and forgets it belongs in a list. The sibling helper already
        # accepts that, and leaving the two asymmetric would mean a model that
        # sends `{"tables": [...]}` fails here while the identical habit is
        # tolerated elsewhere — a difference no one could predict from outside.
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, list):
            return value

        loose: list = []
        out: list = []
        for item in value:
            item = _maybe_json(item)
            if isinstance(item, str):
                loose.append(item)
                continue
            if isinstance(item, dict) and alias_map:
                renamed = dict(item)
                for wrong, right in alias_map.items():
                    if wrong not in renamed:
                        continue
                    value_ = renamed.pop(wrong)
                    # ★A singular alias carries a singular VALUE.
                    # `{"name": "LK_CFC_Sales.dbo.cfc_champion"}` renames to
                    # `{"tables": "LK_CFC_Sales.dbo.cfc_champion"}` — a bare
                    # string where the field is `List[str]`, so the rename fixes
                    # the key and the model rejects the value anyway. Measured
                    # live: that exact dict is what the planner sends.
                    if right == list_key and isinstance(value_, str):
                        value_ = [value_]
                    renamed.setdefault(right, value_)
                out.append(renamed)
                continue
            out.append(item)
        if loose:
            out.append({**defaults, list_key: loose})
        return out

    return _normalise
