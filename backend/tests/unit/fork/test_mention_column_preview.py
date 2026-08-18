"""An @table mention handed the agent a dict's repr instead of its columns.

`MentionContextBuilder` previewed a mentioned table's columns with

    name = getattr(c, "name", None) or str(c)
    dtype = getattr(c, "dtype", None)

but `DataSourceTable.columns` is a JSON column: a list of **dicts**, not of
objects. `getattr` against a dict does not raise — it MISSES — so `name` fell
through to `str(c)` and `dtype` came back `None`, and every @table mention
reached the model as

    {'name': 'Sales', 'dtype': 'bigint'}:None,  {'name': 'Region', ...}:None

instead of `Sales:bigint, Region:text`. Nothing errored, nothing logged, and the
string still looked like a column preview, which is exactly why it survived in a
prompt-facing surface. ★A getattr that silently misses is worse than a crash:
the fallback manufactures a plausible-looking string.

Measured against the live database 2026-08-17: all 106 `datasource_tables` rows
store dicts, so this was firing on every mention on this install, not on an edge
case. `Entity.data["columns"]` — copied verbatim from `Step.data`, which
`format_df_for_widget` writes as `{"field", "headerName"}` dicts — had the same
fault one branch down (`[str(c) for c in cols]`); it is latent only because this
install holds zero entities today.

★RED-PROOF: the whole file was run against a faithful reconstruction of the
pre-fix module — **12 of 17 fail there**, including every dict case and both
entity dict cases. The five that pass on both are named as such below: the three
empty/missing cases, the no-overflow counter, and the entity plain-string case.
They pin that the fix did not buy the dict case by breaking a shape that already
worked, or by inventing a preview where there is no column data.

★The two `TestObjectColumnsStillRender` cases also fail pre-fix, and NOT because
the object path was broken — `test_a_tablecolumn_object_renders_the_same_as_its_dict`
compares the two paths (the dict side is what fails), and the other two assert
behaviour this change adds (`description`, and a bare name instead of
`Sales:None`). Read them as parity assertions, not as evidence the object shape
was ever mishandled.
"""
import pytest

from app.ai.context.builders.mention_context_builder import MentionContextBuilder
from app.ai.prompt_formatters import TableColumn
from app.models.data_source import DataSource
from app.models.datasource_table import DataSourceTable
from app.models.entity import Entity
from app.models.mention import Mention, MentionType


# ── a fake session: this builder only ever `execute`s the mention query and
#    `get`s objects by id, so a dict lookup is the whole of it ─────────────────
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


class _FakeDB:
    def __init__(self, mentions, objects):
        self._mentions = mentions
        self._objects = objects

    async def execute(self, _stmt):
        return _Result(self._mentions)

    async def get(self, _model, obj_id):
        return self._objects.get(str(obj_id))


class _Completion:
    id = "completion-1"


def _table_mention(tbl, objects=None):
    """One TABLE mention pointing at `tbl`, with its data source resolvable."""
    ds = DataSource(name="warehouse")
    objects = dict(objects or {})
    objects.update({"tbl-1": tbl, "ds-1": ds})
    m = Mention(type=MentionType.TABLE, object_id="tbl-1", mention_content="sales")
    return _FakeDB([m], objects)


def _legacy_table(columns):
    """A `DataSourceTable` as the database actually stores one."""
    tbl = DataSourceTable(name="sales", columns=columns)
    tbl.datasource_id = "ds-1"
    tbl.data_source_id = "ds-1"
    return tbl


async def _preview(tbl, **kw):
    builder = MentionContextBuilder(_table_mention(tbl), None, None, _Completion())
    section = await builder.build(**kw)
    assert len(section.tables) == 1
    return section.tables[0].columns_preview


