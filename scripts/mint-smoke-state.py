"""Write a Playwright storageState for the live admin to a FILE.

Run INSIDE the app container — it needs the app's own signing key and ORM:

    docker cp scripts/mint-smoke-state.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python mint-smoke-state.py /tmp/smoke-state.json
    docker cp dash-app:/tmp/smoke-state.json /tmp/smoke-state.json

★★★A file, not stdout, and the redirect form is gone on purpose. `import main`
boots the whole application, and on a freshly recreated container it prints its
own start-up lines first — "Loading settings for environment: production", the
config path, a JSON telemetry log. Redirecting stdout captures all of that ahead
of the JSON, and Playwright fails with

    SyntaxError: Error reading storage state ...
    Unexpected token 'L', "Loading se"... is not valid JSON

which arrives as 18 failed browser tests and reads exactly like the release
being broken. Measured on the 0.0.518.2 deploy: the container had just been
recreated, so the start-up chatter appeared for the first time. Nothing about
the product had changed.

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

    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/smoke-state.json"
    with open(out, "w") as fh:
        json.dump(state, fh)
    # Safe to print — this goes nowhere near the JSON now.
    print(f"wrote {out}")


asyncio.run(main_())
