"""Being admitted and being placed are two different things.

Phase S1 let a trusted identity provider create an account. It stopped there —
the person signed in successfully and landed in a product with no organization,
no agents and nothing to ask a question about. An account without a membership
is not an account anybody can use, so admission was only half-built.

Three faults, all the same shape: something creates a user and nothing decides
where they belong.

  1. SSO auto-provision created the user and no membership.
  2. LDAP auto-provision created the user and no membership — and it is the one
     door that could always have known the answer, because a directory is
     configured against a specific workspace.
  3. A membership on its own is not permission. The resolver reads
     ``role_assignments``; ``Membership.role`` is a label beside it. One without
     the other is a member of a workspace who can see nothing in it, which
     reads as a broken product rather than a permission problem.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AUTH = REPO / "backend" / "app" / "core" / "auth.py"
SVC = REPO / "backend" / "app" / "services" / "organization_settings_service.py"
SCHEMA = REPO / "backend" / "app" / "schemas" / "organization_settings_schema.py"
ROUTES = REPO / "backend" / "app" / "routes" / "organization_settings.py"
ORGSVC = REPO / "backend" / "app" / "services" / "organization_service.py"


def _fn(src: str, header: str) -> str:
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n    (?:async def |def |[A-Z_]+ = )", rest)
    return rest[: nxt.start()] if nxt else rest


# ---------------------------------------------------------------------------
# Both doors place the people they admit
# ---------------------------------------------------------------------------
def test_the_sso_door_places_the_people_it_admits():
    """★The other half of S1. Without this the provider check hands somebody an
    account and an empty product."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def oauth_callback(")
    assert "_place_auto_provisioned_user(" in body