# ─────────────────── the bug ───────────────────
class TestDictColumnsRenderTheirNames:
    @pytest.mark.asyncio
    async def test_a_stored_dict_column_renders_name_and_dtype(self):
        """★The whole fault in one assertion."""
        preview = await _preview(_legacy_table([{"name": "Sales", "dtype": "bigint"}]))
        assert preview == ["Sales:bigint"]

    @pytest.mark.asyncio
    async def test_no_column_reaches_the_prompt_as_a_dict_repr(self):
        """The old output was a plausible-looking string, so pin its shape too —
        a future 'tidy-up' that reintroduces `str(c)` passes the test above only
        if it also keeps the braces out."""
        preview = await _preview(_legacy_table([
            {"name": "Sales", "dtype": "bigint"},
            {"name": "Region", "dtype": "text"},
        ]))
        assert preview == ["Sales:bigint", "Region:text"]
        joined = ", ".join(preview)
        assert "{" not in joined and "'name'" not in joined and ":None" not in joined

    @pytest.mark.asyncio
    async def test_a_column_with_no_dtype_renders_as_a_bare_name(self):
        """★Never `Sales:None`. The literal string "None" reads to the model as
        a type it should reason about — the failure this file exists to close
        put that word next to EVERY column."""
        preview = await _preview(_legacy_table([{"name": "Sales"}]))
        assert preview == ["Sales"]

    @pytest.mark.asyncio
    async def test_a_column_description_reaches_the_prompt(self):
        """Cheap — no column in the live database carries one today — and it is
        the thing that tells the agent which column to use. Parenthesised
        because `MentionsSection` joins this list with ", "."""
        preview = await _preview(_legacy_table([
            {"name": "Sales", "dtype": "bigint",
             "description": "Net of returns, in MMK"},
        ]))
        assert preview == ["Sales:bigint (Net of returns, in MMK)"]

    @pytest.mark.asyncio
    async def test_a_long_description_is_trimmed_not_dropped(self):
        preview = await _preview(_legacy_table([
            {"name": "Sales", "dtype": "bigint", "description": "x" * 400},
        ]))
        assert preview[0].startswith("Sales:bigint (xxx")
        assert preview[0].endswith("...)")
        assert len(preview[0]) < 100


# ─────────────────── the shape that already worked ───────────────────
class TestObjectColumnsStillRender:
    """★Positive control for the regression the fix could have caused. A
    `TableColumn` is what anything that has been through `to_prompt_table()`
    hands over, and it is the shape the original `getattr` was written for."""

    @pytest.mark.asyncio
    async def test_a_tablecolumn_object_renders_the_same_as_its_dict(self):
        objects = await _preview(_legacy_table([
            TableColumn(name="Sales", dtype="bigint"),
            TableColumn(name="Region", dtype="text"),
        ]))
        dicts = await _preview(_legacy_table([
            {"name": "Sales", "dtype": "bigint"},
            {"name": "Region", "dtype": "text"},
        ]))
        assert objects == dicts == ["Sales:bigint", "Region:text"]

    @pytest.mark.asyncio
    async def test_an_object_carries_its_description_too(self):
        preview = await _preview(_legacy_table([
            TableColumn(name="Sales", dtype="bigint", description="Net of returns"),
        ]))
        assert preview == ["Sales:bigint (Net of returns)"]

    @pytest.mark.asyncio
    async def test_a_bare_string_column_still_names_itself(self):
        """`file_preview` stores `list(df.columns)` — plain strings."""
        assert await _preview(_legacy_table(["Sales", "Region"])) == ["Sales", "Region"]


# ─────────────────── boundaries ───────────────────
class TestEmptyAndMissing:
    @pytest.mark.asyncio
    async def test_an_empty_column_list_does_not_raise(self):
        assert await _preview(_legacy_table([])) is None

    @pytest.mark.asyncio
    async def test_a_table_with_no_columns_at_all_does_not_raise(self):
        """`DataSourceTable.columns` is nullable — a migrated row may hold NULL."""
        assert await _preview(_legacy_table(None)) is None

    @pytest.mark.asyncio
    async def test_the_table_still_reaches_the_prompt_without_columns(self):
        builder = MentionContextBuilder(
            _table_mention(_legacy_table([])), None, None, _Completion())
        section = await builder.build()
        assert section.tables[0].table_name == "sales"
        assert section.tables[0].data_source_name == "warehouse"


