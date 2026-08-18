"""Every font a shipped deck theme names must exist inside the image.

`backend/app/ai/decks/pptx_themes.py` ships 81 themes and each one names the
typefaces its slides are laid out in — 55 families in all. Nothing in the
pipeline checks that any of them are installed, and nothing ever will at
runtime: a missing font is not an error anywhere in this stack. fontconfig
answers every request with its closest guess, LibreOffice lays the slide out in
the substitute, and the deck renders looking merely *wrong*.

That is not hypothetical. The runtime stage installed LibreOffice with
`--no-install-recommends`, which is precisely the flag that skips the font
packages (LibreOffice *recommends* them and depends on almost none), so the
image carried Liberation, DejaVu, FreeSans, Noto, WenQuanYi and IPA and nothing
else. Decks ask for Cambria and Calibri. `fc-match Cambria` answered **DejaVu
Serif**, which is about 30% wider, and a title needing 724pt in Times metrics
needs 939pt in DejaVu Serif Bold against an 835pt box — so titles wrapped and
overprinted, on every deck, with no log line anywhere.

The fix has two halves and this file guards both:

  * the **metric-compatible core** from apt — croscore (Arimo/Tinos/Cousine =
    Arial/Times/Courier metrics), carlito (= Calibri), caladea (= Cambria).
    These are what kill the live substitution bug, whatever else is missing.
  * the **theme families**, from a Debian package where one exists and from
    `assets/fonts/` where none does.

★It guards the Dockerfile and the vendored bytes, not a running container —
this suite has no Docker. So it is written to fail on the things that actually
went wrong: a family nobody installed, a `COPY` that never happened, a missing
`fc-cache` (files under /usr/share/fonts are invisible until the cache is
rebuilt), and a vendored file that is not really a font.

★The vendored files are STATIC instances, never the variable fonts Google
Fonts now ships upstream. fontconfig reports a `.ttf` carrying an `fvar` table
at its **default instance only**, so a variable-only install hands LibreOffice
one weight and it synthesises the bold — reintroducing the exact metric drift
this change exists to remove. `test_no_vendored_font_is_a_variable_font` is
what keeps a future refresh from quietly undoing that.

★Families are read out of `pptx_themes.py`, never listed here. A hand-copied
list goes stale the day a theme is added and then guards the past.
"""
from __future__ import annotations

import ast
import re
import struct
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"
FONT_DIR = REPO / "assets" / "fonts"
THEMES = REPO / "backend" / "app" / "ai" / "decks" / "pptx_themes.py"

#: The three that answer Arial / Times / Courier / Calibri / Cambria with the
#: same advance widths. Nothing in the theme list names them; they matter
#: because the *generated python-pptx code* does, and they are the whole reason
#: the live decks overprinted.
METRIC_COMPATIBLE_CORE = (
    "fonts-croscore",
    "fonts-crosextra-carlito",
    "fonts-crosextra-caladea",
)

#: theme family -> the Ubuntu/Debian package that provides it. Verified against
#: the noble package list; every one is in main or universe. ★IBM Plex is
#: deliberately absent — `fonts-ibm-plex` is in **multiverse**, so an image
#: whose sources omit that component would fail the whole `apt-get install`.
#: It is vendored instead.
APT_FAMILIES = {
    "Arimo": "fonts-croscore",
    "Courier Prime": "fonts-courier-prime",
    "DM Mono": "fonts-dm-mono",
    "EB Garamond": "fonts-ebgaramond",
    "Inter": "fonts-inter",
    "JetBrains Mono": "fonts-jetbrains-mono",
    "Karla": "fonts-karla",
    "Open Sans": "fonts-open-sans",
    "Quicksand": "fonts-quicksand",
    "Sora": "fonts-sora",
}


