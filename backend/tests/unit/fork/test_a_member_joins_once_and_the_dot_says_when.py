"""DEF-019/020/021 — Phase 5: joining twice, and a check that never happened.

DEF-019  `_is_email_already_in_organization` — the helper that exists to STOP a
         duplicate membership — asked its existence question with
         `scalar_one_or_none()`, which does not mean "is there one?" but "there
         must be at most one, or raise". It also had no `deleted_at` filter,
         while every membership CHECK in the product has one. Two consequences,
         both of them the guard failing at its own job:

           · a live row beside a soft-deleted one (LDAP's cleanup soft-deletes;
             a later sign-in creates a fresh live row) returns two rows, and the
             invite screen answers 500;
           · a person LDAP dropped out of a group is gone from the members list
             and still "Already a member with this email" when an admin adds
             them back. Removed everywhere, un-addable here.

         ★This exact landmine was found, documented and fixed in `auth.py` (see
         the `.first()` note on the domain-signup check) and left standing here.

DEF-020  `auto_provision_user_for_org` asked "is there an open INVITE?" and read
         the absence of one as "not in this org". Somebody who is ALREADY a
         member has no open invite either — theirs was consumed when they
         joined — so every chat message from an existing member on a
         domain-admitted address minted another membership row. That is the
         one-per-arrival growth in the table, and why the count only went up.

         ★And the seat cap was asked BEFORE anyone checked. Once an org reached
         its licensed count, an existing member's message was answered "ask your
         admin" — the cap locking out the very people it had already counted.

DEF-021  The detail modal renders "Last checked" from
         `connection.last_checked_at`, a field that existed on NO schema, then
         falls back to `user_status.last_checked_at`, which is null for a
         system connection and was ABSENT ENTIRELY from the detail payload. So
         the line said `Never` under a green dot, on every connection the
         product has ever shown. The detail route even COMPUTED the user status
         (it needs it for the table count) and dropped it, because the schema
         had nowhere to put it — the list/detail disagreement of 12.1 again.

★8.1's schema half already shipped: `memuniq01` collapsed the duplicates and
added `uq_membership_user_org`. What remained was the WRITE paths, which the
constraint converts from a silent duplicate into an IntegrityError — a changed
symptom, not a closed cause. See `TestWhatAlreadyShipped`.
"""
import pathlib
import re
import shutil
import tempfile
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main  # noqa: F401  — boots the app's ORM registry
from app.models.base import Base
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User


BACKEND = pathlib.Path(__file__).resolve().parents[3]
REPO = BACKEND.parent
APP = BACKEND / "app"
VUE = REPO / "frontend" / "components" / "ConnectionDetailModal.vue"