class TestOverflowCounter:
    @pytest.mark.asyncio
    async def test_the_counter_reports_what_was_withheld(self):
        cols = [{"name": f"c{i}", "dtype": "text"} for i in range(11)]
        preview = await _preview(_legacy_table(cols), max_columns_preview=8)
        assert len(preview) == 9
        assert preview[:2] == ["c0:text", "c1:text"]
        assert preview[-1] == "+3"

    @pytest.mark.asyncio
    async def test_no_counter_when_everything_fits(self):
        cols = [{"name": f"c{i}", "dtype": "text"} for i in range(8)]
        preview = await _preview(_legacy_table(cols), max_columns_preview=8)
        assert len(preview) == 8
        assert not any(p.startswith("+") for p in preview)

    @pytest.mark.asyncio
    async def test_the_counter_survives_a_column_it_cannot_name(self):
        """★The counter used to be `len(all) - len(preview)`, so a column that
        rendered to nothing was silently re-counted as "+1 more" — promising the
        agent a column it would never be shown. Count off the SHOWN SLICE."""
        cols = [{"name": "Sales", "dtype": "bigint"}, {"dtype": "text"}]
        preview = await _preview(_legacy_table(cols), max_columns_preview=8)
        assert preview == ["Sales:bigint"]


# ─────────────────── the same fault, one branch down ───────────────────
class TestEntityColumnsAreNamesNotReprs:
    """`Entity.data` is a verbatim copy of `Step.data`
    (`entity_service.create_entity_from_step`), and `format_df_for_widget`
    writes columns as `{"field": ..., "headerName": ...}`. `[str(c) for c in
    cols]` rendered those dicts whole."""

    @pytest.fixture(autouse=True)
    def _visible(self, monkeypatch):
        """Stand in for the viewer-data policy, which queries the database to
        decide whether this reader may see the snapshot at all. Patched to the
        VISIBLE answer so these tests are about rendering; whether a reader is
        withheld is `viewer_data_policy`'s own guard, not this one's. ★The
        builder swallows exceptions per item, so without this the columns come
        back `None` and every assertion below would have been testing the
        swallow rather than the fix — which is exactly how they failed first."""
        from app.services import viewer_data_policy

        async def _resolve(db, entity, user=None):
            return entity.data or {}

        monkeypatch.setattr(viewer_data_policy, "resolve_entity_data", _resolve)

    async def _entity_preview(self, data, **kw):
        ent = Entity(title="Q3 revenue", data=data)
        m = Mention(type=MentionType.ENTITY, object_id="ent-1",
                    mention_content="Q3 revenue")
        db = _FakeDB([m], {"ent-1": ent})
        section = await MentionContextBuilder(db, None, None, _Completion()).build(**kw)
        return section.entities[0].columns

    @pytest.mark.asyncio
    async def test_widget_shaped_columns_render_their_field_names(self):
        cols = await self._entity_preview(
            {"columns": [{"field": "region", "headerName": "region"},
                         {"field": "net_sales", "headerName": "net_sales"}],
             "rows": []})
        assert cols == ["region", "net_sales"]

    @pytest.mark.asyncio
    async def test_plain_string_columns_are_untouched(self):
        """The shape the original `str(c)` was written for."""
        cols = await self._entity_preview({"columns": ["region", "net_sales"]})
        assert cols == ["region", "net_sales"]

    @pytest.mark.asyncio
    async def test_entity_columns_honour_the_preview_cap(self):
        cols = await self._entity_preview(
            {"columns": [{"field": f"c{i}"} for i in range(12)]},
            max_columns_preview=8)
        assert cols == [f"c{i}" for i in range(8)]
