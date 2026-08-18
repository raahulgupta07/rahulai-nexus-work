"""The argument shapes models actually send must not throw the call away.

`create_data` failed on its FIRST attempt in 56 of 455 live calls (12.3%). The
retry usually succeeded, so the user still got an answer — and paid an extra
round trip plus a red failed step in the transcript for it. Two shapes account
for 15 of those:

    tables_by_source.0: Input should be a valid dictionary or instance of
                        TablesBySource                                 11
    tables_by_source.0.tables: Field required                           4

and one more of the same family in `create_prompt`:

    parameters.0: Input should be a valid dictionary or instance of
                  PromptParameterSpec                                   1

The payloads below are the real ones, table names and all, taken verbatim from
the live database.

★The load-bearing assertion is the COUNT. Five table names are five tables in
ONE grouping. A fix built on `objects_from_scalars` would produce five
groupings of one table each — that passes every "no validation error" check,
satisfies the schema completely, and asks the wrong question of the wrong
sources. The wrong-helper mistake is invisible unless something counts.
"""

import pytest
from pydantic import ValidationError

from app.ai.tools.schemas.create_data import CreateDataInput
from app.ai.tools.schemas.create_prompt import CreatePromptInput
from app.ai.tools.schemas.create_widget import CreateWidgetInput


# Verbatim from the failing calls: the model listed the live DB's tables flat.
FLAT_TABLES = ["fact_sales", "dim_date", "dim_channel", "dim_product", "dim_category"]

BASE_DATA = {
    "title": "Sales by channel",
    "user_prompt": "sales by channel",
    "interpreted_prompt": "Total sales by channel for the latest day in the data",
}
BASE_WIDGET = {
    "widget_title": "Sales by channel",
    "user_prompt": "sales by channel",
    "interpreted_prompt": "Total sales by channel for the latest day in the data",
}


@pytest.mark.parametrize(
    "model, base",
    [(CreateDataInput, BASE_DATA), (CreateWidgetInput, BASE_WIDGET)],
    ids=["create_data", "create_widget"],
)
class TestTablesBySourceAcceptsAFlatList:
    """★Both tools declare the field, so both are pinned. `create_data` is where
    the failures were counted; `create_widget` shares the annotation and would
    otherwise be one edit away from diverging silently."""

    def test_five_table_names_are_one_grouping_of_five(self, model, base):
        """★The assertion that catches the wrong helper. Not "it validated" —
        how many groupings, and how many tables in them."""
        parsed = model(**base, tables_by_source=FLAT_TABLES)

        assert len(parsed.tables_by_source) == 1, (
            "the five table names were split into "
            f"{len(parsed.tables_by_source)} groupings — one string became one "
            "source, which changes the meaning of the request"
        )
        group = parsed.tables_by_source[0]
        assert group.tables == FLAT_TABLES
        assert len(group.tables) == 5
        # No source was named, and none may be invented: null means all sources.
        assert group.data_source_id is None

    def test_the_correct_shape_is_left_alone(self, model, base):
        """A normaliser that rewrites valid input is a new bug."""
        payload = [
            {"data_source_id": "3f2b1c00-0000-4000-8000-000000000001", "tables": ["fact_sales"]},
            {"data_source_id": None, "tables": ["dim_date", "dim_product"]},
        ]
        parsed = model(**base, tables_by_source=payload)

        assert len(parsed.tables_by_source) == 2
        assert parsed.tables_by_source[0].data_source_id == "3f2b1c00-0000-4000-8000-000000000001"
        assert parsed.tables_by_source[0].tables == ["fact_sales"]
        assert parsed.tables_by_source[1].data_source_id is None
        assert parsed.tables_by_source[1].tables == ["dim_date", "dim_product"]

    def test_a_mixed_list_keeps_its_objects_and_gathers_the_strings(self, model, base):
        """Half-nested is a real shape: the model gets one source right and
        then lapses. The proper grouping must survive untouched, and the loose
        names must land in ONE extra grouping rather than one each."""
        parsed = model(
            **base,
            tables_by_source=[
                {"data_source_id": "3f2b1c00-0000-4000-8000-000000000001", "tables": ["fact_sales"]},
                "dim_date",
                "dim_product",
            ],
        )

        assert len(parsed.tables_by_source) == 2
        kept, gathered = parsed.tables_by_source
        assert kept.data_source_id == "3f2b1c00-0000-4000-8000-000000000001"
        assert kept.tables == ["fact_sales"]
        assert gathered.data_source_id is None
        assert gathered.tables == ["dim_date", "dim_product"]

    def test_omitting_it_entirely_is_still_allowed(self, model, base):
        """The field is optional and the file/`source_file_ids` path relies on
        that. A BeforeValidator that chokes on None would break every
        file-backed call."""
        assert model(**base).tables_by_source is None
        assert model(**base, tables_by_source=None).tables_by_source is None

    # --- positive controls: the model itself stays strict -------------------

    def test_a_grouping_without_tables_is_still_rejected(self, model, base):
        """★`TablesBySource` was deliberately NOT loosened. Leniency lives on the
        field, so a dict that names a source and no tables still errors — the
        tool cannot guess which tables were meant."""
        with pytest.raises(ValidationError) as err:
            model(**base, tables_by_source=[{"data_source_id": "3f2b1c00-0000-4000-8000-000000000001"}])
        assert "tables" in str(err.value)

    def test_genuine_nonsense_is_still_rejected(self, model, base):
        """Non-string, non-dict items are passed through untouched so pydantic
        reports them itself, rather than being silently swallowed into a
        grouping the user never asked for."""
        with pytest.raises(ValidationError):
            model(**base, tables_by_source=[17, 42])
        with pytest.raises(ValidationError):
            model(**base, tables_by_source=[{"tables": "not a list of strings"}, {"tables": [{"a": 1}]}])


