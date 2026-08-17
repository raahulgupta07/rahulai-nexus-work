"""A handover takes the organization's work and leaves the person's own.

`ownership_service` grew four more kinds in this release — notes, instructions,
prompts and agents — and each one carries a rule about what must NOT move. Those
rules are the whole point, and every one of them is satisfied by code that moves
too much:

  * ★★★**Private stays.** A private instruction and a private prompt are
    notes-to-self. ``user_id`` is the entirety of a private prompt's visibility
    (``prompt_service._is_visible``) and half of a private instruction's
    (``is_private, user_id`` is the pair in six places), so moving one both
    reveals it to the recipient and hides it from the person who wrote it —
    while they are still a member of the organization. A test that only asserts
    "the shared one moved" is fully satisfied by a transfer that moves all of
    them, which is why the private rows here are the load-bearing half.
  * ★★★**An agent's owner moves and nothing else does.** ``is_public``,
    ``publish_status`` and ``data_source_memberships`` are visibility.
    A transfer moves responsibility. Re-owning an agent *looks* like it should
    also hand over who can reach it, and quietly publishing a private agent is
    an access change nobody asked for and no screen would show.
  * ★★★**Files are deliberately not transferred.** ``File.user_id`` is an access
    grant, not authorship: ``app/core/file_access.py`` early-returns True on an
    owner match *ahead* of the full-admin check and the report-visibility
    predicate, and it gates the file's bytes, its extracted text and the
    **embed-token mint** in ``app/routes/file.py`` — a bearer credential that
    outlives the session that minted it. Moving that column hands the recipient
    read and embed rights over everything the departing person ever uploaded.
    Nothing is stranded by leaving it: a file is reached through the report or
    the data source that references it, and both of those move. The test below
    exists so a future reader does not "complete" the feature by adding files.
  * ★★★**A note is a child of its report**, exactly like an artifact
    (``notes.report_id`` is NOT NULL). It rides the report and only the report.
  * ★★★**``credential_bound_data_sources`` is read off the CONNECTION.**
    ``auth_policy`` lives on ``Connection``, never on ``DataSource`` — so
    ``getattr(data_source, "auth_policy", "system_only")`` returns the default
    on every row and the comparison is decided when you TYPE it. Two sites in
    this fork shipped with exactly that bug. The negative control here sets the
    policy on the Connection, so a version that read it off the DataSource
    counts zero and the positive assertion fails.
  * ★**Sub-counts are not kinds.** ``creator_identity_reports`` and
    ``credential_bound_data_sources`` count rows already counted under reports
    and data_sources. Adding either to ``total`` inflates every "owns N things"
    number an admin uses to judge a departure.
  * ★**``undo`` writes ``owner_user_id`` on an agent**, via ``_OWNER_ATTR``.
    Writing ``user_id`` there neither raises nor persists — SQLAlchemy accepts
    an unmapped attribute on a mapped object — so the batch would report itself
    fully reverted while every agent stayed with the person who left.

★These build real rows, so they need a schema and live in `tests/unit/`, NOT in
`tests/unit/fork/` — that directory's conftest overrides `run_migrations` with a
no-op and anything touching a table fails there with "no such table", which
reads as a product bug.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.dependencies import async_session_maker
from app.models.connection import Connection
from app.models.data_source import DataSource
from app.models.data_source_membership import DataSourceMembership
from app.models.domain_connection import domain_connection
from app.models.file import File
from app.models.instruction import Instruction
from app.models.membership import Membership
from app.models.note import Note
from app.models.organization import Organization
from app.models.ownership_transfer import OwnershipTransfer
from app.models.prompt import Prompt
from app.models.report import Report
from app.models.report_file_association import report_file_association
from app.models.user import User
from app.services import ownership_service as svc


def _uid() -> str:
    return str(uuid.uuid4())


async def _org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, org, *, is_active: bool = True) -> User:
    user = User(
        id=_uid(),
        name="Member",
        email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x",
        is_active=is_active,
        is_superuser=False,
        is_verified=True,
        is_service_account=False,
    )
    db.add(user)
    await db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="member"))
    await db.flush()
    return user


async def _report(db, org, owner, *, run_identity="viewer", status="draft") -> Report:
    report = Report(
        id=_uid(),
        title=f"r-{_uid()[:6]}",
        slug=f"slug-{_uid()[:8]}",
        status=status,
        user_id=owner.id,
        organization_id=org.id,
        shared_run_identity=run_identity,
    )
    db.add(report)
    await db.flush()
    return report


async def _note(db, org, report, owner) -> Note:
    note = Note(
        id=_uid(),
        report_id=report.id,
        user_id=owner.id,
        organization_id=org.id,
        title="plan",
        content="- [ ] find out why March is flat",
        source="agent",
    )
    db.add(note)
    await db.flush()
    return note


async def _instruction(db, org, owner, *, is_private: bool) -> Instruction:
    row = Instruction(
        id=_uid(),
        text="Revenue excludes intercompany transfers.",
        organization_id=org.id,
        user_id=owner.id,
        is_private=is_private,
        status="published",
        category="data_modeling",
    )
    db.add(row)
    await db.flush()
    return row


async def _prompt(db, org, owner, *, scope: str) -> Prompt:
    row = Prompt(
        id=_uid(),
        title=f"p-{_uid()[:6]}",
        text="Show me last quarter by region",
        organization_id=org.id,
        user_id=owner.id,
        scope=scope,
    )
    db.add(row)
    await db.flush()
    return row


async def _data_source(
    db,
    org,
    owner,
    *,
    is_public: bool = False,
    publish_status: str = "published",
) -> DataSource:
    ds = DataSource(
        id=_uid(),
        name=f"ds-{_uid()[:8]}",
        organization_id=org.id,
        owner_user_id=owner.id,
        is_public=is_public,
        publish_status=publish_status,
    )
    db.add(ds)
    await db.flush()
    return ds


async def _connection(db, org, ds, *, auth_policy: str) -> Connection:
    """A connection linked to ``ds``. ★``auth_policy`` lives HERE, not on the
    data source — see the module docstring."""
    conn = Connection(
        id=_uid(),
        name=f"conn-{_uid()[:8]}",
        type="postgres",
        config={"host": "localhost"},
        organization_id=org.id,
        auth_policy=auth_policy,
    )
    db.add(conn)
    await db.flush()
    await db.execute(
        domain_connection.insert().values(data_source_id=ds.id, connection_id=conn.id)
    )
    await db.flush()
    return conn


# ─────────────────────────────── notes ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_note_moves_with_the_report_it_explains():
    """★``notes.report_id`` is NOT NULL — a note is a child, like an artifact.

    Left behind, every plan, finding and ruled-out hypothesis stays attributed
    to a departed account while the report it explains has a new owner.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        note = await _note(db, org, report, alice)

        summary = await svc.summarize(db, org, alice.id)
        assert summary.notes == 1

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )

        await db.refresh(note)
        assert str(note.user_id) == str(bob.id), (
            "the report changed hands and its working notes did not"
        )
        assert result.moved["note"] == 1


