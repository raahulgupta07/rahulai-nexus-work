"""Emit a Playwright storageState for the live admin, on stdout.

Run INSIDE the app container — it needs the app's own signing key and ORM:

    docker cp scripts/mint-smoke-state.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python mint-smoke-state.py > /tmp/smoke-state.json

★It must sit in /app/backend, not /tmp: `import main` is what registers the ORM
registry, and that import only resolves from there.

★The cookie is `auth.token` (not `auth_token`) — the other spelling authenticates
nothing and the app simply redirects to the login page, which reads as the smoke
suite being broken.

★No password is involved. The live admin's password is not known to tooling, so
E2E work mints a JWT directly instead. See CLAUDE.md, "Admin / auth".

The token is short-lived and grants full admin. Write it to a temp file, not into
the repo.
"""
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, "/app/backend")
import main  # noqa: F401  — registers the ORM registry

from sqlalchemy import select

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

# Which account to impersonate, and which origin the cookie is scoped to.
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")
BASE = os.environ.get("PLAYWRIGHT_BASE_URL", "http://localhost:8095")


async def main_() -> None:
    async with async_session_maker() as db:
        row = await db.execute(select(User).where(User.email == EMAIL))
        user = row.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"no user {EMAIL!r} in this database — pass SMOKE_EMAIL")
        token = await get_jwt_strategy().write_token(user)

    host = urlparse(BASE).hostname or "localhost"
    state = {
        "cookies": [{
            "name": "auth.token",
            "value": token,
            "domain": host,
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": BASE.startswith("https"),
            "sameSite": "Lax",
        }],
        "origins": [],
    }
    print(json.dumps(state))


asyncio.run(main_())
