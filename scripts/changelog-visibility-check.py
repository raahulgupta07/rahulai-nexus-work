"""Live check: the release history is admin-only, against the RUNNING instance.

Not a test and deliberately not in ``tests/`` — it needs a real Postgres, the
real org and the real member rows, none of which a unit or e2e run has. The e2e
guard (``tests/e2e/test_changelog_history_is_admin_only.py``) proves the rule
against a fixture changelog; this proves the deployed image serves it, using the
actual CHANGELOG.md baked into that image.

★**READ-ONLY.** It mints a JWT for an admin and for an existing non-admin member
and issues GETs. It creates no user, writes no row and changes no setting — an
earlier draft invited a throwaway member, which is a permanent write to the live
organization for the sake of an observation, and that trade is not worth making.
If the org has no non-admin member, the member leg is SKIPPED and says so rather
than manufacturing one.

    docker cp scripts/changelog-visibility-check.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python changelog-visibility-check.py

★Copy it into ``/app/backend``, not ``/tmp`` — ``import main`` fails elsewhere.
"""
import asyncio
import os
import sys

import httpx

BASE = os.environ.get("CHECK_BASE_URL", "http://localhost:3000")

sys.path.insert(0, "/app/backend")


async def main() -> int:
    import main as _app_main  # noqa: F401  — registers the ORM registry
    from sqlalchemy import select

    from app.core.auth import get_jwt_strategy
    from app.dependencies import async_session_maker
    from app.models.membership import Membership
    from app.models.organization import Organization
    from app.models.user import User
    # ★Imported, never duplicated: a change to the limit must move this check too.
    from app.routes.changelog import PUBLIC_VERSION_LIMIT

    failures = []

    async with async_session_maker() as db:
        org = (await db.execute(select(Organization).limit(1))).scalars().first()
        if org is None:
            print("no organization on this instance — nothing to check")
            return 1
        org_id = str(org.id)

        admin_row = (await db.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org.id,
                Membership.role == "admin",
                Membership.deleted_at.is_(None),
            )
            .limit(1)
        )).scalars().first()

        member_row = (await db.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org.id,
                Membership.role != "admin",
                Membership.deleted_at.is_(None),
                User.is_superuser.is_(False),
            )
            .limit(1)
        )).scalars().first()

        if admin_row is None:
            print("no admin membership found — cannot check the admin view")
            return 1

        strategy = get_jwt_strategy()
        admin_token = await strategy.write_token(admin_row)
        member_token = (
            await strategy.write_token(member_row) if member_row is not None else None
        )
        member_email = member_row.email if member_row is not None else None

    def hdr(token):
        return {"Authorization": f"Bearer {token}", "X-Organization-Id": org_id}

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        # 1. Anonymous — the public view, and never a 401.
        #    ★The 401 case is the one that matters most: versionCheck.client.ts
        #    polls this route unauthenticated and swallows every error, so a
        #    gate here would kill the new-build toast in total silence.
        r = await client.get("/api/changelog")
        if r.status_code != 200:
            failures.append(f"anonymous got {r.status_code}, expected 200")
            return _report(failures)
        anon = r.json()
        total = anon["total_versions"]
        if len(anon["versions"]) != PUBLIC_VERSION_LIMIT:
            failures.append(
                f"anonymous saw {len(anon['versions'])} releases, "
                f"expected {PUBLIC_VERSION_LIMIT}"
            )
        if not anon.get("current_version"):
            failures.append("anonymous got no current_version — version poller is blind")

        # 2. Admin — the whole history.
        r = await client.get("/api/changelog", headers=hdr(admin_token))
        adm = r.json()
        if len(adm["versions"]) != total:
            failures.append(
                f"admin saw {len(adm['versions'])} of {total} releases, expected all"
            )
        if adm.get("truncated") is not False:
            failures.append("admin response claims to be truncated")

        print(f"anon    : {[v['version'] for v in anon['versions']]}  "
              f"(current_version {anon['current_version']})")
        print(f"admin   : {len(adm['versions'])} of {total} releases  "
              f"[{admin_row.email}]")

        # 3. A real member of the same org — the public view.
        if member_token is None:
            print("member  : SKIPPED — no non-admin membership on this instance")
        else:
            r = await client.get("/api/changelog", headers=hdr(member_token))
            mem = r.json()
            if len(mem["versions"]) != PUBLIC_VERSION_LIMIT:
                failures.append(
                    f"member saw {len(mem['versions'])} releases, "
                    f"expected {PUBLIC_VERSION_LIMIT}"
                )
            if mem.get("truncated") is not True:
                failures.append("member response does not say it is truncated")
            # ★The withheld releases must be ABSENT from the body, not merely
            # flagged — a frontend-only cut would pass every check above.
            oldest = adm["versions"][-1]["version"] if adm["versions"] else None
            if oldest and any(v["version"] == oldest for v in mem["versions"]):
                failures.append(f"member can still read the oldest release {oldest}")
            if oldest and oldest in r.text:
                failures.append(f"the withheld release {oldest} is still in the body")
            print(f"member  : {[v['version'] for v in mem['versions']]}  "
                  f"[{member_email}]")

    return _report(failures, total, PUBLIC_VERSION_LIMIT)


def _report(failures, total=None, limit=None) -> int:
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nOK — {total} releases total, non-admins see {limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
