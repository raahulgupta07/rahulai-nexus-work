"""The bundle you can keep — 0.0.531.7.

Every transfer path so far moves ownership *inside* this install. This is the
layer underneath: a file that survives the install being gone, rolled back, or
left behind. It is the only part of the feature that still works when the
product does not.

Four properties, and the two that matter most are about what the file must NOT
contain:

  * ★★★**No credentials, and nothing reachable from a connection.** Every field
    is named explicitly rather than dumped, so the day somebody adds a decrypted
    column to a model it does not silently appear in a file users email to each
    other.
  * ★★★**A title cannot decide where a file lands.** Report titles are
    user-supplied and become paths inside the zip; `../../etc/passwd` must not
    escape when somebody unzips this on their laptop.
  * ★**Only what they OWNED**, never what they could see. A report shared with
    somebody belongs to its owner and stays there — otherwise an export is a way
    to take a copy of the whole organization.
  * ★**Truncation is named, never silent.** A bundle that quietly stops
    including things is worse than one that says it stopped, because it is
    indistinguishable from a complete backup at the moment you need it.

★These need a schema, so they live here and NOT in `tests/unit/fork`.

★★★**Red proof by mutation of the shipped file** — each took down exactly one
test and left the other 12 green:

  * `_safe` returning the raw title → only `test_a_hostile_title_cannot_escape_the_folder`
  * selecting reports without `Report.user_id == uid` → only
    `test_a_shared_report_belongs_to_its_owner_and_stays_there`
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

from app.dependencies import async_session_maker
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.step import Step
from app.models.user import User
from app.models.widget import Widget
from app.services.ownership_export import DEFAULT_MAX_RESULT_BYTES, build_bundle


def _uid() -> str:
    return str(uuid.uuid4())


async def _org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, org) -> User:
    user = User(
        id=_uid(), name="Member", email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x", is_active=True, is_superuser=False,
        is_verified=True, is_service_account=False,
    )
    db.add(user)
    await db.flush()
    db.add(Membership(id=_uid(), user_id=user.id, organization_id=org.id, role="member"))
    await db.flush()
    return user


async def _report(db, org, owner, *, title="Quarterly revenue") -> Report:
    report = Report(
        id=_uid(), title=title, slug=f"s-{_uid()[:8]}", status="draft",
        user_id=owner.id, organization_id=org.id, shared_run_identity="creator",
    )
    db.add(report)
    await db.flush()
    return report


async def _step(db, report, *, code="print(1)", data=None, title="Step one") -> Step:
    widget = Widget(
        id=_uid(), title="w", slug=f"w-{_uid()[:8]}", status="draft",
        report_id=report.id,
    )
    db.add(widget)
    await db.flush()
    step = Step(
        id=_uid(), title=title, slug=f"st-{_uid()[:8]}", status="success",
        prompt="how much revenue", code=code, widget_id=widget.id,
        data=data if data is not None else {},
    )
    db.add(step)
    await db.flush()
    return step


def _names(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        return z.namelist()


def _read(payload: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        return z.read(name).decode("utf-8")


# ───────────────────────────── it contains the work ────────────────────────


@pytest.mark.asyncio
async def test_the_bundle_carries_the_report_and_its_code():
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        await _step(db, report, code="SELECT revenue FROM sales")

        payload, stats = await build_bundle(db, organization=org, user_id=str(owner.id))

        names = _names(payload)
        assert "manifest.json" in names
        assert any(n.endswith("/report.json") for n in names)
        code_files = [n for n in names if n.endswith(".py")]
        assert code_files, "the generated code — the part somebody rebuilding reads — is missing"
        assert "SELECT revenue FROM sales" in _read(payload, code_files[0])
        assert stats.reports == 1 and stats.steps == 1


@pytest.mark.asyncio
async def test_it_says_the_report_ran_as_its_owner():
    """★A bundle that does not record `shared_run_identity` cannot explain why
    the report stopped working after a handover — that flag is the one part of
    a transfer that is not a column update."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        await _report(db, org, owner)

        payload, _ = await build_bundle(db, organization=org, user_id=str(owner.id))
        name = next(n for n in _names(payload) if n.endswith("/report.json"))

        assert json.loads(_read(payload, name))["shared_run_identity"] == "creator"