def theme_font_families() -> set[str]:
    """Every family named by any shipped deck theme.

    Parsed, not imported: the fork suite must not depend on the app package
    being importable, and `_META` is a plain literal.
    """
    src = THEMES.read_text(encoding="utf-8")
    marker = re.search(r"^_META: dict\[str, tuple\] = \{", src, re.M)
    assert marker, "pptx_themes.py no longer declares _META — re-point this guard"
    start = src.index("{", marker.end() - 1)
    depth = 0
    for end in range(start, len(src)):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
            if depth == 0:
                break
    meta = ast.literal_eval(src[start : end + 1])
    return {family for entry in meta.values() for family in entry[2]}


def apt_installed_packages() -> set[str]:
    """Package names any `apt-get install` line in the Dockerfile asks for."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    # Join backslash continuations so a multi-line install reads as one line.
    joined = re.sub(r"\\\n\s*", " ", text)
    found: set[str] = set()
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "apt-get install" not in stripped:
            continue
        tail = stripped.split("apt-get install", 1)[1]
        tail = tail.split("&&")[0].split("||")[0].split(";")[0]
        for token in tail.split():
            if token.startswith("-") or token.startswith("$") or "=" in token:
                continue
            found.add(token)
    return found


def vendored_family_dirs() -> dict[str, Path]:
    """Directory name (spaces stripped) -> path, for each vendored family."""
    if not FONT_DIR.is_dir():
        return {}
    return {d.name: d for d in sorted(FONT_DIR.iterdir()) if d.is_dir()}


def _sfnt_name_records(path: Path) -> dict[int, str]:
    """nameID -> string, read straight out of the sfnt `name` table.

    Hand-rolled so the guard needs no font library: the thing being asserted is
    that these bytes ARE a font, and a parser that comes from the same ecosystem
    that produced them is a weaker witness than one that does not.
    """
    blob = path.read_bytes()
    if blob[:4] not in (b"\x00\x01\x00\x00", b"true", b"ttcf", b"OTTO"):
        raise ValueError(f"{path.name} is not an sfnt font")
    num_tables = struct.unpack(">H", blob[4:6])[0]
    table = None
    for i in range(num_tables):
        rec = 12 + i * 16
        tag, _checksum, offset, length = struct.unpack(">4sIII", blob[rec : rec + 16])
        if tag == b"name":
            table = (offset, length)
            break
    if table is None:
        raise ValueError(f"{path.name} has no name table")
    base, _length = table
    count, string_offset = struct.unpack(">HH", blob[base + 2 : base + 6])
    out: dict[int, str] = {}
    for i in range(count):
        rec = base + 6 + i * 12
        platform, _enc, _lang, name_id, length, offset = struct.unpack(
            ">HHHHHH", blob[rec : rec + 12]
        )
        start = base + string_offset + offset
        raw = blob[start : start + length]
        try:
            value = raw.decode("utf-16-be") if platform == 3 else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        out.setdefault(name_id, value)
    return out


def _has_table(path: Path, wanted: bytes) -> bool:
    blob = path.read_bytes()
    num_tables = struct.unpack(">H", blob[4:6])[0]
    for i in range(num_tables):
        rec = 12 + i * 16
        tag = blob[rec : rec + 4]
        if tag == wanted:
            return True
    return False


def all_vendored_fonts() -> list[Path]:
    return sorted(FONT_DIR.rglob("*.ttf")) + sorted(FONT_DIR.rglob("*.otf"))


def require_vendored_fonts() -> list[Path]:
    """★Every "no bad font here" assertion below is vacuous on an empty tree.

    Deleting `assets/fonts` would satisfy all of them. This is the positive
    control they each open with, so absence fails loudly instead of passing.
    """
    fonts = all_vendored_fonts()
    assert fonts, f"{FONT_DIR} contains no fonts — this guard would be vacuous"
    return fonts


# --------------------------------------------------------------------------
# The live bug
# --------------------------------------------------------------------------


@pytest.mark.parametrize("package", METRIC_COMPATIBLE_CORE)
def test_the_metric_compatible_core_is_installed(package):
    """Calibri and Cambria must resolve to something the same width.

    This is the half that fixes the reported defect on its own. Without it
    `fc-match Cambria` falls through to DejaVu Serif and every deck title is
    laid out ~30% too wide.
    """
    assert package in apt_installed_packages(), (
        f"{package} is not installed by the Dockerfile. Generated decks name "
        f"Cambria/Calibri and fontconfig will substitute a wider face silently."
    )


def test_libreoffice_is_not_installed_without_fonts():
    """The `--no-install-recommends` on LibreOffice is what dropped the fonts.

    Keeping the flag is right — it is what stops a whole desktop arriving — so
    the guard is not "remove it" but "then name the fonts yourself".
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "libreoffice-impress" in text, "LibreOffice is gone — re-point this guard"
    packages = apt_installed_packages()
    fonts = {p for p in packages if p.startswith("fonts-")}
    assert fonts, (
        "LibreOffice is installed with --no-install-recommends and no font "
        "package is named anywhere. Previews and PDF export will substitute."
    )


