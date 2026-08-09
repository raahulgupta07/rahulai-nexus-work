"""The key that signs a session must not be the key that encrypts the database.

MEASURED DEFECT (2026-08-09, live install). `core/auth.py` read
`SECRET = settings.dash_config.encryption_key` and handed it straight to
`JWTStrategy`. That is the Fernet key protecting every stored secret — SMTP
password, LDAP bind password, every SSO client secret. A token forged with
nothing but that key was accepted by `/api/users/me` as the superuser.

Two consequences, and the second is the one that makes this urgent:

  - a key leak stops being "the attacker can decrypt stored credentials" and
    becomes "…and can forge a session as anyone, including the owner".
  - there is no clean recovery. Rotating the signing secret to kill stolen
    sessions means rotating the Fernet key, which permanently destroys every
    stored credential. The safe response to a leak was also the destructive one.

★The fix was not a new idea. `app/core/file_tokens.py` already derived a
dedicated secret for a far less sensitive token, with a comment stating the rule
outright: "the raw key is never used directly as the JWT secret". This guard
exists because the codebase knew the rule and applied it in the wrong place.

Each purpose also gets its OWN domain prefix, so a password-reset token and a
session token are signed with different keys — neither can be presented as the
other even if the claim checks were ever weakened.
"""
import hashlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.settings.config import settings  # noqa: E402


def _raw_key():
    return settings.dash_config.encryption_key


def test_the_session_secret_is_not_the_encryption_key():
    """The whole point. If this fails, a key leak forges sessions."""
    from app.core.auth import SECRET
    raw = _raw_key()
    assert SECRET != raw, (
        "the session JWT is signed with the raw Fernet key — a leak of it "
        "forges any session AND decrypts every stored credential, and the two "
        "cannot be rotated independently"
    )


def test_the_reset_and_verification_secrets_are_not_the_encryption_key():
    from app.core.auth import RESET_PASSWORD_SECRET, VERIFICATION_SECRET
    raw = _raw_key()
    assert RESET_PASSWORD_SECRET != raw
    assert VERIFICATION_SECRET != raw


def test_each_purpose_has_its_own_secret():
    """A reset token must not be usable as a session token."""
    from app.core.auth import (
        SECRET, RESET_PASSWORD_SECRET, VERIFICATION_SECRET,
    )
    assert SECRET != RESET_PASSWORD_SECRET
    assert SECRET != VERIFICATION_SECRET
    assert RESET_PASSWORD_SECRET != VERIFICATION_SECRET


def test_a_token_forged_from_the_encryption_key_does_not_verify():
    """The exploit, expressed as a test.

    This is what was demonstrated against the running app: sign a token with the
    Fernet key alone and present it. It must now fail signature verification.
    """
    jwt = pytest.importorskip("jwt")
    from app.core.auth import SECRET
    raw = _raw_key()
    if not raw:
        pytest.skip("no encryption key configured in this environment")
    forged = jwt.encode({"sub": "anyone"}, raw, algorithm="HS256")
    with pytest.raises(Exception):
        jwt.decode(forged, SECRET, algorithms=["HS256"])


def test_the_derivation_is_stable():
    """Same installation key in, same signing secret out.

    If this drifted, every restart would invalidate every session — the fix
    would read as "users are randomly logged out" rather than as a security
    improvement.
    """
    from app.core.auth import _derive_token_secret, SECRET
    assert _derive_token_secret(b"dash-session-jwt") == SECRET


def test_the_derivation_actually_depends_on_the_key():
    """Self-test: a constant would satisfy every assertion above.

    Without this, `SECRET = "hardcoded"` would pass the whole file — different
    from the raw key, distinct per purpose, stable across calls, and completely
    insecure. Prove the key is an input.
    """
    from app.core.auth import _derive_token_secret
    a = hashlib.sha256(b"dash-session-jwt:" + b"key-one").hexdigest()
    b = hashlib.sha256(b"dash-session-jwt:" + b"key-two").hexdigest()
    assert a != b, "derivation must depend on the installation key"
    # And the real function must agree with that construction.
    raw = _raw_key()
    if raw:
        raw_b = raw.encode() if isinstance(raw, str) else raw
        expected = hashlib.sha256(b"dash-session-jwt:" + raw_b).hexdigest()
        assert _derive_token_secret(b"dash-session-jwt") == expected