@pytest.mark.asyncio
async def test_the_readme_says_what_the_bundle_is_not():
    """★Somebody opens this months later, during the incident it was made for.
    A bundle that looks complete and is not is worse than no bundle."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        await _report(db, org, owner)

        payload, _ = await build_bundle(db, organization=org, user_id=str(owner.id))
        readme = _read(payload, "README.txt").lower()

        assert "not a restore" in readme
        assert "credentials" in readme


# ──────────────────── what it must NOT contain ─────────────────────────────


@pytest.mark.asyncio
async def test_no_credential_shaped_thing_reaches_the_file():
    """★★★The whole bundle is scanned, not one file.

    A whitelist is only as good as its weakest writer, and this is the check
    that notices when a future field slips through any of them.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        await _step(db, report)

        payload, _ = await build_bundle(db, organization=org, user_id=str(owner.id))

        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            blob = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "ignore")

        lowered = blob.lower()
        for banned in (
            "hashed_password",
            "client_secret",
            "client_secret_enc",
            "bind_password",
            # ★NOT the bare word "credentials": README.txt says, correctly, that
            # none are included, and banning the word would make the guard fail
            # on its own explanation — the same mistake that produced three
            # worthless guards on 2026-08-04.
            "user_data_source_credentials",
            "encryption_key",
            # Fernet ciphertext prefix. Anything encrypted with the install key
            # that reached this file would be recognisable by it.
            "gaaaaa",
        ):
            assert banned not in lowered, (
                f"the export contains {banned!r}. Every field is supposed to be "
                "named explicitly — something is dumping an object."
            )


@pytest.mark.asyncio
async def test_a_hostile_title_cannot_escape_the_folder():
    """★★★A report title becomes a path inside the zip. Unzipping is when it
    matters, on a laptop, outside anything this codebase controls."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        await _report(db, org, owner, title="../../../../etc/passwd")

        payload, _ = await build_bundle(db, organization=org, user_id=str(owner.id))

        for name in _names(payload):
            assert not name.startswith("/"), f"absolute path in the zip: {name}"
            assert ".." not in name.split("/"), f"traversal in the zip: {name}"


@pytest.mark.asyncio
async def test_a_shared_report_belongs_to_its_owner_and_stays_there():
    """★Only what they OWNED, never what they could SEE. Otherwise an export is
    a way to take a copy of the whole organization."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner, other = await _member(db, org), await _member(db, org)
        theirs = await _report(db, org, other, title="Somebody elses numbers")
        db.add(
            ReportShare(
                id=_uid(), report_id=theirs.id, user_id=owner.id,
                share_type="artifact",
            )
        )
        await db.flush()

        payload, stats = await build_bundle(db, organization=org, user_id=str(owner.id))

        assert stats.reports == 0
        assert "Somebody elses numbers" not in "".join(_names(payload))


@pytest.mark.asyncio
async def test_an_archived_report_is_not_exported():
    """Delete on this product is `status='archived'`. A backup that restores
    things the person threw away is not a backup of their work."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        report.status = "archived"
        await db.flush()

        _, stats = await build_bundle(db, organization=org, user_id=str(owner.id))
        assert stats.reports == 0


# ──────────────────────────── results and size ─────────────────────────────


@pytest.mark.asyncio
async def test_result_rows_are_included_by_default():
    """★The deliberate call. Excluding them for an admin who can already GET
    every one of these steps would remove nothing they cannot have, while making
    the backup useless for the case it exists for. The control is the audit row,
    not a withheld capability."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        await _step(db, report, data={"rows": [{"revenue": 42}]})

        payload, _ = await build_bundle(db, organization=org, user_id=str(owner.id))
        data_files = [n for n in _names(payload) if n.endswith(".data.json")]

        assert data_files, "the computed rows are missing from the bundle"
        assert "42" in _read(payload, data_files[0])


