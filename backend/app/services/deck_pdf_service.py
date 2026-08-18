"""Render a generated deck (.pptx) as a PDF.

A .pptx does not carry its fonts. python-pptx has no mechanism for embedding
them, so a deck opened on a machine without the design system's typefaces is
re-set in whatever the viewer happens to have — the deck's identity is the
first thing lost. PDF is the only delivery format that embeds the faces it
draws with, which makes it the fidelity escape hatch for every deck we ship.

The conversion itself is not new capability. ``PptxPreviewService`` in
``app.ai.code_execution.pptx_executor`` already shells LibreOffice headless to
turn the saved .pptx into a PDF, and then throws that PDF away after
rasterising it into slide thumbnails. This module keeps the PDF instead. The
soffice invocation below is deliberately the same one — same flags, same
timeout posture, same "LibreOffice is not installed" handling — so a deck that
previews correctly converts correctly.

Errors are typed. The route needs to tell a missing renderer (an operator
problem, nothing the user did) apart from a conversion that ran and failed, and
both apart from a deck that never had a .pptx to begin with.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

# Same ceiling generate_previews uses, doubled: preview conversion runs on a
# freshly written deck, an export can be asked for a much older and larger one,
# and a download that takes a minute is better than one that is refused.
CONVERSION_TIMEOUT_SECONDS = 120


class DeckPdfError(RuntimeError):
    """Deck -> PDF conversion could not be completed.

    ``message`` is written to be shown to a user as-is: it says what went
    wrong in terms of the deck, not in terms of LibreOffice's exit code.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class DeckPdfRendererUnavailable(DeckPdfError):
    """The converter is not installed on this host.

    Distinct from a failed conversion because nothing about the deck is wrong
    and no amount of regenerating it will help — the fix is on the server.
    """


def render_deck_pdf(
    pptx_path: Union[str, Path],
    *,
    timeout: int = CONVERSION_TIMEOUT_SECONDS,
) -> bytes:
    """Convert a saved .pptx into PDF bytes.

    Blocking (it waits on a subprocess); call it from a worker thread, e.g.
    ``await asyncio.to_thread(render_deck_pdf, path)``.

    Raises :class:`DeckPdfRendererUnavailable` when LibreOffice is missing and
    :class:`DeckPdfError` when the file is absent or the conversion fails.
    """
    source = Path(pptx_path)
    if not source.is_file():
        raise DeckPdfError(
            "This deck's PowerPoint file is missing — regenerate the deck, then export it again.",
            detail=f"not a file: {source}",
        )

    # A temp dir per call: LibreOffice names its output after the input, so two
    # concurrent exports of decks with the same filename would otherwise race
    # for one path. Removed on the way out whether or not conversion succeeded.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        try:
            result = subprocess.run(
                [
                    'soffice',
                    '--headless',
                    '--convert-to', 'pdf',
                    '--outdir', str(tmp_path),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            # Same condition generate_previews reports, same remedy.
            raise DeckPdfRendererUnavailable(
                "PDF export is unavailable on this server because the document "
                "converter is not installed. The deck can still be downloaded "
                "as PowerPoint.",
                detail="LibreOffice not found. Install with: apt-get install libreoffice-impress",
            )
        except subprocess.TimeoutExpired:
            raise DeckPdfError(
                "This deck took too long to convert to PDF. Try again, or "
                "download it as PowerPoint.",
                detail=f"soffice exceeded {timeout}s",
            )

        if result.returncode != 0:
            raise DeckPdfError(
                "This deck could not be converted to PDF. Try again, or "
                "download it as PowerPoint.",
                detail=f"LibreOffice conversion failed: {result.stderr}",
            )

        pdf_files = list(tmp_path.glob("*.pdf"))
        if not pdf_files:
            # soffice can exit 0 having written nothing at all; treat a missing
            # output as a failure rather than returning empty bytes.
            raise DeckPdfError(
                "This deck could not be converted to PDF. Try again, or "
                "download it as PowerPoint.",
                detail="LibreOffice did not produce a PDF file",
            )

        pdf_bytes = pdf_files[0].read_bytes()

    if not pdf_bytes:
        raise DeckPdfError(
            "This deck could not be converted to PDF. Try again, or "
            "download it as PowerPoint.",
            detail="LibreOffice produced an empty PDF",
        )

    return pdf_bytes
