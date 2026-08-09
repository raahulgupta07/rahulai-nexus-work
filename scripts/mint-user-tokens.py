"""Mint a JWT for each named user so an end-to-end run can act AS them.

No passwords anywhere: this asks the app's own auth strategy for a token, the
same way the login route does once a password has already been checked. That
matters because the passwords for these accounts are not known to tooling, and
inventing a password flow to get a session would be both slower and worse.

★Must live in /app/backend and be run from there — `import main` boots the app
and registers the ORM mappers. Run it from /tmp and the import fails.

★Writes to a PATH ARGUMENT, never stdout. A freshly recreated container prints
its start-up lines first, so anything redirected from stdout begins with
"Loading settings for environment: production" and is not valid JSON. That trap
cost a whole browser-smoke run once; it is not repeated here.

Usage:  python mint-user-tokens.py /tmp/tokens.json email1 email2 ...
"""
import asyncio
import json
import sys

import main  # noqa: F401  — registers the ORM registry; must precede app imports

from sqlalchemy import select

from app.core.auth import get_jwt_strategy
from app.models.user import User
from app.models.organization import Organization
from app.models.membership import Membership
from app.dependencies import async_session_maker


async def mint(emails):
    out = {"org": None, "users": {}}
    strategy = get_jwt_strategy()
    async with async_session_maker() as db:
        for email in emails:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            if not user:
                out["users"][email] = {"error": "no such user"}
                continue

            membership = (await db.execute(
                select(Membership).where(Membership.user_id == user.id)
            )).scalars().first()
            org = None
            if membership:
                org = (await db.execute(
                    select(Organization).where(Organization.id == membership.organization_id)
                )).scalars().first()

            token = await strategy.write_token(user)
            out["users"][email] = {
                "id": str(user.id),
                "token": token,
                "role": getattr(membership, "role", None),
                "is_superuser": bool(user.is_superuser),
                "org_id": str(org.id) if org else None,
                "org_name": org.name if org else None,
            }
            if org and not out["org"]:
                out["org"] = {"id": str(org.id), "name": org.name}
    return out


if __name__ == "__main__":
    path, emails = sys.argv[1], sys.argv[2:]
    result = asyncio.run(mint(emails))
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2)
    # Deliberately terse and secret-free: the tokens are in the file, not here.
    for email, info in result["users"].items():
        print(f"{email}: {'ok' if 'token' in info else info.get('error')}")
    print(f"wrote {path}")