@pytest.mark.asyncio
async def test_results_can_be_left_out():
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        await _step(db, report, data={"rows": [{"revenue": 42}]})

        payload, _ = await build_bundle(
            db, organization=org, user_id=str(owner.id), include_results=False
        )

        assert not [n for n in _names(payload) if n.endswith(".data.json")]
        assert json.loads(_read(payload, "manifest.json"))["results_included"] is False


@pytest.mark.asyncio
async def test_dropped_results_are_named_in_the_manifest():
    """★★★Truncation must never be silent. "12 steps lost their data" leaves the
    reader unable to tell WHICH twelve, so nobody can fetch them individually —
    and a bundle that quietly stops including things is indistinguishable from a
    complete one."""
    async with async_session_maker() as db:
        org = await _org(db)
        owner = await _member(db, org)
        report = await _report(db, org, owner)
        step = await _step(db, report, data={"rows": [{"x": "y" * 500}]})

        payload, stats = await build_bundle(
            db, organization=org, user_id=str(owner.id), max_result_bytes=10
        )

        manifest = json.loads(_read(payload, "manifest.json"))
        assert manifest["steps_without_results"] == [str(step.id)]
        assert stats.steps == 1, "the step definition was dropped along with its data"
        assert any(n.endswith(".py") for n in _names(payload)), (
            "the generated code went too — a size cap on RESULTS removed the "
            "definition, which is the part that cannot be recomputed"
        )
        assert "too large" in _read(payload, "README.txt")


def test_the_default_cap_is_large_enough_to_be_useful():
    """A cap so low that ordinary exports truncate would make the warning above
    routine, and a warning everybody sees is one nobody reads."""
    assert DEFAULT_MAX_RESULT_BYTES >= 32 * 1024 * 1024


# ──────────────────────── the routes and their gates ───────────────────────


def test_the_two_export_routes_are_gated_the_way_the_release_says():
    """★The admin export is `full_admin_access`, NOT `manage_settings` — unlike
    the read-only count and the orphan listing beside it. Those answer "how
    much"; this hands over the generated code and the last computed rows. Gating
    it like a count is the mistake this asserts against."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app" / "routes" / "ownership.py"
    ).read_text(encoding="utf-8")

    marker = "# ADMIN — transferring on somebody else's behalf"
    member_half, admin_half = src.split(marker)

    assert '"/me/content/export"' in member_half, "the self-export moved out of the member half"
    assert "@requires_permission" not in member_half, (
        "a permission string appeared on the member half; downloading your own "
        "work is not an administrative act"
    )

    idx = admin_half.find('/content-export"')
    assert idx != -1, "the admin export route is gone"
    window = admin_half[idx : idx + 300]
    assert "@requires_permission('full_admin_access')" in window, (
        "the admin export is not gated on the full-admin wildcard"
    )


def test_the_admin_export_is_never_declared_ungated():
    """★The ungated-route baseline is an allow-list somebody edits to make a red
    test green. `export_my_content` belongs there — it selects on the caller's
    own id. `export_member_content` returns the same shape for SOMEBODY ELSE,
    and the cheapest way to silence its decorator would be to add it too. This
    is the assertion that stops that being a quiet one-line edit."""
    from pathlib import Path

    baseline = (
        Path(__file__).resolve().parents[1]
        / "unit" / "fork" / "test_every_route_is_gated.py"
    ).read_text(encoding="utf-8")

    assert '"export_my_content"' in baseline, (
        "the self-export is no longer declared, so either it gained a "
        "permission string it should not have or the baseline drifted"
    )
    assert '"export_member_content"' not in baseline, (
        "the ADMIN export was added to the ungated-route baseline. It hands "
        "over another person's generated code and result rows; it must stay "
        "gated on full_admin_access."
    )


def test_every_export_is_recorded():
    """★★★The real control on this feature, and the reason results are included
    at all. Remove this and the design's own justification stops holding."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app" / "routes" / "ownership.py"
    ).read_text(encoding="utf-8")

    assert src.count("await _record_export(") == 2, (
        "an export path does not write an audit row"
    )
    body = src[src.index("async def _record_export") :][:1800]
    assert 'action="content.exported"' in body
    assert "self_export" in body, (
        "the audit row cannot distinguish somebody downloading their own work "
        "from an administrator downloading somebody else's"
    )
