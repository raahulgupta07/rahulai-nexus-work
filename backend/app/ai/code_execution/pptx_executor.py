"""
Secure executor for python-pptx code generation.

Allows LLM-generated python-pptx code to run in a sandboxed environment
with security validation reused from code_execution.py.
"""

import io
import ast
import tempfile
import subprocess
from pathlib import Path
from contextlib import redirect_stdout
from typing import Dict, Any, List, Tuple, Optional

# python-pptx imports
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.chart.data import CategoryChartData, ChartData

from app.ai.code_execution.code_execution import (
    CodeSecurityError,
    UnsafePythonError,
    FORBIDDEN_MODULES,
    FORBIDDEN_BUILTINS,
    FORBIDDEN_ATTRIBUTES,
)


# =============================================================================
# PPTX-Specific Security Validation
# =============================================================================

# Modules allowed for PPTX generation (extend the forbidden list with exceptions)
PPTX_ALLOWED_MODULES = frozenset({
    'pptx',
})


class PptxSecurityVisitor(ast.NodeVisitor):
    """AST visitor that checks for dangerous code patterns, allowing pptx imports."""

    def __init__(self):
        self.errors: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_name = alias.name.split('.')[0]
            # Allow pptx imports, block everything else that's forbidden
            if module_name not in PPTX_ALLOWED_MODULES and module_name in FORBIDDEN_MODULES:
                self.errors.append(f"Forbidden import: '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module_name = node.module.split('.')[0]
            # Allow pptx imports, block everything else that's forbidden
            if module_name not in PPTX_ALLOWED_MODULES and module_name in FORBIDDEN_MODULES:
                self.errors.append(f"Forbidden import: 'from {node.module}'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check for forbidden built-in calls like eval(), exec(), open()
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_BUILTINS:
                self.errors.append(f"Forbidden function call: '{node.func.id}()'")

        # Check for __import__('os') style calls
        if isinstance(node.func, ast.Name) and node.func.id == '__import__':
            self.errors.append("Forbidden function call: '__import__()'")

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Check for direct access to forbidden attributes like obj.__class__
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.errors.append(f"Forbidden attribute access: '{node.attr}'")
        self.generic_visit(node)


import re as _re

# python-pptx's Chart object exposes no `.plot_area` or `.chart_area` attribute.
# LLM-generated deck code frequently hallucinates them (e.g.
# `chart.plot_area.format.fill.solid()`), which raises AttributeError at exec
# time and fails the whole slide-generation run. Neutralize those statement
# lines so the rest of the deck still builds.
_INVALID_CHART_ATTR = _re.compile(r'^(\s*)\S.*\.(?:plot_area|chart_area)\b')


def sanitize_pptx_code(code: str) -> str:
    """Replace statements touching non-existent python-pptx chart attributes
    (`.plot_area` / `.chart_area`) with a no-op so generation doesn't crash."""
    out = []
    for line in code.splitlines():
        m = _INVALID_CHART_ATTR.match(line)
        if m:
            out.append(f"{m.group(1)}pass  # neutralized: python-pptx Chart has no .plot_area/.chart_area")
        else:
            out.append(line)
    return "\n".join(out)


def normalize_chart_axes(pptx_path: Path, logger=None) -> int:
    """Give every chart in a deck the same axis numbers the browser shows.

    Generated deck code hands python-pptx raw values and python-pptx writes
    them out in full, so a chart that reads `4.3B` on screen printed
    `70000000000` in the exported file. This walks the saved deck, reads each
    chart's own plotted values, and applies the shared abbreviation from
    `app.services.number_format` to its value axis — the same rule, from the
    same definition, as the browser and the Word export.

    Category labels are also made unambiguous: two categories that render the
    same string are two different things presented as one.

    Returns the number of charts changed. Never raises — a deck that could not
    be post-processed is still a valid deck.
    """
    from app.services.number_format import (
        pptx_axis_number_format,
        qualify_duplicate_labels,
    )

    changed = 0
    try:
        prs = Presentation(str(pptx_path))
    except Exception:
        if logger:
            logger.warning("pptx axis normalize: could not reopen %s", pptx_path)
        return 0

    dirty = False
    for slide in prs.slides:
        for shape in slide.shapes:
            if not getattr(shape, "has_chart", False):
                continue
            chart = shape.chart
            touched = False

            # Value axis: abbreviate to the magnitude of the largest plotted
            # point, so the axis reads the same as it does on screen.
            magnitudes = []
            try:
                for plot in chart.plots:
                    for series in plot.series:
                        for v in series.values:
                            if isinstance(v, (int, float)):
                                magnitudes.append(abs(v))
            except Exception:
                magnitudes = []
            if magnitudes:
                try:
                    axis = chart.value_axis
                    axis.tick_labels.number_format = pptx_axis_number_format(max(magnitudes))
                    axis.tick_labels.number_format_is_linked = False
                    touched = True
                except (ValueError, NotImplementedError, AttributeError):
                    # Pie/doughnut and some plot types have no value axis.
                    pass

            # Category axis: qualify labels that repeat.
            try:
                for plot in chart.plots:
                    labels = list(plot.categories)
                    flat = [
                        "" if l is None else str(getattr(l, "label", l))
                        for l in labels
                    ]
                    if len(set(flat)) != len(flat):
                        qualified = qualify_duplicate_labels(flat)
                        _rewrite_category_labels(plot, qualified)
                        touched = True
            except Exception:
                pass

            if touched:
                changed += 1
                dirty = True

    if dirty:
        try:
            prs.save(str(pptx_path))
        except Exception:
            if logger:
                logger.warning("pptx axis normalize: could not save %s", pptx_path)
            return 0
    return changed


def _rewrite_category_labels(plot, labels: List[str]) -> None:
    """Replace a plot's category strings in the underlying chart XML.

    python-pptx exposes categories read-only, so the cached string points are
    edited directly. Guarded by the caller.
    """
    from pptx.oxml.ns import qn

    for ser in plot._element.iter(qn("c:ser")):
        # Scope to <c:cat> only. A series also carries a <c:strCache> for its
        # own NAME under <c:tx>, so an unscoped search returns one point too
        # many, the length check fails and the rewrite is skipped in silence —
        # which is what happened, and it took looking at a rendered slide to
        # notice.
        cat = ser.find(qn("c:cat"))
        if cat is None:
            continue
        pts = cat.findall(f'.//{qn("c:strCache")}/{qn("c:pt")}')
        if len(pts) != len(labels):
            continue
        for pt, text in zip(pts, labels):
            v = pt.find(qn("c:v"))
            if v is not None:
                v.text = text


def validate_pptx_code(code: str) -> None:
    """
    Validate Python code for security issues, allowing pptx imports.

    Raises:
        UnsafePythonError: If the code contains dangerous constructs.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Let syntax errors pass through - they'll fail at exec() time
        return

    visitor = PptxSecurityVisitor()
    visitor.visit(tree)

    if visitor.errors:
        raise UnsafePythonError(
            f"Code contains forbidden constructs: {'; '.join(visitor.errors)}"
        )


# =============================================================================
# PPTX Code Executor
# =============================================================================

def _make_image_opener(images: Dict[str, bytes]):
    """Build the `image(file_id)` helper handed to generated code.

    Returns a fresh BytesIO per call: python-pptx reads the stream it is given
    to exhaustion, so handing out one shared buffer would make the second
    placement of the same image silently render empty.
    """

    def image(file_id: str) -> io.BytesIO:
        raw = images.get(str(file_id))
        if raw is None:
            raise ValueError(
                f"Unknown image id {file_id!r}. Available: {sorted(images) or 'none'}"
            )
        return io.BytesIO(raw)

    return image


class PptxCodeExecutor:
    """
    Secure executor for python-pptx code generation.

    Reuses security patterns from StreamingCodeExecutor but with a namespace
    tailored for PPTX generation.
    """

    def __init__(self, logger=None):
        self.logger = logger

    def execute_pptx_code(
        self,
        *,
        code: str,
        visualizations: List[Dict[str, Any]],
        report: Dict[str, Any],
        output_path: Path,
        images: Optional[Dict[str, bytes]] = None,
    ) -> Tuple[Path, str]:
        """
        Execute python-pptx code and save the resulting presentation.

        Args:
            code: The python-pptx code to execute
            visualizations: List of visualization dicts with rows/columns
            report: Report info dict with id, title, theme
            output_path: Path where the PPTX file should be saved
            images: Raw bytes per embeddable file id. Resolved by the caller —
                generated code never touches the filesystem, so the sandbox
                keeps its no-IO guarantee while still being able to place art.

        Returns:
            Tuple of (output_path, stdout_log)

        Raises:
            UnsafePythonError: If code contains forbidden imports, calls, or attributes
        """
        # Neutralize hallucinated python-pptx chart APIs (.plot_area/.chart_area)
        # that would otherwise crash the whole run with AttributeError.
        code = sanitize_pptx_code(code)

        # Security: Validate code before execution
        validate_pptx_code(code)

        output_log = ""

        # Build the namespace with pptx utilities and data
        local_namespace = {
            # python-pptx classes
            'Presentation': Presentation,
            'Inches': Inches,
            'Pt': Pt,
            'Emu': Emu,
            'RGBColor': RGBColor,
            'PP_ALIGN': PP_ALIGN,
            'MSO_ANCHOR': MSO_ANCHOR,
            'MSO_SHAPE': MSO_SHAPE,
            'XL_CHART_TYPE': XL_CHART_TYPE,
            'XL_LEGEND_POSITION': XL_LEGEND_POSITION,
            'CategoryChartData': CategoryChartData,
            'ChartData': ChartData,

            # Data access
            'visualizations': visualizations,
            'report': report,

            # Embeddable art. `image(file_id)` hands back a FRESH stream each
            # call — python-pptx consumes the stream it is given, so a shared
            # BytesIO would silently produce an empty picture the second time
            # the same image is placed.
            'image': _make_image_opener(images or {}),
            'image_ids': list((images or {}).keys()),

            # Output target (set by executor, not user code)
            '_pptx_output_path': str(output_path),
        }

        if self.logger:
            self.logger.debug(f"Executing PPTX code:\n{code}")

        # Capture via the per-thread stdout router (not redirect_stdout,
        # which swaps the process-global sys.stdout and cross-talks with
        # any concurrently-running sandboxed code execution).
        from app.ai.code_execution.code_execution import _stdout_router
        router = _stdout_router()
        stdout_capture = io.StringIO()
        router.bind(stdout_capture)
        try:
            exec(code, local_namespace)
            output_log = stdout_capture.getvalue()
        finally:
            router.unbind()
            stdout_capture.close()

        # Verify the file was created
        if not output_path.exists():
            raise RuntimeError(
                f"PPTX code executed but no file was created at {output_path}. "
                "Ensure the code calls prs.save(_pptx_output_path)"
            )

        # Axis numbers and category labels are a property of the product, not
        # of whatever the generating code happened to write. Applied after the
        # save so it covers every chart in the deck regardless of how it was
        # built. Never fatal — see normalize_chart_axes.
        try:
            normalize_chart_axes(output_path, logger=self.logger)
        except Exception:
            if self.logger:
                self.logger.warning("pptx axis normalize skipped", exc_info=True)

        return output_path, output_log


# =============================================================================
# PPTX to Image Preview Conversion
# =============================================================================

class PptxPreviewService:
    """
    Service for generating preview images from PPTX files.

    Uses LibreOffice (headless) to convert PPTX to PDF,
    then pdf2image to convert PDF pages to PNG images.
    """

    def __init__(self, preview_dir: Optional[Path] = None, logger=None):
        self.logger = logger
        if preview_dir:
            self.preview_dir = preview_dir
        else:
            # Default to uploads/pptx_previews relative to backend root
            backend_root = Path(__file__).parent.parent.parent.parent
            self.preview_dir = backend_root / "uploads" / "pptx_previews"

        # Ensure preview directory exists
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    def generate_previews(
        self,
        pptx_path: Path,
        artifact_id: str,
        dpi: int = 150,
    ) -> List[str]:
        """
        Convert PPTX to PNG preview images.

        Args:
            pptx_path: Path to the PPTX file
            artifact_id: Artifact ID for organizing previews
            dpi: Resolution for preview images (default 150)

        Returns:
            List of relative paths to preview images (e.g., ["pptx_previews/{id}/slide-1.png", ...])
        """
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError

        # Create artifact-specific preview directory
        artifact_preview_dir = self.preview_dir / artifact_id
        artifact_preview_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Convert PPTX to PDF using LibreOffice
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # LibreOffice convert to PDF
            try:
                result = subprocess.run(
                    [
                        'soffice',
                        '--headless',
                        '--convert-to', 'pdf',
                        '--outdir', str(tmp_path),
                        str(pptx_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")
            except FileNotFoundError:
                raise RuntimeError(
                    "LibreOffice not found. Install with: apt-get install libreoffice-impress"
                )

            # Find the generated PDF
            pdf_files = list(tmp_path.glob("*.pdf"))
            if not pdf_files:
                raise RuntimeError("LibreOffice did not produce a PDF file")
            pdf_path = pdf_files[0]

            # Step 2: Convert PDF pages to PNG using pdf2image
            try:
                images = convert_from_path(pdf_path, dpi=dpi)
            except PDFInfoNotInstalledError:
                raise RuntimeError(
                    "poppler not found. Install with: apt-get install poppler-utils"
                )
            except PDFPageCountError as e:
                raise RuntimeError(f"Failed to read PDF: {e}")

            # Save images
            for i, image in enumerate(images):
                image_path = artifact_preview_dir / f"slide-{i + 1:02d}.png"
                image.save(str(image_path), "PNG")

        # Collect generated image paths
        preview_images = sorted(artifact_preview_dir.glob("slide-*.png"))

        # Return relative paths from uploads directory
        relative_paths = [
            f"pptx_previews/{artifact_id}/{img.name}" for img in preview_images
        ]

        if self.logger:
            self.logger.info(f"Generated {len(relative_paths)} preview images for artifact {artifact_id}")

        return relative_paths

    def get_preview_paths(self, artifact_id: str) -> List[str]:
        """Get existing preview image paths for an artifact."""
        artifact_preview_dir = self.preview_dir / artifact_id
        if not artifact_preview_dir.exists():
            return []

        preview_images = sorted(artifact_preview_dir.glob("slide-*.png"))
        return [
            f"pptx_previews/{artifact_id}/{img.name}" for img in preview_images
        ]

    def cleanup_previews(self, artifact_id: str) -> None:
        """Remove all preview images for an artifact."""
        artifact_preview_dir = self.preview_dir / artifact_id
        if artifact_preview_dir.exists():
            import shutil
            shutil.rmtree(artifact_preview_dir)
