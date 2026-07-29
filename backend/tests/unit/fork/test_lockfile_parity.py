"""Every dependency declared in pyproject.toml must exist in uv.lock.

★ The build installs with ``uv sync --frozen``. ``--frozen`` does not verify the
lock against the manifest — it installs exactly what the lock says and asks no
questions. So a dependency can be declared, reviewed, merged, and shipped while
being entirely absent from the image, with no error at build time and no error
at start-up. The failure only surfaces when someone uses the feature.

That is exactly what happened to ``python-docx``: declared in pyproject, missing
from the lock, therefore never installed. Word export returned a 500 on every
installation since it shipped, and the file classifier silently fell back to
treating uploaded .docx files as unknown.

This is the fifth instance in this codebase of the same shape — a claim that
nothing enforces (see also the permission registry vs the seeded role, the
.gitignore rule over already-tracked files, upgrade.sh's rollback promise, and
the auto-publish gate reading contained rather than changed content). The fix
for that shape is always the same: make something fail when the two disagree.

Reads the files, not the environment, so it stays in the fast fork suite.
"""
import re
import tomllib
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[3]
_PYPROJECT = _BACKEND / "pyproject.toml"
_LOCK = _BACKEND / "uv.lock"

# PEP 508: a requirement string starts with the distribution name, which ends at
# the first of a version specifier, an extras bracket, a marker, or whitespace.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """PEP 503 normalisation — uv writes `python-docx`, a manifest may say
    `python_docx`, and both mean the same distribution."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependencies() -> set:
    data = tomllib.loads(_PYPROJECT.read_text())
    project = data.get("project", {})

    reqs = list(project.get("dependencies", []))
    # Optional extras count too: the Dockerfile installs `--extra kerberos`, so a
    # missing entry there fails in production exactly like a missing core one.
    for extra_reqs in (project.get("optional-dependencies") or {}).values():
        reqs.extend(extra_reqs)

    out = set()
    for req in reqs:
        m = _NAME_RE.match(req)
        if m:
            out.add(_normalize(m.group(1)))
    return out


def _locked_packages() -> set:
    """Every `name = "..."` that opens a [[package]] block in uv.lock."""
    locked, in_package = set(), False
    for line in _LOCK.read_text().splitlines():
        stripped = line.strip()
        if stripped == "[[package]]":
            in_package = True
            continue
        if in_package and stripped.startswith("name = "):
            locked.add(_normalize(stripped.split("=", 1)[1].strip().strip('"')))
            in_package = False
    return locked


def test_lock_covers_every_declared_dependency():
    missing = sorted(_declared_dependencies() - _locked_packages())
    assert not missing, (
        "declared in pyproject.toml but absent from uv.lock, so `uv sync "
        f"--frozen` will not install it: {missing}. Run `uv lock` and rebuild. "
        "This does NOT fail the build — it fails at runtime, in whichever "
        "feature imports the package."
    )


def test_the_lock_parser_actually_found_packages():
    """Guards the guard: a uv.lock format change that broke the parser would
    turn the test above into a silent pass, which is the very failure mode this
    file exists to prevent."""
    locked = _locked_packages()
    assert len(locked) > 100, (
        f"only parsed {len(locked)} packages out of uv.lock — the parser is "
        "probably broken, and the parity check above is now vacuous"
    )


@pytest.mark.parametrize("package,floor", [("pdfminer-six", 20251107)])
def test_security_floors_are_not_lowered(package, floor):
    """★ pdfminer-six below 20251107 carries CVE-2025-64512 and CVE-2025-70559,
    both HIGH ("Deserialization of Untrusted Data"), reachable from
    DocAgent.get_content() — i.e. from any PDF a user uploads."""
    data = tomllib.loads(_PYPROJECT.read_text())
    reqs = [r for r in data["project"]["dependencies"]
            if _normalize(_NAME_RE.match(r).group(1)) == package]
    assert reqs, f"{package} is no longer declared — was it removed deliberately?"

    lower = re.search(r">=\s*(\d+)", reqs[0])
    assert lower and int(lower.group(1)) >= floor, (
        f"{package} lower bound is below the security floor {floor}: {reqs[0]}"
    )
