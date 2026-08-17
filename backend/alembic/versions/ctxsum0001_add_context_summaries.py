"""add lightweight context summaries for steps and tool executions

Revision ID: ctxsum0001
Revises: succ01
Create Date: 2026-08-11 00:00:00.000000

★RE-POINTED ON PORT. Upstream chains this off `usrdsdef01`, which this fork's
`ownxfer01` (the ownership-handover ledger) already claims as its parent.
Taking upstream's parent unchanged makes `usrdsdef01` a branch point and leaves
the tree with TWO heads — alembic then refuses to upgrade at all, and it fails
at deploy time rather than here. Re-pointed onto `succ01`, the fork's single
head at the time of the 537 port. Migration order between independent upstream
migrations is ours to choose; nothing in this migration depends on anything in
the ownership chain, and nothing there depends on this.

The migration intentionally does not rewrite historical large JSON payloads.
Existing null summaries retain the application's behavior-preserving legacy
projection fallback; new writes populate summaries automatically.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ctxsum0001"
down_revision: str | None = "succ01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "context_summary_json" not in _columns("steps"):
        op.add_column(
            "steps",
            sa.Column(
                "context_summary_json",
                sa.JSON(none_as_null=True),
                nullable=True,
            ),
        )
    if "context_summary_json" not in _columns("tool_executions"):
        op.add_column(
            "tool_executions",
            sa.Column(
                "context_summary_json",
                sa.JSON(none_as_null=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if "context_summary_json" in _columns("tool_executions"):
        op.drop_column("tool_executions", "context_summary_json")
    if "context_summary_json" in _columns("steps"):
        op.drop_column("steps", "context_summary_json")