class TestPromptParametersAcceptAFlatList:
    """★Opposite helper, same family. One string is one PARAMETER here, so the
    count must go the other way from `tables_by_source`."""

    BASE = {"text": "Sales for {{region}} in {{year}}"}

    def test_a_list_of_strings_becomes_one_parameter_each(self):
        parsed = CreatePromptInput(**self.BASE, parameters=["region", "year"])

        assert len(parsed.parameters) == 2, (
            "two parameter names must not be collapsed into one parameter"
        )
        assert [p.name for p in parsed.parameters] == ["region", "year"]
        # Everything else keeps the schema's defaults — nothing is invented.
        assert parsed.parameters[0].type == "text"
        assert parsed.parameters[0].required is False

    def test_the_correct_shape_is_left_alone(self):
        parsed = CreatePromptInput(
            **self.BASE,
            parameters=[{"name": "region", "type": "enum", "options": ["EMEA", "APAC"], "required": True}],
        )
        assert len(parsed.parameters) == 1
        assert parsed.parameters[0].name == "region"
        assert parsed.parameters[0].type == "enum"
        assert parsed.parameters[0].options == ["EMEA", "APAC"]
        assert parsed.parameters[0].required is True

    def test_a_mixed_list_keeps_its_objects(self):
        parsed = CreatePromptInput(
            **self.BASE,
            parameters=[{"name": "region", "type": "enum", "options": ["EMEA"]}, "year"],
        )
        assert [p.name for p in parsed.parameters] == ["region", "year"]
        assert parsed.parameters[0].options == ["EMEA"]
        assert parsed.parameters[1].type == "text"

    def test_omitting_it_entirely_is_still_allowed(self):
        assert CreatePromptInput(**self.BASE).parameters is None

    def test_genuine_nonsense_is_still_rejected(self):
        """`PromptParameterSpec` stays strict: `name` is required, and a
        parameter with no name cannot be referenced by any {{placeholder}}."""
        with pytest.raises(ValidationError):
            CreatePromptInput(**self.BASE, parameters=[{"label": "Region"}])
        with pytest.raises(ValidationError):
            CreatePromptInput(**self.BASE, parameters=[17])