def test_the_directory_door_places_the_people_it_admits():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _ldap_authenticate(")
    assert "_place_auto_provisioned_user(" in body


def test_placement_happens_before_the_commit_on_both_doors():
    """★The account and the membership must be ONE transaction. Placed after the
    commit, a crash in between leaves exactly the orphaned account this whole
    phase exists to remove."""
    src = AUTH.read_text(encoding="utf-8")
    for header in ("    async def oauth_callback(", "    async def _ldap_authenticate("):
        body = _fn(src, header)
        place = body.index("_place_auto_provisioned_user(")
        commit = body.index("await session.commit()", place - 4000 if place > 4000 else 0)
        # the first commit at or after the placement call must come after it
        after = body.find("await session.commit()", place)
        assert after > place, f"{header.strip()}: no commit follows the placement"


def test_placement_uses_the_callers_session():
    """★Not a new session. A separate session could not participate in the
    caller's transaction, which is the entire point."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "async_session_maker" not in body, (
        "opening its own session breaks the account/membership transaction"
    )


def test_the_directory_door_passes_its_own_organization():
    """★A directory is configured against a specific workspace, so it is the one
    door that can answer 'which one' for itself. S1 already resolves it."""
    src = AUTH.read_text(encoding="utf-8")
    body = _fn(src, "    async def _ldap_authenticate(")
    assert re.search(r"_place_auto_provisioned_user\(\s*session,\s*\w+,\s*ldap_org_id", body, re.S)

    do_auth = _fn(src, "    async def _do_authenticate(")
    assert "ldap_config, ldap_org_id = await self._login_ldap_config()" in do_auth
    assert re.search(
        r"_ldap_authenticate\(\s*credentials\.username,\s*credentials\.password,\s*ldap_config,\s*ldap_org_id",
        do_auth, re.S,
    )


def test_the_login_config_helper_returns_the_org_alongside_the_config():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _login_ldap_config(")
    assert "return await OrganizationSettingsService().resolve_login_ldap_config(_db)" in body
    assert "return settings.dash_config.ldap, None" in body, (
        "the fallback must return a pair too, or unpacking raises and every "
        "database blip becomes a total login outage"
    )


# ---------------------------------------------------------------------------
# What placement must NOT do
# ---------------------------------------------------------------------------
def test_an_existing_membership_is_never_overwritten():
    """An invite already placed them, and a human chose that role. Never
    second-guess it."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    idx = body.index("select(Membership)")
    assert "Membership.user_id == user.id" in body[idx:idx + 400]
    assert "return False" in body[idx:idx + 800]


def test_it_refuses_to_guess_between_several_organizations():
    """★SSO is instance-global and does not say which workspace it speaks for.
    With one org there is no question; with several, a guess puts somebody in
    another company's data."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "len(org_rows) == 1" in body
    assert "len(org_rows) > 1" in body
    guess = body.index("len(org_rows) > 1")
    assert "return False" in body[guess:guess + 700], (
        "an ambiguous placement must refuse, not pick one"
    )


def test_the_first_user_bootstrap_is_left_alone():
    """No org exists yet on a fresh install — `_ensure_org_for_first_uninvited_user`
    owns that, and it also makes them the superuser. Placement must not race it."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    tail = body[body.index("len(org_rows) > 1"):]
    assert "else:" in tail and "return False" in tail


def test_every_write_is_inside_a_savepoint():
    """★★★`except` does not contain a database error.

    Found live, not by any static check: a failed INSERT aborts the WHOLE
    Postgres transaction, so swallowing the exception leaves the caller's
    session poisoned and its own `commit()` raises PendingRollbackError. The
    sign-in 500s and the account it just created is rolled back with it —
    precisely the failure the swallow claims to prevent. The savepoint is what
    makes the claim true.
    """
    src = AUTH.read_text(encoding="utf-8")
    for header in (
        "    async def _place_auto_provisioned_user(",
        "    async def _assign_system_role(",
    ):
        body = _fn(src, header)
        for stmt in ("session.add(Membership(", "session.add(RoleAssignment("):
            if stmt not in body:
                continue
            before = body[: body.index(stmt)]
            assert "session.begin_nested()" in before, (
                f"{header.strip()}: {stmt} is not inside a savepoint — a failed "
                f"write would abort the caller's transaction"
            )


def test_an_unknown_organization_is_refused_before_the_insert():
    """A directory pointed at a deleted organization must produce a log line,
    not a foreign-key error inside somebody's sign-in."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    check = body.index("Organization.deleted_at.is_(None)")
    insert = body.index("session.add(Membership(")
    assert check < insert
    assert "if not exists:" in body


def test_a_placement_failure_never_breaks_the_sign_in():
    """★The authentication already succeeded. Raising here surfaces as a broken
    login AND still leaves the account behind — strictly worse than today."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "except Exception" in body
    assert body.rstrip().endswith("return False")


def test_the_sso_call_does_not_pass_an_organization():
    """Instance-global providers have none to pass; the helper resolves it."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def oauth_callback(")
    assert re.search(r'_place_auto_provisioned_user\(\s*session,\s*user,\s*None,\s*source="sso"', body, re.S)


# ---------------------------------------------------------------------------
# A membership without a role assignment is a seat with no permissions
# ---------------------------------------------------------------------------
def test_placement_creates_the_role_assignment_too():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "_assign_system_role(" in body
    assert "Membership(" in body


def test_the_invite_path_and_the_auto_path_share_one_role_helper():
    """★Extracted so they cannot drift. One of them getting RBAC right and the
    others not is precisely the bug being fixed."""
    src = AUTH.read_text(encoding="utf-8")
    attach = _fn(src, "    async def _attach_open_memberships(")
    assert "_assign_system_role(" in attach
    assert "RoleAssignment(" not in attach, (
        "the invite path still has its own inline copy — two implementations of "
        "the same rule is how they diverge"
    )


def test_the_role_helper_matches_the_other_one_argument_for_argument():
    """★★`OrganizationService._assign_system_role(db, org_id, user_id, role_name)`
    already exists. Two same-named helpers whose last two arguments are SWAPPED
    is a bug waiting for the first person who moves a line between them — and it
    would silently assign a role named after a user id."""
    ours = _fn(AUTH.read_text(encoding="utf-8"), "    async def _assign_system_role(")
    theirs_src = ORGSVC.read_text(encoding="utf-8")
    assert "async def _assign_system_role(self, db: AsyncSession, org_id: str, user_id: str, role_name: str)" in theirs_src, (
        "the reference signature moved; re-check the order in auth.py"
    )
    sig = ours[: ours.index(") -> bool:")]
    order = [p.strip().split(":")[0].strip() for p in sig.split(",")]
    order = [p for p in order if p and p != "self"]
    assert order[:4] == ["session", "organization_id", "user_id", "role_name"], order


def test_the_role_helper_is_idempotent_and_swallows():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _assign_system_role(")
    assert "if existing:" in body
    assert "except Exception" in body


def test_a_missing_role_assignment_is_reported_not_hidden():
    """A seat with no permissions is the failure mode that looks like success.
    If it happens, say so in the log."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "if not self_assigned:" in body
    assert "log.warning" in body[body.index("if not self_assigned:"):]


# ---------------------------------------------------------------------------
# The role is an admin's choice, in one place, for both doors
# ---------------------------------------------------------------------------
def test_the_role_setting_exists_and_defaults_to_member():
    src = SCHEMA.read_text(encoding="utf-8")
    assert "class AutoProvision(BaseModel):" in src
    assert "auto_provision: AutoProvision = AutoProvision()" in src
    for line in src.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("role:") and "AutoProvision" not in stripped:
            assert 'str = "member"' in stripped, stripped


def test_there_is_exactly_one_role_setting_for_both_doors():
    """★No per-door role. Two settings answering the same question are two
    settings that can disagree, and the disagreement shows up as one door
    quietly granting more than the other."""
    src = SCHEMA.read_text(encoding="utf-8")
    assert "sso_role" not in src and "ldap_role" not in src


def test_the_runtime_resolver_needs_no_caller():
    """★`get_settings` requires a `current_user`. There isn't one during sign-in
    — the account is being created right now. That is why this is separate."""
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def resolve_auto_provision_role(")
    # ★Strip the docstring before asserting on the CODE — it names both of these
    # deliberately, to explain why they are absent. Scanning the whole body made
    # the explanation fail the assertion it was explaining.
    code = body.split('"""')[-1]
    assert "current_user" not in code
    assert "get_settings" not in code


def test_the_runtime_resolver_falls_back_to_member():
    """A missing setting must still produce a usable account — refusing to place
    somebody is the failure this phase removes."""
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def resolve_auto_provision_role(")
    assert "except Exception" in body
    assert body.count('return "member"') >= 1
    assert 'return role or "member"' in body


def test_placement_reads_the_setting_rather_than_hardcoding_a_role():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _place_auto_provisioned_user(")
    assert "resolve_auto_provision_role(" in body
    assert 'role="member"' not in body and "role='member'" not in body


def test_an_unknown_role_is_refused_at_the_write():
    """★A typo would hand every future arrival a membership with nothing behind
    it — a sign-in that looks fine and a product that does nothing."""
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def update_auto_provision(")
    assert "status_code=400" in body
    assert "Role.deleted_at.is_(None)" in body


def test_the_role_write_is_audited():
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def update_auto_provision(")
    assert "settings.auto_provision_role_updated" in body


def test_the_setting_is_reachable_and_admin_only():
    src = ROUTES.read_text(encoding="utf-8")
    assert '@router.get("/organization/auto-provision"' in src
    assert '@router.put("/organization/auto-provision"' in src
    gets = src.index('@router.get("/organization/auto-provision"')
    assert "@requires_permission('full_admin_access')" in src[gets:gets + 200]
    puts = src.index('@router.put("/organization/auto-provision"')
    assert "@requires_permission('full_admin_access')" in src[puts:puts + 200]


def test_the_settings_write_goes_through_the_validating_service():
    """★Never raw-SQL an org setting: `FeatureConfig.description` is required and
    a partial write makes EVERY later read of the whole settings blob 500."""
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def update_auto_provision(")
    assert "flag_modified(settings" in body, (
        "SQLAlchemy misses an in-place JSON mutation without this"
    )
    assert "current_config = dict(settings.config)" in body
