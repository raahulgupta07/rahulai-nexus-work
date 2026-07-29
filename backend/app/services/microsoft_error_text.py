"""Turn a Microsoft sign-in error into something a member can act on.

Microsoft returns errors like::

    AADSTS700016: Application with identifier '1950a258-…' was not found in the
    directory 'contoso.onmicrosoft.com'. This can happen if the application has
    not been installed by the administrator of the tenant…  Trace ID: 3f2c…
    Correlation ID: 8b1e…  Timestamp: 2026-07-28 06:11:02Z

Handing that to somebody who wanted to look at last month's sales tells them
nothing they can do. It also leaks tenant names and trace identifiers into a
browser. This module maps the codes we actually see onto:

    message : what happened, in a sentence
    action  : what the member (or their admin) should do about it
    code    : the AADSTS code, kept for logs and support — never the whole blob

★The raw text is NOT discarded — callers log it. What changes is what reaches
the screen. An unmapped code degrades to a generic sentence plus the code
itself, so support can still trace it and the member still gets a next step.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

# The code is always the first token of the description.
_CODE_RE = re.compile(r"\bAADSTS(\d{4,7})\b")

# Keyed on the numeric part. Sourced from the codes this connector actually
# produces (the MFA set in `powerbi_device_code`, plus what live sign-ins hit).
_MESSAGES: Dict[str, Dict[str, str]] = {
    # --- credentials -------------------------------------------------------
    "50126": {
        "message": "That email or password was not accepted by Microsoft.",
        "action": "Check them and try again. If you normally sign in with a "
                  "company button rather than a password, use Connect and pick "
                  "the code option.",
    },
    "50034": {
        "message": "Microsoft does not recognise that email address.",
        "action": "Check the spelling, and make sure it is your work account "
                  "rather than a personal one.",
    },
    "50053": {
        "message": "Your Microsoft account is locked.",
        "action": "Too many failed sign-ins. Wait a few minutes, or ask whoever "
                  "manages Microsoft 365 for your organisation to unlock it.",
    },
    "50055": {
        "message": "Your Microsoft password has expired.",
        "action": "Change it with Microsoft first, then come back and connect.",
    },
    "50057": {
        "message": "Your Microsoft account is disabled.",
        "action": "Ask whoever manages Microsoft 365 for your organisation.",
    },
    "50058": {
        "message": "Microsoft could not tell who was signing in.",
        "action": "Try again. If it keeps happening, sign out of Microsoft in "
                  "this browser and start over.",
    },
    # --- second factor / policy -------------------------------------------
    "50076": {
        "message": "Your organisation requires a second step to sign in.",
        "action": "Use the code shown here to finish on the Microsoft page.",
    },
    "50079": {
        "message": "Your organisation requires you to set up a second sign-in step.",
        "action": "Finish the setup with Microsoft, then connect again.",
    },
    "50072": {
        "message": "Your organisation requires you to set up a second sign-in step.",
        "action": "Finish the setup with Microsoft, then connect again.",
    },
    "50158": {
        "message": "Your organisation applies an extra security check to this sign-in.",
        "action": "Use the code shown here to finish on the Microsoft page.",
    },
    "53000": {
        "message": "This device does not meet your organisation's security rules.",
        "action": "Try from a managed device, or ask whoever manages Microsoft "
                  "365 for your organisation.",
    },
    "53003": {
        "message": "Your organisation's access policy blocked this sign-in.",
        "action": "Ask whoever manages Microsoft 365 for your organisation to "
                  "allow it.",
    },
    "7000218": {
        "message": "Your organisation does not allow signing in with a password here.",
        "action": "Use Connect and finish with the code on the Microsoft page.",
    },
    # --- licensing / permission -------------------------------------------
    "65001": {
        "message": "You have not yet allowed this app to read your data.",
        "action": "Connect again and accept the permission prompt Microsoft shows.",
    },
    "700016": {
        "message": "This app is not enabled in your Microsoft directory.",
        "action": "Ask whoever manages Microsoft 365 for your organisation to "
                  "approve it.",
    },
    "900023": {
        "message": "Microsoft did not recognise the organisation for that account.",
        "action": "Check you used your work email address.",
    },
    # --- token lifetime ----------------------------------------------------
    "70043": {
        "message": "Your Microsoft sign-in has expired.",
        "action": "Reconnect your Microsoft account.",
    },
    "50173": {
        "message": "Your Microsoft sign-in is no longer valid — your password "
                   "changed, or an administrator signed you out everywhere.",
        "action": "Reconnect your Microsoft account.",
    },
    "700082": {
        "message": "Your Microsoft sign-in expired because it went unused too long.",
        "action": "Reconnect your Microsoft account.",
    },
}

# Not an AADSTS code, but the single most common Power BI refusal and the one
# most likely to be read as "the product is broken".
_NOT_LICENSED = {
    "message": "Your account is not licensed for Power BI.",
    "action": "Power BI datasets need a Pro or Premium licence on your own "
              "account. Ask whoever manages Microsoft 365 for your organisation. "
              "Microsoft Fabric lakehouses do not need that licence.",
}

_FALLBACK = {
    "message": "Microsoft would not accept that sign-in.",
    "action": "Try again. If it keeps happening, send this code to whoever "
              "manages Microsoft 365 for your organisation.",
}


def extract_code(raw: Optional[str]) -> Optional[str]:
    """The AADSTS number in a Microsoft error blob, or None."""
    if not raw:
        return None
    m = _CODE_RE.search(str(raw))
    return m.group(1) if m else None


def humanize(raw: Optional[str]) -> Dict[str, Optional[str]]:
    """Map a raw Microsoft error onto ``{message, action, code}``.

    Never raises and never returns an empty message — a member who cannot see
    what went wrong is worse off than one reading a code they do not understand.
    """
    text = str(raw or "")
    code = extract_code(text)

    if code and code in _MESSAGES:
        entry = _MESSAGES[code]
        return {"message": entry["message"], "action": entry["action"], "code": f"AADSTS{code}"}

    lowered = text.lower()
    # Parenthesised deliberately — `A or B and C` reads as `A or (B and C)`, and
    # relying on that precedence in a condition somebody will later edit is how
    # a licence error starts being reported as something else.
    if ("not licensed" in lowered) or ("powerbi" in lowered and "license" in lowered):
        return {**_NOT_LICENSED, "code": f"AADSTS{code}" if code else None}

    # A transport failure has no code at all, and telling somebody to ask their
    # Microsoft administrator about a dropped connection wastes both their time.
    if not code and any(
        t in lowered for t in ("connection error", "timed out", "timeout",
                               "name or service not known", "connection refused")
    ):
        return {
            "message": "We could not reach Microsoft.",
            "action": "Check your connection and try again.",
            "code": None,
        }

    return {**_FALLBACK, "code": f"AADSTS{code}" if code else None}


def humanize_sentence(raw: Optional[str]) -> str:
    """One string, for places that can only carry a single line (an HTTP detail).

    ``message`` then ``action``, and the code only when there is one — appending
    a bare "None" to an otherwise clear sentence helps nobody.
    """
    h = humanize(raw)
    parts = [h["message"], h["action"]]
    if h.get("code"):
        parts.append(f"({h['code']})")
    return " ".join(p for p in parts if p)
