"""Built-in skills that ship in the image.

A skill is an ``Instruction`` row with ``kind='skill'``: it is advertised in the
prompt as a one-line catalog entry and its full text is pulled on demand with
the ``read_instruction`` tool. Nothing here is force-loaded, so an unused skill
costs one catalog line and nothing else.

Why these three and not more
----------------------------
A nine-question suite built specifically to break the Fabric connector
(2026-07-25) scored 8/9. The model already brackets spaced identifiers, uses
``TOP`` rather than ``LIMIT``, ranges varchar dates as strings, and splits work
per lakehouse without being told. Restating any of that would spend prompt
budget teaching a model what it demonstrably knows.

What survived are rules a competent T-SQL model gets WRONG on Fabric because
they contradict SQL Server habits, plus one defect measured on a real run.

Provenance rule
---------------
Every Microsoft-sourced line carries its Learn URL. A rule with no citation
does not ship — that is exactly how an auto-generated overview came to assert a
join key whose two code spaces have zero values in common. ``sql-determinism``
is deliberately uncited and says so in its own text: it comes from our own
measurement, not from Microsoft.

Scope
-----
These are attached to Fabric data sources, never org-global. An unscoped
instruction is advertised to every agent in the organization.

★ Generic only
--------------
A built-in skill ships to EVERY user of a Fabric connector, and no two users
see the same tables — access is per-user (each signs in with their own
Microsoft account and gets their own overlay). So a skill must never name a
real table, column or value from any one deployment: to everyone else that is
noise at best and a wrong instruction at worst. Every example here uses
placeholder identifiers (`fact_table`, `dim_table`, `[Category]`, `Month`) that
the reader is expected to substitute. Anything deployment-specific belongs in
that data source's own overview instruction, which is generated per connector,
not here.
"""
from __future__ import annotations

from typing import Dict, List

# Bump when a skill's text changes so the seeder can update rows in place.
# v2: removed every deployment-specific table/column name in favour of
# placeholders — these ship to users whose table access is entirely different.
# v3: all three descriptions were over the catalog's 160-char cap (198/216/181)
# and were being truncated mid-sentence in the one line the planner uses to
# decide whether to open the skill. Rewritten to 155/158/159. Text only — no
# rule changed.
BUILTIN_SKILLS_VERSION = 3

# Connector types these skills apply to.
BUILTIN_SKILL_CONNECTOR_TYPES = ("fabric_user",)

_LEARN = "https://learn.microsoft.com/en-us/fabric"


