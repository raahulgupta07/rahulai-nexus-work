"""Read a `.vue`/`.ts` file as text with its COMMENTS REMOVED.

★Every guard in this directory that scans a `.vue` file must strip comments
first. A fix's own comment explains the defect and therefore *quotes it* — the
`uppercaseTitle` fix says "so transforming it here rewrote the row for
everyone", the reference fix says "`relation_type` hardcoded to 'scope'". A
scanner that reads comments finds the defect it was written to prove absent and
fails citing its own explanation. This repo has been bitten by exactly that: a
`#`-stripper that still read the docstring, and a string-literal stripper that
removed the very identifier being searched for.

The line-comment rule is deliberately conservative — only a `//` that begins a
line (after whitespace) is treated as a comment. A trailing `//` cannot be told
from a URL or a regex without parsing, and every comment these guards must not
see is a full-line one.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)


def strip_comments(text: str) -> str:
    text = _HTML_COMMENT.sub("", text)
    text = _BLOCK_COMMENT.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    return text


def read_source(relative_path: str) -> str:
    """Frontend file at `relative_path` (relative to `frontend/`), decommented."""
    return strip_comments((FRONTEND / relative_path).read_text(encoding="utf-8"))
