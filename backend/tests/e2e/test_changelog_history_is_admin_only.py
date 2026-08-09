"""The full release history is for administrators; everyone else sees the newest few.

Contract under test (``GET /api/changelog``):

- An org admin gets every version in CHANGELOG.md.
- An ordinary member gets ``PUBLIC_VERSION_LIMIT`` versions, newest first, and
  is told so via ``truncated``/``total_versions`` rather than being handed a
  short list that silently passes for the whole history.
- An anonymous caller gets the same public view — the route must not 401.
- ``current_version`` is served to everyone, authenticated or not.

★That last one is not decoration. ``frontend/plugins/versionCheck.client.ts``
polls this endpoint every 60s with a bare ``$fetch`` carrying no auth header, to
notice that a new build was deployed. Its error path is ``return null`` with the
comment "never nag on errors", so putting a normal auth dependency on this route
would not fail loudly — it would delete the new-version toast permanently and
log nothing. ``test_the_version_poller_still_gets_an_answer_unauthenticated``
exists so that a future tightening of this endpoint fails here first.

★The changelog is pinned to a FIXTURE file rather than the repo's real
CHANGELOG.md. Asserting against the real file would make the test's meaning
drift with every release — and once the repo happened to hold exactly three
versions, the member and admin cases would agree and the test would pass while
measuring nothing.
"""
import uuid

import pytest

from app.routes.changelog import PUBLIC_VERSION_LIMIT, _load_changelog


FIXTURE_CHANGELOG = """# Changelog

## Version 9.9.5 (2026-08-07)

- Newest release.

## Version 9.9.4 (2026-08-06)

- Fourth.

## Version 9.9.3 (2026-08-05)

- Third.

## Version 9.9.2 (2026-08-04)

- Second.

## Version 9.9.1 (2026-08-03)

- Oldest release.
"""

TOTAL_VERSIONS = 5


@pytest.fixture
def pinned_changelog(tmp_path, monkeypatch):
    """Point the endpoint at a known 5-release changelog.

    ★``_load_changelog`` is ``@lru_cache(maxsize=1)``, so the cache must be
    cleared on the way IN (the real file may already be cached from another
    test) and on the way OUT (or every later test inherits this fixture).
    """
    path = tmp_path / "CHANGELOG.md"
    path.write_text(FIXTURE_CHANGELOG, encoding="utf-8")
    monkeypatch.setenv("CHANGELOG_PATH", str(path))
    _load_changelog.cache_clear()
    yield path
    _load_changelog.cache_clear()


def _headers(token, org_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = str(org_id)
    return headers


def _admin_and_member(create_user, login_user, whoami, test_client):
    """First registrant bootstraps the org and is its admin; invite a member."""
    admin_email = f"cl_admin_{uuid.uuid4().hex[:6]}@test.com"
    create_user(email=admin_email, password="test123")
    admin_token = login_user(email=admin_email, password="test123")
    org_id = whoami(admin_token)["organizations"][0]["id"]

    member_email = f"cl_member_{uuid.uuid4().hex[:6]}@test.com"
    invite = test_client.post(
        f"/api/organizations/{org_id}/members",
        json={"organization_id": org_id, "email": member_email, "role": "member"},
        headers=_headers(admin_token, org_id),
    )
    assert invite.status_code == 200, invite.json()

    create_user(email=member_email, password="test123")
    member_token = login_user(email=member_email, password="test123")

    return admin_token, member_token, org_id


@pytest.mark.e2e
def test_an_admin_sees_every_release(
    pinned_changelog, test_client, create_user, login_user, whoami
):
    admin_token, _, org_id = _admin_and_member(
        create_user, login_user, whoami, test_client
    )

    resp = test_client.get("/api/changelog", headers=_headers(admin_token, org_id))
    assert resp.status_code == 200, resp.json()
    body = resp.json()

    assert len(body["versions"]) == TOTAL_VERSIONS
    assert body["truncated"] is False
    assert body["total_versions"] == TOTAL_VERSIONS
    assert [v["version"] for v in body["versions"]][0] == "9.9.5"
    # The oldest release is the one a member must NOT be able to reach.
    assert body["versions"][-1]["version"] == "9.9.1"


@pytest.mark.e2e
def test_a_member_sees_only_the_newest_releases(
    pinned_changelog, test_client, create_user, login_user, whoami
):
    _, member_token, org_id = _admin_and_member(
        create_user, login_user, whoami, test_client
    )

    resp = test_client.get("/api/changelog", headers=_headers(member_token, org_id))
    assert resp.status_code == 200, resp.json()
    body = resp.json()

    assert len(body["versions"]) == PUBLIC_VERSION_LIMIT
    assert [v["version"] for v in body["versions"]] == ["9.9.5", "9.9.4", "9.9.3"]
    assert body["truncated"] is True
    assert body["total_versions"] == TOTAL_VERSIONS
    # The entries themselves must be gone, not merely hidden behind a flag.
    assert "Oldest release." not in resp.text


@pytest.mark.e2e
def test_a_member_cannot_widen_the_view_with_someone_elses_org_header(
    pinned_changelog, test_client, create_user, login_user, whoami
):
    """A header is a claim, not a credential.

    ★The admin check resolves permissions for (caller, org-in-header). Naming an
    organization the caller does not belong to must resolve to an empty
    permission set, not to that org's admin rights — so this asserts the public
    view, not a 500 or a widened one.
    """
    _, member_token, _ = _admin_and_member(
        create_user, login_user, whoami, test_client
    )
    stranger_org = str(uuid.uuid4())

    resp = test_client.get("/api/changelog", headers=_headers(member_token, stranger_org))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["versions"]) == PUBLIC_VERSION_LIMIT


@pytest.mark.e2e
def test_an_anonymous_caller_gets_the_public_view_not_a_401(
    pinned_changelog, test_client
):
    resp = test_client.get("/api/changelog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["versions"]) == PUBLIC_VERSION_LIMIT
    assert body["truncated"] is True


@pytest.mark.e2e
def test_the_version_poller_still_gets_an_answer_unauthenticated(
    pinned_changelog, test_client
):
    """Pins ``versionCheck.client.ts``. See this module's docstring."""
    resp = test_client.get("/api/changelog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_version"], "the new-build detector reads current_version"
    assert body["available"] is True