BUILTIN_SKILLS: List[Dict] = [
    {
        "slug": "fabric-collation-and-joins",
        "title": "Fabric: case sensitivity and join keys",
        # The planner decides whether to pull a skill from this line alone, so
        # it carries the words a relevant question would use.
        # 155 chars. ★ The catalog truncates at 160 (_skill_description,
        # instruction_context_builder.py:660) and this line is the ONLY thing
        # the planner reads when deciding to pull the skill — a cut here costs
        # the whole skill, silently. All three shipped over the cap until
        # v3; keep every new one measured, not estimated.
        "description": (
            "Case-sensitive text matching, duplicate-looking GROUP BY values, and "
            "why a declared key is no proof two columns join. Read before "
            "grouping text or joining."
        ),
        "text": f"""\
## Case sensitivity

Every Fabric warehouse and every Lakehouse SQL analytics endpoint defaults to
the collation `Latin1_General_100_BIN2_UTF8`, which is **case-sensitive**. This
is the opposite of a typical SQL Server default.

Consequences that produce wrong answers rather than errors:

- `WHERE status = 'active'` does not match a stored `'Active'`.
- `GROUP BY [Category]` returns `'System'` and `'system'` as two separate rows.
  A "top 5" list can silently be a top 5 of fragments.
- `SELECT DISTINCT` and `JOIN ... ON a.code = b.code` are case-sensitive too.

When the business meaning of a column is case-insensitive, normalise it
explicitly: `GROUP BY UPPER(LTRIM(RTRIM([Category])))`. When two counts of the
"same" dimension disagree, check casing before assuming dirty data.

If a result set contains values differing only by case, say so in the answer —
do not silently merge them and do not silently report them as distinct
entities.

Source: {_LEARN}/data-warehouse/collation

Cross-database queries between items with *different* collations "could
encounter errors or unexpected query results". Collation cannot be changed
after an item is created.

## Join keys are never enforced

Fabric supports `PRIMARY KEY`, `UNIQUE` and `FOREIGN KEY` only as
`NOT ENFORCED` (`PRIMARY KEY` and `UNIQUE` additionally require
`NONCLUSTERED`). The engine never validates them, so a declared key proves
nothing about the data.

Microsoft states this directly: "since primary key and unique key constraints
aren't enforced, columns with these constraints aren't necessarily good
candidates for JOINs."

Therefore, before relying on a join between two tables you have not joined
before — especially one suggested by a column name, a data dictionary, or an
agent-written overview — **verify it against the data**:

```sql
SELECT COUNT(*) AS overlap
FROM (SELECT DISTINCT LTRIM(RTRIM(a.KeyCol)) AS k FROM SchemaA.TableA a) x
JOIN (SELECT DISTINCT LTRIM(RTRIM(b.KeyCol)) AS k FROM SchemaB.TableB b) y
  ON x.k = y.k;
```

An overlap of 0 means the two columns live in different code spaces and the
join is meaningless no matter how similar the names are. Report that finding
instead of returning an empty result as if it were an answer.

Sources: {_LEARN}/data-warehouse/table-constraints ·
{_LEARN}/data-warehouse/guidelines-warehouse-performance

## uniqueidentifier does not cross the boundary

A `uniqueidentifier` column is stored as binary and **cannot be read on a SQL
analytics endpoint**; reading it in a lakehouse shows a binary representation.
Cross-joins between a Warehouse and a SQL analytics endpoint on such a column
"don't work as expected". Pick a different key.

Source: {_LEARN}/data-warehouse/data-types
""",
    },
    {
        "slug": "fabric-tsql-surface",
        "title": "Fabric: unsupported T-SQL and data types",
        "description": (
            # 158 chars — see the cap note on the first skill.
            "T-SQL commands and data types Fabric rejects, and what a read-only "
            "Lakehouse endpoint refuses. Read before recursive CTEs, temp "
            "objects, DDL, DML or nvarchar."
        ),
        "text": f"""\
## The endpoint is read-only

"Creating, altering, and dropping tables, and insert, update, and delete are
only supported in Warehouse in Microsoft Fabric, **not in the SQL analytics
endpoint of the Lakehouse**."

Views, functions and stored procedures *can* be created on the endpoint over
Delta tables. Everything that writes table data cannot. If a request needs a
temporary table to stage results, do the staging in pandas instead.

Source: {_LEARN}/data-warehouse/tsql-surface-area

## Commands that are not supported

Microsoft's warning is explicit: "Don't try to use these commands. Even though
they might appear to succeed, they could cause issues to your warehouse."

`BULK LOAD` · `CREATE USER` · `FOR JSON` inside a subquery (it must be the last
operator) · manually created multi-column stats · materialized views ·
`PREDICT` · queries targeting system and user tables · **recursive queries** ·
schema and table names containing `/` or `\\` · `SELECT ... FOR XML` ·
`SET ROWCOUNT` · `SET TRANSACTION ISOLATION LEVEL` · `sp_showspaceused` ·
synonyms · triggers · vector data type and search functions

Two of these bite ordinary analytics work:

- **Recursive CTEs do not work.** Standard, sequential and nested CTEs do.
  Hierarchy walks (parent/child rollups, bill-of-materials) must be done with a
  bounded number of joins, or in pandas.
- **Isolation level cannot be set.** Fabric uses snapshot isolation only and
  ignores the statement.

`MERGE` is supported and generally available. `TRUNCATE TABLE` is supported in
Warehouse.

Source: {_LEARN}/data-warehouse/tsql-surface-area

## Data types that do not exist here

Not supported for stored columns: `money`, `smallmoney`, `datetime`,
`smalldatetime`, `datetimeoffset`, `nchar`, `nvarchar`, `text`, `ntext`,
`image`, `tinyint`, `geography`, `geometry`, `json`, `xml`, CLR user-defined
types, `vector`.

Practical effects:

- **There is no `nvarchar`.** `varchar` in a UTF-8 collation already carries
  unicode. Writing `N'...'` literals is pointless.
- Use `datetime2` (max 6 fractional-second digits), never `datetime`.
- `CAST(x AS decimal(n,m))` for money-like values; there is no monetary type.

These types may still be used for variables, parameters and in-memory
expressions — only persisted columns are restricted.

Source: {_LEARN}/data-warehouse/data-types

## How Delta types arrive on the endpoint

Endpoint tables are generated from the lakehouse Delta tables:
`STRING` and `VARCHAR(n>=2000)` become `varchar(8000)`; `TIMESTAMP` becomes
`datetime2`; `DATE` becomes `date`; `LONG`/`BIGINT` become `bigint`;
`BOOLEAN` becomes `bit`. A Delta type with no mapping is not represented as a
column at all — if an expected column is missing from the endpoint, that is
why.

Source: {_LEARN}/data-warehouse/data-types

## Do not judge speed on the first run

The first execution of a query pays a cold start (data loaded from OneLake,
statistics generated, nodes resumed). "Don't judge query performance based on
the first execution."

Also note that `TOP`, global sorting and final join steps force single-node
execution; on a large result this can emit "One or more non-scalable operation
is detected" and run slowly. Reduce the filtered set first.

Source: {_LEARN}/data-warehouse/guidelines-warehouse-performance
""",
    },
    {
        "slug": "sql-determinism",
        "title": "Ordering and labelling rows keyed by text dates",
        "description": (
            # 159 chars — see the cap note on the first skill.
            "Month, day and year stored as text sort alphabetically, not "
            "chronologically. Read before ordering, charting or naming a peak "
            "or latest period from a text date."
        ),
        "text": """\
**Source: measured on this deployment, 2026-07-25. This skill is not from
Microsoft documentation.**

## The defect this prevents

A run grouped a year of rows by a `Month` column typed `varchar`, with no
`ORDER BY`. The rows came back in lexical order — `1, 10, 11, 12, 2, 3, ...` —
so the last row was month `9`, not month `12`. The narration named the peak
after the row's *position* rather than its label, and reported December's name
beside October's number.

The totals were right; only the label was wrong. Nothing failed, nothing
retried, and the answer looked authoritative. That is the whole danger: this
class of mistake never raises an error.

## The rules

1. A `GROUP BY` on a text-typed month, day, quarter or year key returns rows in
   **lexical** order. `'10'` sorts before `'2'`.

2. Always order such a key numerically, and never rely on the engine's default
   order:

   ```sql
   SELECT Month, SUM([Amount]) AS total
   FROM fact_table
   WHERE Year = '2024'
   GROUP BY Month
   ORDER BY CAST(Month AS int);
   ```

   For a `'YYYYMMDD'` text key, `ORDER BY DayKey` is already correct because
   that format sorts lexically the same way it sorts chronologically. Zero-
   padded `'01'..'12'` months are likewise safe. Unpadded `'1'..'12'` are not.

3. **Never name a row by its position.** Do not call the last row "December" or
   the first row "January". Read the label from the row you are describing.

4. When reporting a maximum or minimum, take the label and the value from the
   **same row**:

   ```sql
   SELECT TOP 1 Month, SUM([Amount]) AS total
   FROM fact_table WHERE Year = '2024'
   GROUP BY Month ORDER BY SUM([Amount]) DESC;
   ```

   Do not sort by the period and then assume the largest number belongs to the
   last period.

5. This applies to every downstream artifact. A chart or dashboard fed an
   unordered frame renders the categories in whatever order it received them,
   and the narration written alongside it inherits the same mistake.

## Quick check before answering

If an answer names a period ("peak in December", "latest month", "first
quarter"), confirm that the query producing it had an explicit `ORDER BY` and
that the name came from the row's own label column.
""",
    },
]


def get_builtin_skills() -> List[Dict]:
    """Return the shipped skill definitions (copies, safe to mutate)."""
    return [dict(s) for s in BUILTIN_SKILLS]
