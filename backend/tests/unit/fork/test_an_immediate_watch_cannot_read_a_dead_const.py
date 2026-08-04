"""An immediate watch must not read a `const` that does not exist yet.

★This is the defect that shipped as 0.0.518.1 and took the whole product down.

`DataSourceSelector.vue` had, in this order:

    L222  const isAutoMode = computed(() => … selectableSources.value …)
    L471  watch(isAutoMode, …, { immediate: true })
    L517  const selectableSources = computed(…)

A `computed` is LAZY, so the forward reference at L222 is harmless on its own —
which is exactly why it sat there for releases without hurting anything. It only
bites when something evaluates the computed *during setup*, and
`{ immediate: true }` is precisely that something. `selectableSources` is still
in its temporal dead zone at L471, so setup throws

    ReferenceError: Cannot access 'selectableSources' before initialization

Vue aborts the component, the render tree dies, and the home page comes up as a
bare sidebar. Clicking a nav item then changes the URL and renders nothing,
which reads as "the entire app is broken" rather than "one component threw".

★NEITHER PARENT HAD THIS BUG. Measured over three trees:

    upstream up518          0 findings
    our v0.0.510.15         0 findings
    our 0.0.518.1           1 finding

Upstream's `isAutoMode` reads `visibleDataSources` + `projectDefaultSources`,
and upstream placed its watch after both — correctly, with a comment saying so.
Our fork had changed what `isAutoMode` reads (to `selectableSources`, so that
disabled agents don't count toward "Auto"). Also correct, and harmless until
upstream's watch arrived. The PORT put them together. Both sides were right;
the combination was fatal. That is the fourth member of the family already
listed in CLAUDE.md under "three hunks git gets wrong".

★AND NOTHING CAUGHT IT. 5,451 Python tests passed on that build. They cannot
see the frontend: the 169 "frontend guards" read `.vue` files as TEXT, and grep
cannot evaluate declaration order. The Playwright suite that *does* boot the app
is `workflow_dispatch`-only in CI and is not in the release checklist — and both
home specs would have gone green anyway (one asserts nothing at all, the other
only checks the sidebar, which survives the crash). This test is the cheap half
of closing that hole; `frontend/tests/smoke/every-route-renders.spec.ts` is the
half that actually boots the app.

★Read-only, no schema — `tests/unit/fork`. See CLAUDE.md.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"
SEARCH_DIRS = ("components", "composables", "layouts", "pages", "plugins", "ee", "middleware", "utils")

# Top-level `const NAME =` inside a <script setup> block.
DECL = re.compile(r"^const\s+([A-Za-z_$][\w$]*)\s*=", re.M)
WATCH = re.compile(r"watch\(\s*([A-Za-z_$][\w$]*)\s*,", re.M)
READS = re.compile(r"\b([A-Za-z_$][\w$]*)\.value\b")


def _script_setup(text: str) -> tuple[str, int]:
    """The <script setup> body plus the line number it starts on."""
    m = re.search(r"<script[^>]*setup[^>]*>", text)
    if not m:
        return text, 0
    start = m.end()
    end = text.find("</script>", start)
    return text[start: end if end != -1 else len(text)], text.count("\n", 0, start)


def _strip_comments(body: str) -> str:
    """★Blank comments out, KEEPING line count and offsets.

    Without this the test reports the comment that *describes* the hazard as an
    instance of it — the fix for this very bug carries such a comment, and the
    first run of this scanner duly flagged it. A comment is not code.
    """
    body = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), body)
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), body, flags=re.S)


def _balanced(body: str, start: int) -> str:
    """Slice from a declaration to the close of its first bracket group."""
    depth, seen = 0, False
    for i in range(start, len(body)):
        c = body[i]
        if c in "([{":
            depth += 1
            seen = True
        elif c in ")]}":
            depth -= 1
            if seen and depth == 0:
                return body[start:i + 1]
    return body[start:start + 2000]


def _sources():
    for d in SEARCH_DIRS:
        p = FRONTEND / d
        if not p.is_dir():
            continue
        for f in list(p.rglob("*.vue")) + list(p.rglob("*.ts")):
            if "node_modules" in f.parts or ".nuxt" in f.parts:
                continue
            yield f


def _rel(f: Path) -> str:
    """Repo-relative when we can — the self-tests point FRONTEND at a tmpdir."""
    try:
        return str(f.relative_to(REPO))
    except ValueError:
        return str(f)


def _findings():
    out = []
    for f in sorted(_sources()):
        body, off = _script_setup(f.read_text(encoding="utf-8", errors="ignore"))
        body = _strip_comments(body)
        decl_line = {m.group(1): body.count("\n", 0, m.start()) + 1 for m in DECL.finditer(body)}
        decl_pos = {m.group(1): m.start() for m in DECL.finditer(body)}
        if not decl_pos:
            continue

        for m in WATCH.finditer(body):
            watched = m.group(1)
            if watched not in decl_pos:
                continue  # watching a prop, a getter, or something imported
            call = _balanced(body, m.start() + len("watch"))
            if not re.search(r"immediate\s*:\s*true", call):
                continue
            wline = body.count("\n", 0, m.start()) + 1
            for read in {r.group(1) for r in READS.finditer(_balanced(body, decl_pos[watched]))}:
                if decl_line.get(read, 0) > wline:
                    out.append(
                        f"{_rel(f)}:{off + wline} — "
                        f"watch({watched}, …, {{ immediate: true }}) runs during setup; "
                        f"{watched} (line {off + decl_line[watched]}) reads `{read}`, "
                        f"declared later at line {off + decl_line[read]}"
                    )
    return out


def test_the_frontend_tree_is_actually_there():
    """★A path guard, because this scanner is silent when it finds nothing —
    and a scanner pointed at an empty directory is also silent. `dash-app`
    resolves REPO to `/app`, which has no `frontend/` source; that is why this
    suite must run against `/src`. See CLAUDE.md."""
    assert FRONTEND.is_dir(), f"no frontend tree at {FRONTEND} — wrong runner?"
    assert len(list(_sources())) > 300, "found suspiciously few source files"


def test_no_immediate_watch_reads_an_uninitialised_const():
    found = _findings()
    assert not found, (
        "an immediate watch evaluates a computed that reads a const declared "
        "later — setup will throw `Cannot access … before initialization` and "
        "the page will render as a bare shell:\n  " + "\n  ".join(found)
    )


def test_the_scanner_still_recognises_the_shape_it_was_written_for(tmp_path):
    """★Guards the guard. A regex that quietly stops matching reports 0 findings,
    which is indistinguishable from a healthy tree — the exact failure mode that
    let the original bug through. Rebuild the 0.0.518.1 shape and require a hit."""
    global FRONTEND
    real, FRONTEND = FRONTEND, tmp_path
    try:
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "Regression.vue").write_text(
            "<script setup lang=\"ts\">\n"
            "const isAutoMode = computed(() => selectableSources.value.length > 0)\n"
            "watch(isAutoMode, (v) => emit('x', v), { immediate: true })\n"
            "const selectableSources = computed(() => [])\n"
            "</script>\n"
        )
        hits = _findings()
        assert len(hits) == 1, f"the scanner no longer detects its own bug: {hits}"
        assert "selectableSources" in hits[0]
    finally:
        FRONTEND = real


def test_a_comment_describing_the_hazard_is_not_a_finding(tmp_path):
    """★The fix carries a comment naming the pattern. Flagging that comment would
    make the guard fail forever on correct code — it did, on the first run."""
    global FRONTEND
    real, FRONTEND = FRONTEND, tmp_path
    try:
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "Commented.vue").write_text(
            "<script setup lang=\"ts\">\n"
            "const selectableSources = computed(() => [])\n"
            "// watch(isAutoMode, …, { immediate: true }) below reads this,\n"
            "// so it must not move under const selectableSources = computed(...)\n"
            "const isAutoMode = computed(() => selectableSources.value.length > 0)\n"
            "watch(isAutoMode, (v) => emit('x', v), { immediate: true })\n"
            "</script>\n"
        )
        assert _findings() == []
    finally:
        FRONTEND = real


def test_a_lazy_forward_reference_alone_is_not_a_finding(tmp_path):
    """★Narrowness matters. Forward references between computeds are legal and
    common — ours survived releases that way. Only an IMMEDIATE watch turns one
    into a crash, so flagging the rest would bury the real signal in noise."""
    global FRONTEND
    real, FRONTEND = FRONTEND, tmp_path
    try:
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "Lazy.vue").write_text(
            "<script setup lang=\"ts\">\n"
            "const a = computed(() => b.value + 1)\n"
            "watch(a, (v) => emit('x', v))\n"          # no immediate: lazy, fine
            "const b = computed(() => 1)\n"
            "</script>\n"
        )
        assert _findings() == []
    finally:
        FRONTEND = real