@pytest.mark.asyncio
async def test_a_note_on_an_archived_report_stays_put():
    """★Negative control for the rule above: a note rides its report and ONLY
    its report. Delete on this product is ``status='archived'``, so the archived
    report is not selected — and neither is anything hanging off it.

    Would fail if notes were ever selected org-wide by ``user_id`` instead of
    off the moved report ids.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        live = await _report(db, org, alice, status="draft")
        binned = await _report(db, org, alice, status="archived")
        live_note = await _note(db, org, live, alice)
        binned_note = await _note(db, org, binned, alice)

        summary = await svc.summarize(db, org, alice.id)
        assert summary.notes == 1, "a note on an archived report was counted"

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )
        assert result.moved["note"] == 1

        await db.refresh(live_note)
        await db.refresh(binned_note)
        assert str(live_note.user_id) == str(bob.id)
        assert str(binned_note.user_id) == str(alice.id)


# ───────────────────── shared moves, private stays ────────────────────────


@pytest.mark.asyncio
async def test_a_shared_instruction_outlives_the_person_who_wrote_it():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        shared = await _instruction(db, org, alice, is_private=False)

        summary = await svc.summarize(db, org, alice.id)
        assert summary.instructions == 1

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )

        await db.refresh(shared)
        assert str(shared.user_id) == str(bob.id), (
            "a published rule the organization depends on stayed with the "
            "person who left"
        )
        assert result.moved["instruction"] == 1


@pytest.mark.asyncio
async def test_a_private_instruction_is_not_somebody_elses_to_inherit():
    """★★★The negative control. Without it, code that moves EVERY instruction
    passes the test above.

    ``(is_private, user_id)`` is the visibility pair. Move a private row and it
    starts appearing in the RECIPIENT'S AI context — somebody else's private
    note, silently steering answers, with no screen that would show it.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        shared = await _instruction(db, org, alice, is_private=False)
        private = await _instruction(db, org, alice, is_private=True)

        summary = await svc.summarize(db, org, alice.id)
        assert summary.instructions == 1, (
            "the confirmation screen counted a private instruction, so it "
            "promises to move something the transfer must not move"
        )

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )

        await db.refresh(shared)
        await db.refresh(private)
        assert str(shared.user_id) == str(bob.id)
        assert str(private.user_id) == str(alice.id), (
            "a private instruction changed hands: it is now in the recipient's "
            "AI context and gone from its author's, and they still work here"
        )
        assert result.moved["instruction"] == 1, (
            "the count says two rows moved — the screen and the write disagree"
        )


