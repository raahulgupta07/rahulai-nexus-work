"""The route `/` must be the chat composer, not some other page's copy.

WHAT BROKE. `frontend/pages/index.vue` — the home route, a chat composer built
on `PromptBoxV2` — was overwritten with a byte-identical copy of
`frontend/pages/reports/index.vue`, the reports browser. Both files ended up
915 lines, md5 `7e19fea8d7880a0841c973d3ec20edb7`.

WHAT THE USER SAW. Nothing failed. No console error, no 404, no red anywhere:
the page rendered perfectly — it was simply the WRONG page. Three "New report"
buttons (`pages/reports/index.vue`, `layouts/default.vue`, and their callers)
do `router.push({ path: '/' })` because the composer creates the report lazily
on the first question. Landing on the reports LIST, those became navigations to
the route the user was already on, which Vue Router aborts in silence. The
report was: "I click New report and I don't see the chat screen any more, and
the top-left button doesn't work either."

WHY EVERYTHING ELSE BEING GREEN DID NOT HELP. The Python suite cannot see a
rendered page at all, so it had nothing to say. The Playwright home specs DID
fail and were written off as stale. A byte-for-byte page swap is invisible to
every check that asks "does the code work" rather than "is this the right
code" — the copied file is perfectly good code.

WHAT THIS PINS. Properties, never a line count or an md5:

1. `/` and `/reports` must not be byte-identical. Two distinct routes with
   identical content is the signature of exactly this accident.
2. The home route must IMPORT and RENDER `PromptBoxV2`. ★The broken file
   contained the string `PromptBoxV2` exactly once, inside a comment, so the
   obvious `"PromptBoxV2" in src` check PASSES ON THE BUG. Comments are
   stripped before anything is asserted.
3. Generalised: the home route must render some composer-shaped component at
   all — so renaming `PromptBoxV2` fails (2) with "update the name here" while
   (3) stays green, rather than both failing and reading like the page is gone.
4. From the other direction: every `router.push` / `navigateTo` whose own
   comment says it is routing to the composer must target a route whose page
   file actually renders that composer. This is the check that catches the
   defect at the caller, and it is the one the user's bug report was about.

★LIMIT, stated honestly: this reads `.vue` files as TEXT (see the note in
CLAUDE.md about the 169 frontend guards). It cannot mount a component, evaluate
declaration order, or prove a pixel. `<PromptBoxV2 v-if="false">` would satisfy
it. What it can do is notice that the home route stopped being the home route,
which is the whole of the defect above. The browser smoke suite remains the
only thing that can see a rendered page; this exists because that suite was
disbelieved.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"
PAGES = FRONTEND / "pages"
HOME = PAGES / "index.vue"
REPORTS = PAGES / "reports" / "index.vue"

# The component the home route is built on today.
COMPOSER = "PromptBoxV2"

# Generalised shape of (2), for property (3): anything a reader would call a
# prompt box / composer. Deliberately loose — it exists to keep a RENAME
# readable, not to be precise.
COMPOSER_SHAPED = re.compile(r"\b(?:\w*PromptBox\w*|\w*Composer\w*|\w*PromptInput\w*)\b")

# A nav call, capturing the literal route: navigateTo('/'), router.push('/x'),
# router.push({ path: '/' }), router.replace({ path: '/x' }).
NAV = re.compile(
    r"(?:router\.(?:push|replace)|navigateTo)\s*\(\s*(?:\{\s*path\s*:\s*)?['\"]([^'\"]+)['\"]"
)

# A comment line claiming this navigation lands on the composer.
CLAIMS_COMPOSER = re.compile(r"(?:composer|PromptBox)", re.I)

# How far back from a nav call a comment still counts as describing it.
COMMENT_LOOKBACK = 6


def _strip_comments(src: str) -> str:
    """Remove HTML, block and line comments from a `.vue` source.

    ★Load-bearing. The broken home page mentioned ``PromptBoxV2`` in a comment
    and nowhere else, so any check run against the raw text passes on the bug.
    A guard that cannot fail is a comment with a test's salary.
    """
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    # `//` only when it does not follow `:` (a URL scheme) or a quote.
    src = re.sub(r"(?<![:'\"\\])//[^\n]*", " ", src)
    return src


def _imports(src: str) -> set:
    """Component identifiers this file imports (comments already stripped)."""
    names = set()
    for m in re.finditer(r"^\s*import\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]", src, re.M):
        names.add(m.group(1))
    for m in re.finditer(r"^\s*import\s*\{([^}]*)\}\s*from\s+['\"]", src, re.M):
        for part in m.group(1).split(","):
            part = part.strip().split(" as ")[-1].strip()
            if part:
                names.add(part)
    return names


def _template(src: str) -> str:
    """The `<template>` block, or the whole file if it has no explicit one."""
    m = re.search(r"<template[^>]*>(.*)</template>", src, re.S)
    return m.group(1) if m else src


def _rendered_tags(src: str) -> set:
    """PascalCase component tags the template actually renders."""
    return set(re.findall(r"<([A-Z][\w.]*)", _template(src)))


def _renders(src: str, component: str) -> bool:
    """True only if `component` is both imported and rendered, comments aside.

    Nuxt auto-imports components, so an explicit import is not strictly
    required to render one — the home page has always had one, and requiring it
    is a second independent signal that the file is the file it claims to be.
    """
    clean = _strip_comments(src)
    return component in _imports(clean) and component in _rendered_tags(clean)


def _page_file_for_route(route: str):
    """The `pages/` file Nuxt would resolve a literal route to, if any.

    Static routes only. A route with a dynamic segment resolves to a `[id].vue`
    that no literal can be matched against, and returning None means such a
    navigation is skipped rather than guessed at.
    """
    path = route.split("?")[0].split("#")[0].strip("/")
    if not path:
        return HOME if HOME.exists() else None
    for candidate in (PAGES / f"{path}.vue", PAGES / path / "index.vue"):
        if candidate.exists():
            return candidate
    return None


def _composer_navigations(sources: dict) -> list:
    """Every nav call whose nearby comment claims it routes to the composer.

    `sources` maps a display name to the file's text. Returns
    `(name, line_number, route)` triples.
    """
    found = []
    for name, src in sources.items():
        lines = src.splitlines()
        for i, line in enumerate(lines):
            m = NAV.search(line)
            if not m:
                continue
            window = lines[max(0, i - COMMENT_LOOKBACK):i]
            comments = [l for l in window if re.match(r"\s*(?://|\*|<!--)", l)]
            if any(CLAIMS_COMPOSER.search(l) for l in comments):
                found.append((name, i + 1, m.group(1)))
    return found


def _vue_sources() -> dict:
    """Every live `.vue` file under frontend/, keyed by repo-relative path.

    ★Skips the gitignored `.bak-*` snapshots — there were 1043 tree-wide as of
    2026-08-07 and they hold whatever the page said months ago.
    """
    out = {}
    for path in FRONTEND.rglob("*.vue"):
        rel = path.relative_to(REPO).as_posix()
        if ".bak-" in rel or "/node_modules/" in rel or "/dist/" in rel:
            continue
        out[rel] = path.read_text(encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# The checkers. Each returns a list of failure sentences, and each takes its
# input as an ARGUMENT rather than reading disk, so the same logic can be
# pointed at the broken file to prove it fails. See the module docstring.
# --------------------------------------------------------------------------


def check_not_twins(home_src: str, reports_src: str) -> list:
    if home_src == reports_src:
        return [
            "frontend/pages/index.vue is byte-identical to "
            "frontend/pages/reports/index.vue. Two distinct routes cannot have "
            "the same content — the home route has been overwritten with a copy "
            "of the reports browser."
        ]
    return []


def check_home_renders_the_composer(home_src: str) -> list:
    if _renders(home_src, COMPOSER):
        return []
    clean = _strip_comments(home_src)
    detail = []
    if COMPOSER not in _imports(clean):
        detail.append("not imported")
    if COMPOSER not in _rendered_tags(clean):
        detail.append("not rendered in <template>")
    if COMPOSER in home_src:
        detail.append(
            "the name appears ONLY inside a comment, which renders nothing"
        )
    return [
        f"frontend/pages/index.vue does not render {COMPOSER}: "
        + "; ".join(detail)
        + ". If the component was renamed, update COMPOSER in this test; if the "
        "file was overwritten, the home route is the wrong page."
    ]


def check_home_has_some_composer(home_src: str) -> list:
    clean = _strip_comments(home_src)
    rendered = _rendered_tags(clean)
    if any(COMPOSER_SHAPED.fullmatch(tag) for tag in rendered):
        return []
    return [
        "frontend/pages/index.vue renders no composer/prompt-box component at "
        "all. The home route is where a user starts a conversation; without an "
        "entry point it is not the home route. Rendered components were: "
        + (", ".join(sorted(rendered)) or "(none)")
    ]


def check_composer_navigations(sources: dict, resolve_route) -> list:
    """`resolve_route(route)` -> the target page's source text, or None."""
    problems = []
    navs = _composer_navigations(sources)
    if not navs:
        problems.append(
            "No navigation in frontend/ claims to route to the composer. The "
            '"New report" buttons used to say so in a comment beside '
            "router.push({ path: '/' }); if that wording changed, update "
            "CLAIMS_COMPOSER — this check is asleep until it matches something."
        )
        return problems
    for name, line, route in navs:
        target = resolve_route(route)
        if target is None:
            continue  # dynamic or external route — nothing static to check
        if not _renders(target, COMPOSER):
            problems.append(
                f"{name}:{line} says it routes to the composer and pushes "
                f"'{route}', but that route's page does not render {COMPOSER}. "
                "Either the comment lies or the page was overwritten; the user "
                "sees a button that navigates to the wrong screen, or to the "
                "screen they are already on, in silence."
            )
    return problems


# --------------------------------------------------------------------------
# The tests. Thin: read the real paths, delegate, report every failure at once.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def home_src() -> str:
    assert HOME.exists(), f"{HOME} is missing — the home route has no page file"
    return HOME.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reports_src() -> str:
    assert REPORTS.exists(), f"{REPORTS} is missing"
    return REPORTS.read_text(encoding="utf-8")


def test_the_home_route_is_not_a_copy_of_the_reports_browser(home_src, reports_src):
    problems = check_not_twins(home_src, reports_src)
    assert not problems, "\n".join(problems)


def test_the_home_route_renders_the_prompt_box(home_src):
    problems = check_home_renders_the_composer(home_src)
    assert not problems, "\n".join(problems)


def test_the_home_route_has_a_composer_of_some_name(home_src):
    problems = check_home_has_some_composer(home_src)
    assert not problems, "\n".join(problems)


def test_a_comment_naming_the_composer_must_route_to_the_composer():
    sources = _vue_sources()

    def resolve(route: str):
        page = _page_file_for_route(route)
        return page.read_text(encoding="utf-8") if page else None

    problems = check_composer_navigations(sources, resolve)
    assert not problems, "\n".join(problems)
