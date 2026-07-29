"""An agent manager may publish changes to their OWN agent.

Builds are created with ``copy_from_main=True``, so a build always CONTAINS
every instruction in the organization — deliberate, it is how the promoted
build carries everything else forward untouched. The auto-publish gate used to
judge permission over that whole copied set, which made the documented "agent
admin" tier unreachable the moment an org had a second agent:

    build contains  CRM (mine, manage=yes) + Microsoft Fabric + City Mart
    all(...)     -> False
    build stays  -> pending_approval, forever

Visible symptom: "Accept all does nothing". The click was recorded, a build was
created, it could never publish, and the next click made another one. One org
accumulated ten stuck builds.

The rule locked in here: gate on what the build CHANGES relative to main, not
on what it contains. Inheriting a row untouched is not authoring it.

These call the real `_can_auto_publish_build`. The database is replaced by a
fake session that returns queued rows in query order, so this stays in the fast
fork suite — but the logic under test is the shipped logic, not a restatement
of it.
"""
import inspect

import pytest

from app.services.instruction_service import InstructionService

CRM, FABRIC, CITYMART = "ds-crm", "ds-fabric", "ds-citymart"
MAIN_BUILD = "build-main"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0][0] if self._rows else None


class _FakeSession:
    """Returns queued results in the order the gate issues its queries."""

    def __init__(self, queued):
        self._queued = list(queued)

    async def execute(self, _stmt):
        return _Result(self._queued.pop(0))


class _Build:
    id = "build-under-test"
    organization_id = "org-1"


class _Resolved:
    def __init__(self, allowed):
        self._allowed = set(allowed)

    def has_resource_permission(self, _rt, resource_id, _perm):
        return resource_id in self._allowed


async def _run_gate(monkeypatch, *, this_build, main_contents, assoc, allowed):
    """Drive the real gate. `this_build`/`main_contents` are (instr, version) pairs."""
    svc = InstructionService()
    monkeypatch.setattr(svc, "_is_admin_permissions", lambda _perms: False)

    async def fake_resolve(_db, _uid, _oid):
        return _Resolved(allowed)

    import app.core.permission_resolver as pr
    monkeypatch.setattr(pr, "resolve_permissions", fake_resolve)

    db = _FakeSession([
        this_build,                 # 1. this build's contents
        [(MAIN_BUILD,)],            # 2. the main build id
        main_contents,              # 3. main's contents
        assoc,                      # 4. instruction -> data_source
    ])

    class _User:
        id = "user-1"

    return await svc._can_auto_publish_build(db, _Build(), _User(), set())


@pytest.mark.asyncio
async def test_manager_publishes_a_change_to_their_own_agent(monkeypatch):
    """The reported bug. Only the CRM instruction differs from main."""
    decision = await _run_gate(
        monkeypatch,
        this_build=[("i-crm", "v2"), ("i-fabric", "v1"), ("i-citymart", "v1")],
        main_contents=[("i-crm", "v1"), ("i-fabric", "v1"), ("i-citymart", "v1")],
        assoc=[("i-crm", CRM), ("i-fabric", FABRIC), ("i-citymart", CITYMART)],
        allowed={CRM},
    )
    assert decision is True, (
        "a manager changed only their own agent's instruction; the two agents "
        "they do not manage were inherited unchanged via copy_from_main and "
        "must not block publication"
    )


@pytest.mark.asyncio
async def test_manager_cannot_publish_a_change_to_someone_elses_agent(monkeypatch):
    """The permission still has to mean something."""
    decision = await _run_gate(
        monkeypatch,
        this_build=[("i-crm", "v2"), ("i-fabric", "v2")],
        main_contents=[("i-crm", "v1"), ("i-fabric", "v1")],
        assoc=[("i-crm", CRM), ("i-fabric", FABRIC)],
        allowed={CRM},
    )
    assert decision is False


@pytest.mark.asyncio
async def test_a_changed_global_instruction_still_needs_an_org_admin(monkeypatch):
    """Global (no data source) authoring stays an org-level capability."""
    decision = await _run_gate(
        monkeypatch,
        this_build=[("i-global", "v2")],
        main_contents=[("i-global", "v1")],
        assoc=[],  # no data source → global
        allowed={CRM},
    )
    assert decision is False


@pytest.mark.asyncio
async def test_an_inherited_global_instruction_does_not_block(monkeypatch):
    """A global instruction that main already had, carried forward unchanged.

    Under the old logic this alone was enough to force admin review on every
    build in the org.
    """
    decision = await _run_gate(
        monkeypatch,
        this_build=[("i-crm", "v2"), ("i-global", "v1")],
        main_contents=[("i-crm", "v1"), ("i-global", "v1")],
        assoc=[("i-crm", CRM)],
        allowed={CRM},
    )
    assert decision is True


@pytest.mark.asyncio
async def test_a_build_identical_to_main_is_not_left_pending(monkeypatch):
    """Nothing changed → nothing to authorize.

    Refusing here would leave a no-op build pending with no action that could
    ever clear it.
    """
    decision = await _run_gate(
        monkeypatch,
        this_build=[("i-crm", "v1")],
        main_contents=[("i-crm", "v1")],
        assoc=[("i-crm", CRM)],
        allowed=set(),
    )
    assert decision is True


def test_gate_still_compares_against_main():
    """Guards against a silent revert to 'everything in the build'."""
    src = inspect.getsource(InstructionService._can_auto_publish_build)
    assert "is_main" in src and "main_versions" in src, (
        "_can_auto_publish_build no longer compares against the main build — it "
        "is judging every instruction copied in by copy_from_main again, which "
        "makes the agent-admin tier unreachable in any multi-agent org."
    )
    # ★ A duplicate is_main row must not turn a permission check into a 500.
    after = src.split("is_main")[1][:400]
    assert "scalar_one_or_none" not in after, (
        "main-build lookup must tolerate a duplicate is_main row"
    )