# --------------------------------------------------------------------------
# Theme coverage
# --------------------------------------------------------------------------


def test_the_theme_list_is_readable():
    """A guard that cannot read the themes silently guards nothing."""
    families = theme_font_families()
    assert len(families) >= 50, f"only {len(families)} families parsed from _META"


def test_every_theme_family_is_covered():
    """Each family arrives either from apt or from the vendored directory."""
    families = theme_font_families()
    vendored = vendored_family_dirs()
    packages = apt_installed_packages()

    missing = []
    for family in sorted(families):
        package = APT_FAMILIES.get(family)
        if package is not None:
            if package not in packages:
                missing.append(f"{family} (expects apt package {package})")
            continue
        directory = vendored.get(family.replace(" ", ""))
        if directory is None or not list(directory.glob("*.ttf")):
            missing.append(f"{family} (expects {FONT_DIR.name}/{family.replace(' ', '')}/)")

    assert not missing, (
        f"{len(missing)} of {len(families)} theme font families would be "
        f"substituted at render time:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("family,package", sorted(APT_FAMILIES.items()))
def test_each_apt_covered_family_names_its_package(family, package):
    assert package in apt_installed_packages(), (
        f"{family} is claimed to come from {package}, which nothing installs"
    )


# --------------------------------------------------------------------------
# The vendored half actually reaching the image
# --------------------------------------------------------------------------


def test_the_vendored_fonts_are_copied_into_the_image():
    text = DOCKERFILE.read_text(encoding="utf-8")
    copies = [
        line
        for line in text.splitlines()
        if line.startswith("COPY") and "assets/fonts" in line
    ]
    assert copies, "assets/fonts is never COPYed into the image"
    assert any("/usr/share/fonts" in line for line in copies), (
        "the fonts are copied somewhere fontconfig does not scan; the "
        f"destination must live under /usr/share/fonts (got {copies})"
    )


def test_the_font_cache_is_rebuilt():
    """Files under /usr/share/fonts are invisible until `fc-cache` runs.

    LibreOffice reads the cache, not the directory, so a COPY with no
    `fc-cache` looks completely correct and changes nothing at all.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    copy_at = text.find("COPY assets/fonts")
    assert copy_at != -1, "assets/fonts is never COPYed into the image"
    assert re.search(r"fc-cache\b", text[copy_at:]), (
        "no `fc-cache` after the font COPY — the files ship but nothing sees them"
    )


def test_the_font_directory_is_not_excluded_from_the_build_context():
    """★.dockerignore patterns are anchored, not recursive — and cut both ways.

    A pattern that excluded the font directory would make the COPY above fail
    the build, or worse, copy an empty tree.
    """
    ignored = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        pattern = line.rstrip("/")
        if pattern in {"assets", "assets/fonts"} or pattern.endswith("/assets"):
            ignored.append(line)
    assert not ignored, f".dockerignore excludes the font directory: {ignored}"


def test_the_font_licences_are_re_included_in_the_build_context():
    """★`LICENSE*` is in .dockerignore, and the failure it could cause is silent.

    The pattern is anchored today, so it matches only the repo root and the
    per-family `LICENSE.txt` files ship anyway. But this file has already had
    one `**/`-recursion sweep, and a second one would start matching them — the
    build would still succeed and the image would redistribute 45 OFL/Apache
    families with no licence text in it. The explicit re-include is what makes
    that impossible rather than merely unlikely.
    """
    lines = [
        raw.strip() for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        line.startswith("!") and "assets/fonts" in line and "LICENSE" in line
        for line in lines
    ), (
        ".dockerignore does not explicitly re-include assets/fonts/**/LICENSE.txt "
        "while it excludes LICENSE*"
    )


# --------------------------------------------------------------------------
# The vendored bytes being real, correct fonts
# --------------------------------------------------------------------------


def test_there_are_vendored_fonts_at_all():
    assert FONT_DIR.is_dir(), f"{FONT_DIR} does not exist"
    assert all_vendored_fonts(), f"{FONT_DIR} contains no font files"


def test_every_vendored_file_is_really_a_font():
    """A truncated download and a real font look identical in a file listing."""
    bad = []
    for path in require_vendored_fonts():
        try:
            names = _sfnt_name_records(path)
        except Exception as exc:  # noqa: BLE001 - the message is the point
            bad.append(f"{path.relative_to(FONT_DIR)}: {exc}")
            continue
        if not names.get(1):
            bad.append(f"{path.relative_to(FONT_DIR)}: no family name")
    assert not bad, "vendored files that are not usable fonts:\n  " + "\n  ".join(bad)


def test_every_vendored_font_declares_the_family_its_directory_claims():
    """fontconfig matches on the NAME TABLE, never the filename.

    A correctly named file carrying the wrong face substitutes just as
    silently as a missing one.
    """
    require_vendored_fonts()
    wrong = []
    for directory in vendored_family_dirs().values():
        for path in sorted(directory.glob("*.ttf")):
            names = _sfnt_name_records(path)
            declared = (names.get(16) or names.get(1) or "").strip()
            if declared.replace(" ", "") != directory.name:
                wrong.append(
                    f"{path.relative_to(FONT_DIR)} declares {declared!r}, "
                    f"directory claims {directory.name!r}"
                )
    assert not wrong, "\n  ".join(["family name mismatches:"] + wrong)


def test_no_vendored_font_is_a_variable_font():
    """★A VF is reported at its default instance only — bold is synthesised.

    Which is the metric drift this whole change exists to delete. Google Fonts
    now ships most of these families as variable-only, so a future refresh that
    copies upstream files straight in is the likely way this regresses.
    """
    variable = [
        str(p.relative_to(FONT_DIR))
        for p in require_vendored_fonts()
        if _has_table(p, b"fvar")
    ]
    assert not variable, (
        "these carry an fvar table, so fontconfig will expose one weight and "
        "LibreOffice will synthesise the rest:\n  " + "\n  ".join(variable)
    )


def test_every_vendored_family_ships_its_licence():
    require_vendored_fonts()
    missing = [
        d.name for d in vendored_family_dirs().values() if not (d / "LICENSE.txt").is_file()
    ]
    assert not missing, f"vendored families with no LICENSE.txt: {missing}"


def test_the_font_directory_stays_small():
    """The build context is the reason this number is asserted.

    This repo shipped 1.8 GB of `.venv` for months because a `.dockerignore`
    pattern was anchored rather than recursive. Fonts are the kind of thing
    that grows a directory quietly — every family has nine weights upstream and
    only two are used here.
    """
    require_vendored_fonts()
    total = sum(p.stat().st_size for p in FONT_DIR.rglob("*") if p.is_file())
    assert total < 40 * 1024 * 1024, (
        f"assets/fonts is {total / 1024 / 1024:.1f} MB; keep it to the weights "
        f"the themes actually use"
    )