# ★No test for a NULL ``is_private`` row, deliberately. The service uses
# ``.isnot(True)`` rather than ``.is_(False)`` because the column's own comment
# reads "False/NULL = SHARED" — but the column is ``nullable=False``, so such a
# row cannot be built here without the flush failing on the constraint. The
# choice is defensive against a future migration relaxing the column, and a test
# that cannot construct the state it describes would be a comment with a test's
# salary.


@pytest.mark.asyncio
async def test_an_organizational_prompt_moves_and_a_private_one_does_not():
    """★★★``prompt_service._is_visible`` returns True on a ``user_id`` match at
    ANY scope, so ``user_id`` is the whole of a private prompt's visibility.
    Moving one is doubly wrong in a single write: it appears for the recipient
    AND disappears for its author.

    'agent' and 'global' are both organizational; only 'private' is the
    note-to-self. Both are asserted, so a fix that keyed on 'global' alone —
    or on 'agent' alone — fails.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        agent_scoped = await _prompt(db, org, alice, scope="agent")
        global_scoped = await _prompt(db, org, alice, scope="global")
        private = await _prompt(db, org, alice, scope="private")

        summary = await svc.summarize(db, org, alice.id)
        assert summary.prompts == 2, (
            "the confirmation screen counted a private prompt among the rows a "
            "transfer will move"
        )

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )

        for row in (agent_scoped, global_scoped, private):
            await db.refresh(row)
        assert str(agent_scoped.user_id) == str(bob.id)
        assert str(global_scoped.user_id) == str(bob.id)
        assert str(private.user_id) == str(alice.id), (
            "somebody's personal draft changed hands — it is visible to the "
            "recipient and invisible to the person who wrote it"
        )
        assert result.moved["prompt"] == 2


# ─────────────────────── an agent: owner and nothing else ─────────────────


@pytest.mark.asyncio
async def test_re_owning_an_agent_changes_nothing_about_who_can_reach_it():
    """★★★A transfer moves responsibility, never visibility.

    ``owner_user_id`` is in no visibility predicate — access to an agent is
    ``is_public`` OR a ``data_source_memberships`` row. This is the easiest
    place in the service to break that rule, because re-owning an agent *looks*
    like it should also hand over who can reach it. Quietly publishing a private
    agent, or adding the recipient to its membership list, is an access change
    nobody asked for and no screen would show.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        ds = await _data_source(
            db, org, alice, is_public=False, publish_status="draft"
        )
        db.add(
            DataSourceMembership(
                id=_uid(),
                data_source_id=ds.id,
                principal_type="user",
                principal_id=alice.id,
            )
        )
        await db.flush()

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )

        await db.refresh(ds)
        assert str(ds.owner_user_id) == str(bob.id)
        assert result.moved["data_source"] == 1

        assert ds.is_public is False, (
            "a private agent was published by a transfer — everyone in the "
            "organization can now reach it and nobody chose that"
        )
        assert ds.publish_status == "draft", (
            "a draft agent was pushed live by a change of owner"
        )

        principals = sorted(
            str(p)
            for p in (
                await db.execute(
                    select(DataSourceMembership.principal_id).where(
                        DataSourceMembership.data_source_id == str(ds.id),
                        DataSourceMembership.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert principals == [str(alice.id)], (
            "the membership list changed: a transfer granted or revoked access "
            f"to an agent, which it must never do — {principals}"
        )


@pytest.mark.asyncio
async def test_credential_bound_agents_are_counted_off_the_connection():
    """★★★``auth_policy`` is on Connection and NEVER on DataSource.

    ``getattr(data_source, "auth_policy", "system_only")`` returns the default
    on every row, so that comparison is decided when you type it. Two sites in
    this fork shipped exactly that, with opposite symptoms and no error from
    either. Both halves are asserted here: a ``user_required`` connection IS
    counted (which a DataSource-side read can never satisfy) and a
    ``system_only`` one is NOT (which a read that counted every owned agent can
    never satisfy).

    The count is a warning, and the thing being warned about is real: on a
    ``user_required`` connection the new owner either cannot run the agent at
    all (fabric_user / powerbi_user) or silently gains the use of system
    credentials they were never granted.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)

        bound = await _data_source(db, org, alice)
        await _connection(db, org, bound, auth_policy="user_required")

        unbound = await _data_source(db, org, alice)
        await _connection(db, org, unbound, auth_policy="system_only")

        # No connection at all — nothing to read a policy off, so nothing to
        # warn about.
        await _data_source(db, org, alice)

        summary = await svc.summarize(db, org, alice.id)
        assert summary.data_sources == 3
        assert summary.credential_bound_data_sources == 1, (
            "the warning count is not being read off the Connection: a "
            "system_only agent was counted, or a user_required one was missed"
        )

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )
        assert result.credential_bound_data_sources == 1, (
            "★counted AFTER the move, when every row already belongs to the "
            "recipient — the dialog would report a clean transfer of exactly "
            "the agents it most needed to warn about"
        )


@pytest.mark.asyncio
async def test_an_agent_on_two_bound_connections_is_warned_about_once():
    """★The relationship is M:N — without ``.distinct()`` the warning overstates
    itself, and an inflated number is how people learn to ignore the number."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        ds = await _data_source(db, org, alice)
        await _connection(db, org, ds, auth_policy="user_required")
        await _connection(db, org, ds, auth_policy="user_required")

        summary = await svc.summarize(db, org, alice.id)
        assert summary.data_sources == 1
        assert summary.credential_bound_data_sources == 1


# ──────────────────────────── files stay put ──────────────────────────────


@pytest.mark.asyncio
async def test_a_departing_persons_files_are_not_handed_over():
    """★★★A decision, not an omission — do not "complete" this by adding files.

    ``File.user_id`` is not authorship on this product, it is an access grant.
    ``app/core/file_access.py:68`` early-returns True on an owner match, *ahead*
    of the full-admin check and the report-visibility predicate, and that
    function gates three routes in ``app/routes/file.py``: the content bytes,
    the extracted text, and the **embed-token mint** — a bearer credential that
    outlives the session that minted it. Moving the column would hand the
    recipient read and embed rights over everything the departing person ever
    uploaded, which is the one thing this service must never do.

    Nothing is stranded by leaving it: a file is reached through the report or
    the data source that references it, and both of those transfer.
    ``file_service.get_files`` is org-scoped, so there is no "my files" list to
    go empty, and no delete gate is keyed on ``user_id``.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        upload = File(
            id=_uid(),
            filename="salaries.xlsx",
            path="/app/backend/uploads/files/salaries.xlsx",
            content_type="application/vnd.ms-excel",
            user_id=alice.id,
            organization_id=org.id,
        )
        db.add(upload)
        await db.flush()
        # ★Written straight into the association table rather than through
        # ``report.files.append`` — the relationship is lazy and touching it on
        # a freshly flushed object can emit a load in async context, which fails
        # for a reason that has nothing to do with what is under test.
        await db.execute(
            report_file_association.insert().values(
                report_id=report.id, file_id=upload.id
            )
        )
        await db.flush()

        await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )

        await db.refresh(report)
        await db.refresh(upload)
        assert str(report.user_id) == str(bob.id), (
            "positive control — the report this file hangs off did move"
        )
        assert str(upload.user_id) == str(alice.id), (
            "a transfer moved File.user_id. That column is an access grant "
            "read ahead of every other check in file_access, and it gates the "
            "embed-token mint, so the recipient now holds read and embed "
            "rights over everything the departing person ever uploaded"
        )

        rows = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(upload.id)
                )
            )
        ).scalars().all()
        assert rows == [], "the ledger claims a file changed hands"


# ────────────────────────── include_assets=False ──────────────────────────


@pytest.mark.asyncio
async def test_a_per_project_handover_does_not_move_the_organizations_assets():
    """★The three org-level kinds are behind ``include_assets`` for a reason.

    They have no ``report_id``. A handover of one project that dragged them
    along would rewrite the organization's whole glossary because one dashboard
    changed hands.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        instruction = await _instruction(db, org, alice, is_private=False)
        prompt = await _prompt(db, org, alice, scope="global")
        ds = await _data_source(db, org, alice)

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
            include_assets=False,
        )

        await db.refresh(report)
        assert str(report.user_id) == str(bob.id), (
            "positive control — the report graph still moves"
        )

        for row, column in (
            (instruction, "user_id"),
            (prompt, "user_id"),
            (ds, "owner_user_id"),
        ):
            await db.refresh(row)
            assert str(getattr(row, column)) == str(alice.id), (
                f"{type(row).__name__} moved despite include_assets=False"
            )

        for kind in ("instruction", "prompt", "data_source"):
            assert result.moved.get(kind, 0) == 0
        assert result.credential_bound_data_sources == 0, (
            "a warning was raised about agents this transfer did not touch"
        )


# ─────────────────────────────── undo ─────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_puts_every_new_kind_back():
    """★★★Including the agent, whose owner column is ``owner_user_id``.

    ``_OWNER_ATTR`` exists because SQLAlchemy lets you assign an unmapped
    attribute to a mapped object without complaining: ``ds.user_id = x``
    succeeds, persists nothing, and the batch reports itself fully reverted
    while every agent stays with the person who left. The stray-attribute
    assertion below is what makes that failure visible instead of silent.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        note = await _note(db, org, report, alice)
        instruction = await _instruction(db, org, alice, is_private=False)
        prompt = await _prompt(db, org, alice, scope="global")
        ds = await _data_source(db, org, alice)

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="offboarding",
        )
        await db.refresh(ds)
        assert str(ds.owner_user_id) == str(bob.id)

        await svc.undo(db, org, result.batch_id, actor_user_id=alice.id)

        for row, column in (
            (note, "user_id"),
            (instruction, "user_id"),
            (prompt, "user_id"),
            (ds, "owner_user_id"),
        ):
            await db.refresh(row)
            assert str(getattr(row, column)) == str(alice.id), (
                f"undo reported success and left {type(row).__name__} with the "
                "person it was taken from"
            )

        assert "user_id" not in DataSource.__table__.columns, (
            "DataSource grew a user_id column; _OWNER_ATTR and this test both "
            "need re-reading before anything else is changed"
        )
        assert "user_id" not in ds.__dict__, (
            "undo wrote `user_id` on an agent — an unmapped attribute that "
            "neither raises nor persists. The row would look reverted in the "
            "ledger and would still belong to the recipient in the database"
        )


# ─────────────────────── the counts an admin reads ────────────────────────


@pytest.mark.asyncio
async def test_the_two_warning_counts_are_not_added_to_the_total():
    """★Each is a sub-count OF a kind already counted, not a kind of its own.

    ``creator_identity_reports`` is a subset of ``reports``;
    ``credential_bound_data_sources`` is a subset of ``data_sources``. Adding
    either to ``total`` double-counts those rows in the one number an admin uses
    to judge how much a departure strands — and an inflated number is how people
    learn to stop trusting the number.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        await _report(db, org, alice, run_identity="creator")
        ds = await _data_source(db, org, alice)
        await _connection(db, org, ds, auth_policy="user_required")

        summary = await svc.summarize(db, org, alice.id)
        assert summary.creator_identity_reports == 1
        assert summary.credential_bound_data_sources == 1

        assert summary.total == 2, (
            "total counted a warning as a kind: one report and one agent is "
            f"two things, not {summary.total}"
        )
        assert summary.as_dict()["total"] == 2
