"""The knowledge-harness exclusion must not drop ordinary blocks.

`_send_completion_blocks` filters out blocks belonging to a knowledge-harness
plan decision. The original predicate was:

    or_(CompletionBlock.plan_decision_id == None,
        PlanDecision.phase != 'knowledge_harness')

`phase` is NULL for every regular main-loop decision, and in SQL
`NULL != 'knowledge_harness'` evaluates to NULL — not true — so a block whose
decision had a NULL phase satisfied neither arm and was silently dropped. The
fix adds an explicit `PlanDecision.phase == None` arm.

These tests run the real predicate against a real database, because the bug is
a SQL three-valued-logic bug: it cannot be reproduced in Python, where
`None != 'x'` is plainly True.
"""

import uuid

import pytest
from sqlalchemy import Column, String, or_, select
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Block(Base):
    __tablename__ = "t_blocks"
    id = Column(String, primary_key=True)
    plan_decision_id = Column(String, nullable=True)


class Decision(Base):
    __tablename__ = "t_decisions"
    id = Column(String, primary_key=True)
    phase = Column(String, nullable=True)


def _stmt(null_safe: bool):
    """The production predicate, with and without the NULL arm."""
    arms = [Block.plan_decision_id == None]  # noqa: E711
    if null_safe:
        arms.append(Decision.phase == None)  # noqa: E711
    arms.append(Decision.phase != "knowledge_harness")
    return (
        select(Block.id)
        .outerjoin(Decision, Block.plan_decision_id == Decision.id)
        .where(or_(*arms))
    )


@pytest.fixture()
def session(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path}/{uuid.uuid4().hex}.db")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([
            Decision(id="d_null", phase=None),            # a regular decision
            Decision(id="d_main", phase="main"),
            Decision(id="d_kh", phase="knowledge_harness"),
            Block(id="b_orphan", plan_decision_id=None),  # no decision at all
            Block(id="b_null", plan_decision_id="d_null"),
            Block(id="b_main", plan_decision_id="d_main"),
            Block(id="b_kh", plan_decision_id="d_kh"),
        ])
        s.commit()
        yield s


def test_null_phase_block_survives_the_filter(session):
    kept = set(session.execute(_stmt(null_safe=True)).scalars().all())
    assert kept == {"b_orphan", "b_null", "b_main"}
    assert "b_kh" not in kept, "knowledge-harness block must still be excluded"


def test_the_old_predicate_dropped_it(session):
    """Pins the bug, so a 'simplification' cannot quietly reintroduce it."""
    kept = set(session.execute(_stmt(null_safe=False)).scalars().all())
    assert "b_null" not in kept, (
        "expected the pre-fix predicate to drop NULL-phase blocks; if this "
        "fails the three-valued-logic bug no longer reproduces and this whole "
        "test file needs rethinking"
    )
    assert kept == {"b_orphan", "b_main"}