@pytest.fixture(scope="module")
def schema_template():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="join-once-"))
    template = tmp / "template.db"
    engine = create_engine(f"sqlite:///{template}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield template
    shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def db(schema_template):
    copy = schema_template.parent / f"{uuid.uuid4().hex}.db"
    shutil.copyfile(schema_template, copy)
    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        copy.unlink(missing_ok=True)


def _user(local: str) -> User:
    return User(
        id=str(uuid.uuid4()),
        name=local,
        email=f"{local}@example.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )


def _org(name: str) -> Organization:
    return Organization(id=str(uuid.uuid4()), name=name)


def _membership(org, *, user=None, email=None, role="member", deleted_at=None):
    return Membership(
        id=str(uuid.uuid4()),
        user_id=user.id if user is not None else None,
        organization_id=org.id,
        email=email,
        role=role,
        deleted_at=deleted_at,
    )


async def _live_rows(db, user, org) -> int:
    return (await db.execute(
        select(func.count()).select_from(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org.id,
            Membership.deleted_at.is_(None),
        )
    )).scalar() or 0


# ---------------------------------------------------------------------------
# DEF-019 — the guard must survive the thing it guards against
# ---------------------------------------------------------------------------

class TestTheDuplicateGuardSurvivesADuplicate:
    @pytest.mark.asyncio
    async def test_a_soft_deleted_row_beside_a_live_one_does_not_raise(self, db):
        """★The 500. `memuniq01` forbids a second LIVE row; it does not forbid a
        soft-deleted one sitting beside it, and that pair is ordinary — LDAP's
        cleanup soft-deletes, a later sign-in creates a fresh live row."""
        from app.services.organization_service import OrganizationService

        u, o = _user("kaung"), _org("Insights")
        db.add_all([u, o,
                    _membership(o, user=u, deleted_at=None),
                    _membership(o, user=u, deleted_at=__import__("datetime").datetime.utcnow())])
        await db.commit()

        got = await OrganizationService()._is_email_already_in_organization(
            db, u.email, o.id
        )
        assert got, "a member with an old soft-deleted row must still read as a member"

    @pytest.mark.asyncio
    async def test_a_removed_member_can_be_added_back(self, db):
        """Only a soft-deleted row: they are gone from the members list, so the
        guard must let an admin put them back."""
        from app.services.organization_service import OrganizationService

        u, o = _user("nyilin"), _org("Insights")
        db.add_all([u, o, _membership(
            o, user=u, deleted_at=__import__("datetime").datetime.utcnow()
        )])
        await db.commit()

        got = await OrganizationService()._is_email_already_in_organization(
            db, u.email, o.id
        )
        assert not got, "a removed member is not already a member"

    @pytest.mark.asyncio
    async def test_a_current_member_is_still_refused(self, db):
        """★Positive control. A filter that let everybody through would pass the
        test above and re-open the duplicate this guard exists to stop."""
        from app.services.organization_service import OrganizationService

        u, o = _user("rahul"), _org("Insights")
        db.add_all([u, o, _membership(o, user=u)])
        await db.commit()

        assert await OrganizationService()._is_email_already_in_organization(
            db, u.email, o.id
        )

    @pytest.mark.asyncio
    async def test_a_stranger_is_not_a_member(self, db):
        from app.services.organization_service import OrganizationService

        o = _org("Insights")
        db.add(o)
        await db.commit()

        assert not await OrganizationService()._is_email_already_in_organization(
            db, "nobody@example.test", o.id
        )

    @pytest.mark.asyncio
    async def test_a_member_of_another_org_is_not_a_member_here(self, db):
        """★Positive control on scope: the question is per-organization."""
        from app.services.organization_service import OrganizationService

        u, here, there = _user("visitor"), _org("Insights"), _org("Other")
        db.add_all([u, here, there, _membership(there, user=u)])
        await db.commit()

        # ★`not`, not `is False`. The old helper returned None here and the new
        # one returns False; both are "no". Pinning the falsy VALUE would have
        # made this control red on HEAD for a cosmetic reason, and a control
        # that fails on HEAD is not a control.
        assert not await OrganizationService()._is_email_already_in_organization(
            db, u.email, here.id
        )

    @pytest.mark.asyncio
    async def test_a_pending_invite_still_counts(self, db):
        """The email-keyed branch: an invite that nobody has accepted occupies
        the address, so a second invite must still be refused."""
        from app.services.organization_service import OrganizationService

        o = _org("Insights")
        db.add_all([o, _membership(o, email="pending@example.test")])
        await db.commit()

        assert await OrganizationService()._is_email_already_in_organization(
            db, "pending@example.test", o.id
        )

    @pytest.mark.asyncio
    async def test_a_withdrawn_invite_does_not(self, db):
        from app.services.organization_service import OrganizationService

        o = _org("Insights")
        db.add_all([o, _membership(
            o, email="pending@example.test",
            deleted_at=__import__("datetime").datetime.utcnow(),
        )])
        await db.commit()

        assert not await OrganizationService()._is_email_already_in_organization(
            db, "pending@example.test", o.id
        )


# ---------------------------------------------------------------------------
# DEF-020 — one arrival, one membership
# ---------------------------------------------------------------------------

@pytest.fixture
def admitting_domain(monkeypatch):
    """Domain signup is an enterprise feature; `_org_signup_policy` returns {}
    without a license. Replace the policy lookup, not the license check — the
    behaviour under test is what happens AFTER a domain is admitted."""
    import app.core.auth as auth_mod

    async def _policy(db, organization_id):
        return {"enabled": True, "allowed_domains": ["example.test"],
                "auto_invite_role": "member"}

    monkeypatch.setattr(auth_mod, "_org_signup_policy", _policy)
    return _policy


class TestAMemberJoinsOnce:
    @pytest.mark.asyncio
    async def test_an_existing_member_does_not_gain_a_second_row(self, db, admitting_domain):
        """★The defect, in one call. Before `memuniq01` this left two rows;
        after it, the same call raises IntegrityError instead. Either way the
        member arrived once and the product recorded it twice."""
        from app.core.auth import auto_provision_user_for_org

        u, o = _user("rahul"), _org("Insights")
        db.add_all([u, o, _membership(o, user=u)])
        await db.commit()

        got = await auto_provision_user_for_org(db, o.id, u.email)

        assert got is not None and str(got.id) == str(u.id)
        assert await _live_rows(db, u, o) == 1

    @pytest.mark.asyncio
    async def test_arriving_five_times_still_leaves_one_row(self, db, admitting_domain):
        """★The measured shape: six rows for one person, one per sign-in."""
        from app.core.auth import auto_provision_user_for_org

        u, o = _user("kaung"), _org("Insights")
        db.add_all([u, o, _membership(o, user=u)])
        await db.commit()

        for _ in range(5):
            await auto_provision_user_for_org(db, o.id, u.email)

        assert await _live_rows(db, u, o) == 1

    @pytest.mark.asyncio
    async def test_a_full_org_still_admits_its_own_member(self, db, admitting_domain, monkeypatch):
        """★The second half. The seat cap ran before anyone checked whether the
        sender already held a seat, so a licensed-out org started refusing the
        people it had already counted."""
        import app.core.seats as seats

        async def _no_room(db_, org_id):
            return False

        monkeypatch.setattr(seats, "has_seat_for", _no_room)

        from app.core.auth import auto_provision_user_for_org

        u, o = _user("rahul"), _org("Insights")
        db.add_all([u, o, _membership(o, user=u)])
        await db.commit()

        got = await auto_provision_user_for_org(db, o.id, u.email)
        assert got is not None, "a member who already holds a seat is not asking for one"

    @pytest.mark.asyncio
    async def test_a_full_org_still_refuses_a_newcomer(self, db, admitting_domain, monkeypatch):
        """★Positive control. Moving the membership check above the seat gate
        must not move the gate itself — a stranger still needs a free seat."""
        import app.core.seats as seats

        async def _no_room(db_, org_id):
            return False

        monkeypatch.setattr(seats, "has_seat_for", _no_room)

        from app.core.auth import auto_provision_user_for_org

        o = _org("Insights")
        db.add(o)
        await db.commit()

        assert await auto_provision_user_for_org(db, o.id, "newcomer@example.test") is None

    @pytest.mark.asyncio
    async def test_a_genuine_newcomer_still_gets_a_membership(self, db, admitting_domain):
        """★Positive control. A guard that returned early for everyone would
        pass every test above and quietly stop admitting anybody."""
        from app.core.auth import auto_provision_user_for_org

        o = _org("Insights")
        db.add(o)
        await db.commit()

        got = await auto_provision_user_for_org(db, o.id, "newcomer@example.test")
        assert got is not None
        assert await _live_rows(db, got, o) == 1

    @pytest.mark.asyncio
    async def test_an_existing_account_with_no_membership_is_placed(self, db, admitting_domain):
        """★Positive control. The early return must key on the MEMBERSHIP, not
        on the account existing."""
        from app.core.auth import auto_provision_user_for_org

        u, o = _user("stranger"), _org("Insights")
        db.add_all([u, o])
        await db.commit()

        got = await auto_provision_user_for_org(db, o.id, u.email)
        assert got is not None and str(got.id) == str(u.id)
        assert await _live_rows(db, u, o) == 1

    @pytest.mark.asyncio
    async def test_an_unadmitted_domain_is_still_refused(self, db, admitting_domain):
        from app.core.auth import auto_provision_user_for_org

        o = _org("Insights")
        db.add(o)
        await db.commit()

        assert await auto_provision_user_for_org(db, o.id, "someone@elsewhere.test") is None

    @pytest.mark.asyncio
    async def test_an_open_invite_is_still_attached_not_duplicated(self, db, admitting_domain):
        """★Positive control on the other branch: an invite is a seat already
        taken, so it must be claimed rather than joined by a second row."""
        from app.core.auth import auto_provision_user_for_org

        u, o = _user("invited"), _org("Insights")
        db.add_all([u, o, _membership(o, email=u.email)])
        await db.commit()

        got = await auto_provision_user_for_org(db, o.id, u.email)
        assert got is not None
        assert await _live_rows(db, u, o) == 1


# ---------------------------------------------------------------------------
# DEF-021 — the dot says when
# ---------------------------------------------------------------------------

class TestTheTimestampReachesTheScreen:
    def test_both_payloads_carry_it(self):
        from app.schemas.connection_schema import ConnectionDetailSchema, ConnectionSchema

        assert "last_checked_at" in ConnectionSchema.model_fields
        assert "last_checked_at" in ConnectionDetailSchema.model_fields

    def test_the_detail_payload_carries_the_user_status_too(self):
        """★The list/detail disagreement of 12.1, in a second field. The route
        already computed this and had nowhere to put it."""
        from app.schemas.connection_schema import ConnectionDetailSchema

        assert "user_status" in ConnectionDetailSchema.model_fields

    def test_every_connection_payload_is_populated(self):
        """Four constructors build these schemas. A field added to three of
        them is a field that is `Never` on the fourth screen."""
        src = (APP / "routes" / "connection.py").read_text(encoding="utf-8")
        built = src.count("ConnectionSchema(") + src.count("ConnectionDetailSchema(")
        assert built == 4, f"constructor count changed ({built}) — check the new one too"
        assert src.count("last_checked_at=") == built

    def test_it_comes_from_the_column_the_check_writes(self):
        src = (APP / "routes" / "connection.py").read_text(encoding="utf-8")
        assert "last_connection_checked_at.isoformat()" in src

    def test_the_detail_route_passes_what_it_computed(self):
        src = (APP / "routes" / "connection.py").read_text(encoding="utf-8")
        assert "user_status=_user_status," in src

    def test_the_screen_reads_the_fresh_status_not_the_stale_prop(self):
        """★After a reconnect the fresh status lands in `statusOverride`, and
        `userStatus` is the computed that knows about it. Reading
        `props.connection.user_status` showed the value from page load — the one
        moment this line matters most."""
        src = VUE.read_text(encoding="utf-8")
        block = src[src.index("const lastCheckedDisplay"):]
        block = block[:block.index("})")]
        assert "userStatus.value?.last_checked_at" in block
        assert "props.connection?.user_status" not in block

    def test_the_connection_level_fallback_is_kept(self):
        """★Positive control. `userStatus` is null for a system connection, so
        dropping the connection-level source would trade one `Never` for
        another."""
        src = VUE.read_text(encoding="utf-8")
        block = src[src.index("const lastCheckedDisplay"):]
        block = block[:block.index("})")]
        assert "props.connection?.last_checked_at" in block


class TestALiveTestIsAlsoACheck:
    def test_the_live_branch_stamps_the_time(self):
        """A live test that reports `Never` a moment later is this defect in its
        purest form. Three branches run one, and all three left it None."""
        src = (APP / "services" / "user_data_source_credentials_service.py").read_text(encoding="utf-8")
        body = src[src.index("async def build_user_status("):
                   src.index("async def build_user_status_for_connection(")]
        assert body.count("last_checked = datetime.utcnow()") == 3

    def test_nothing_calls_it_yet(self):
        """★Pinned as DEAD, the same way `mark_running()` was in Phase 3. Every
        caller in the tree passes live_test=False, so the branch above is
        corrected rather than proven. If this test fails somebody wired a live
        test up — good, and the stamp above is now load-bearing rather than
        precautionary."""
        callers = []
        for path in APP.rglob("*.py"):
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "live_test=True" in line:
                    callers.append(f"{path.name}: {line.strip()}")
        assert callers == [], f"live_test=True now has callers: {callers}"


class TestWhatAlreadyShipped:
    """★Recorded so 8.1 is not re-diagnosed from the roadmap text alone.

    The duplicate rows measured on dev are real; the SCHEMA fix for them landed
    before this phase and is applied on local. What was still open was the code
    that creates them.
    """

    def test_the_constraint_exists(self):
        mig = BACKEND / "alembic" / "versions" / "memuniq01_one_membership_per_person_per_org.py"
        assert mig.exists()
        src = mig.read_text(encoding="utf-8")
        assert "uq_membership_user_org" in src

    def test_it_marks_rather_than_deletes(self):
        """★The property that makes it safe to run on a live workspace: the
        duplicates are stamped with `deleted_at`, and the unique index is
        partial, so marking is exactly as effective as deleting while every
        column survives."""
        src = (BACKEND / "alembic" / "versions"
               / "memuniq01_one_membership_per_person_per_org.py").read_text(encoding="utf-8")
        assert "deleted_at IS NULL" in src
        assert re.search(r"NOTHING IS DELETED", src)
