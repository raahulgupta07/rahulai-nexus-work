"""What an artifact can actually be exported as — one source of truth.

Export availability used to be stated twice: once in each route (as a mode
check that raises 400) and again in the frontend toolbar (as a hardcoded
``mode === 'page'`` style condition). Two statements of the same rule drift,
and when they drift the user is shown a button whose only possible outcome is
an error — a designed limitation reading as a broken feature.

This module states the rule once. Routes ask :func:`assert_export_supported`
before doing any work; the UI asks ``GET /artifacts/{id}/exports`` and renders
only what comes back. A format the UI offers is therefore, by construction, a
format the route accepts.

Availability is a function of the artifact alone — its mode and whether the
content that format needs is actually present. Nothing here knows about any
dataset, connector, table or tenant, and nothing should: an artifact built
from any source of any shape gets the same answer.
"""

from typing import Any, Dict, List, Optional

# Every export the product offers, and the modes it applies to. Adding a
# format here is the whole change: the route gate and the UI both follow.
EXPORT_FORMATS: Dict[str, Dict[str, Any]] = {
    "pdf": {
        "label": "PDF",
        # Decks are here because a .pptx cannot embed its fonts (python-pptx has
        # no mechanism for it), so PDF is the only format that delivers a deck
        # looking the way it was designed. See app.services.deck_pdf_service.
        "modes": ("doc", "page", "slides"),
        "path": "export/pdf",
        "media_type": "application/pdf",
    },
    "docx": {
        "label": "Word",
        "modes": ("doc",),
        "path": "export/docx",
        "media_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    },
    "pptx": {
        "label": "PowerPoint",
        "modes": ("slides",),
        "path": "export/pptx",
        "media_type": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    },
}

# Reason strings are the message the route returns and the tooltip the UI can
# show. Kept identical so a refused download and a disabled button explain the
# same thing.
_WRONG_MODE = "{label} export is not available for this artifact type."
_NOT_READY = "This artifact has no content to export as {label} yet."
_FAILED = "Generation failed — regenerate before exporting."
# A deck's PDF is converted from the saved .pptx, so a deck that never produced
# one has nothing to convert. The legacy slides/code sources can still be built
# into a .pptx on request, but there is no file for LibreOffice to read, and
# saying "no content" would send the user looking in the wrong place.
_DECK_NO_FILE = (
    "This deck has no PowerPoint file to convert yet — regenerate the deck, "
    "then export it as PDF."
)


def _content(artifact: Any) -> Dict[str, Any]:
    c = getattr(artifact, "content", None)
    return c if isinstance(c, dict) else {}


def _has_text(value: Any) -> bool:
    return bool(isinstance(value, str) and value.strip())


def _readiness(artifact: Any, fmt: str) -> Optional[str]:
    """None when the format can be produced, else why it cannot.

    Mirrors each route's own content checks. A route that can still fail on a
    condition only discoverable by doing the work (e.g. slide markup that
    parses to zero slides) is not modelled here — this answers "is there
    anything to export", not "will the renderer succeed".
    """
    mode = getattr(artifact, "mode", None)
    spec = EXPORT_FORMATS.get(fmt)
    if spec is None:
        return "Unknown export format."
    label = spec["label"]

    if mode not in spec["modes"]:
        return _WRONG_MODE.format(label=label)

    content = _content(artifact)

    if fmt == "pptx":
        if getattr(artifact, "status", None) == "failed":
            return _FAILED
        has_source = (
            _has_text(getattr(artifact, "pptx_path", None))
            or bool(content.get("slides"))
            or _has_text(content.get("code"))
        )
        return None if has_source else _NOT_READY.format(label=label)

    if fmt == "docx":
        return None if _has_text(content.get("markdown")) else _NOT_READY.format(label=label)

    # pdf: dashboards render their code, documents render their markdown, and
    # decks are converted from the .pptx that was saved when they were built.
    if mode == "slides":
        if getattr(artifact, "status", None) == "failed":
            return _FAILED
        return None if _has_text(getattr(artifact, "pptx_path", None)) else _DECK_NO_FILE
    if mode == "page":
        return None if _has_text(content.get("code")) else _NOT_READY.format(label=label)
    return None if _has_text(content.get("markdown")) else _NOT_READY.format(label=label)


def export_unavailable_reason(artifact: Any, fmt: str) -> Optional[str]:
    """Why ``fmt`` cannot be produced for this artifact, or None if it can."""
    return _readiness(artifact, fmt)


def is_export_supported(artifact: Any, fmt: str) -> bool:
    return _readiness(artifact, fmt) is None


def supported_exports(artifact: Any) -> List[Dict[str, Any]]:
    """The exports this artifact can actually produce, in a stable order.

    Only available formats are returned. A caller rendering this list cannot
    put a dead control in front of a user.
    """
    artifact_id = str(getattr(artifact, "id", "") or "")
    out: List[Dict[str, Any]] = []
    for fmt, spec in EXPORT_FORMATS.items():
        if _readiness(artifact, fmt) is not None:
            continue
        out.append(
            {
                "format": fmt,
                "label": spec["label"],
                "media_type": spec["media_type"],
                "url": f"/artifacts/{artifact_id}/{spec['path']}",
            }
        )
    return out


def assert_export_supported(artifact: Any, fmt: str) -> None:
    """Raise the route's 400 when ``fmt`` is not available for this artifact."""
    reason = _readiness(artifact, fmt)
    if reason is None:
        return
    from fastapi import HTTPException

    raise HTTPException(status_code=400, detail=reason)
