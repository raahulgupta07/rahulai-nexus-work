"""App Analytics was switchable, and nobody could find the switch.

The feature shipped complete: `HYBRID_APP_ANALYTICS` defaults to true, the
tri-state override lives on `instance_settings.config["features"]`, the route
`GET/PUT /api/instance/features` works, and a super admin has a real toggle.
Measured live: `{"app_analytics": {"value": true, "source": "default",
"default": true}}`.

The toggle was on **Settings > General**, roughly 2,250px down a long page. An
administrator looking for a feature switch opens the tab labelled *Features*
(`settings.accessTab` -> "Features", route `settings/access`), finds nothing
about App Analytics, and reasonably concludes the feature is missing. That is
the same failure the `instance_features` module docstring was written about —
"a feature can ship complete, sit in the image for eleven releases, and never
surface" — repeated one level up: the switch shipped and never surfaced.

So the block is rendered on the Features tab too. What these tests pin is the
part that is easy to get wrong while making it look right:

  * ★It is NOT another org card. Every other switch on that page is scoped to
    one organization and gated on `manage_settings`; this one changes the
    product for EVERY organization on the server and is gated on
    `is_superuser`. Rendered among the org cards it would tell an org admin
    they are changing their own workspace while they change every other
    customer's. The separate box, the badge and the warning line are the whole
    safety property — not decoration.
  * ★Both screens read the SAME composable. A second copy of the state is how
    one of the two screens starts lying about what is on.
  * ★Reset clears the override (`null`), it does not write `false`. "Off" and
    "never chosen" are different states and only the second lets the server
    default apply — writing false would pin the switch off and make the
    deployment's own default unreachable.

★Red proof, carried here rather than left at a shell prompt: the pre-change
file is `git show HEAD:frontend/pages/settings/access.vue`. It contains no
`isSuperAdmin`, no `useInstanceFeatures` and no `deployment.title`, so every
test below fails against it. `test_the_original_gap_is_still_detected`
reconstructs that condition inline so the proof runs every time.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
PAGE = REPO / "frontend" / "pages" / "settings" / "access.vue"
GENERAL = REPO / "frontend" / "pages" / "settings" / "general.vue"
COMPOSABLE = REPO / "frontend" / "composables" / "useInstanceFeatures.ts"
LOCALES = REPO / "locales"

DEPLOYMENT_KEYS = ("title", "badge", "warning")


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. the switch reaches the tab an administrator actually opens
# ---------------------------------------------------------------------------


def test_the_features_tab_renders_the_deployment_block():
    src = _src(PAGE)
    assert "settings.accessPage.deployment.title" in src, (
        "the Features tab does not render a deployment-wide section, so the "
        "App Analytics switch is still only reachable from Settings > General"
    )


def test_it_reads_the_shared_composable_not_its_own_copy():
    """Two screens with two copies of the state is how one starts lying."""
    src = _src(PAGE)
    assert "useInstanceFeatures()" in src, (
        "the Features tab is not using the shared instance-features composable"
    )
    assert "/api/instance/features" not in src, (
        "the page is calling the instance-features endpoint directly instead of "
        "going through useInstanceFeatures — that is a second copy of the state"
    )
    # ...and the other screen still uses it, so they cannot drift apart.
    assert "useInstanceFeatures()" in _src(GENERAL)


def test_the_block_is_actually_fetched():
    """★The binding is inert without this call.

    `useInstanceFeatures` does not auto-fetch — unlike `useAppSettings`, which
    does. A page that destructures `features` and never calls `fetchFeatures()`
    renders nothing, with no error anywhere, and the section simply does not
    appear. That is precisely how this feature was invisible in the first place.
    """
    src = _src(PAGE)
    assert "fetchFeatures()" in src, (
        "nothing calls fetchFeatures(), so `features` stays null and the "
        "deployment block never renders"
    )


# ---------------------------------------------------------------------------
# 2. ★ the safety property — an instance switch must not read as an org switch
# ---------------------------------------------------------------------------


def _gated_on_super_admin(src: str) -> bool:
    """★Presence of the identifier is NOT the property.

    `isSuperAdmin` also appears in the composable destructuring, so a bare
    `"isSuperAdmin" in src` stays true on a page whose deployment block has
    been deleted entirely — which is what the red proof below caught this test
    doing. What matters is that the gate sits immediately above the block, so
    look back from the block itself.
    """
    i = src.find("settings.accessPage.deployment.title")
    if i < 0:
        return False
    return "isSuperAdmin" in src[max(0, i - 800):i]


def test_the_block_is_gated_on_super_admin_not_on_manage_settings():
    assert _gated_on_super_admin(_src(PAGE)), (
        "the deployment block is not gated on is_superuser; the tab itself only "
        "requires manage_settings, so an org admin would get a switch that "
        "changes every organization on the server"
    )


def test_the_block_says_it_is_not_an_organization_setting():
    """The badge and the warning ARE the safety property, not decoration.

    Without them the section looks like every other card on a page whose own
    subtitle says "Control which features your members can reach" — i.e. it
    reads as scoped to this organization, which is exactly wrong.
    """
    src = _src(PAGE)
    for key in ("badge", "warning"):
        assert f"settings.accessPage.deployment.{key}" in src, (
            f"the deployment block does not render its {key}, so nothing on "
            "screen distinguishes an instance-wide switch from an org one"
        )


def test_reset_clears_the_override_rather_than_writing_false():
    """★"Off" and "never chosen" are different states.

    Writing false pins the switch off and makes the deployment's own default
    unreachable — the tri-state exists precisely to keep those distinguishable.
    """
    src = _src(PAGE)
    assert "setFeature(name, null)" in src, (
        "reset does not clear the override; if it writes false the server "
        "default becomes unreachable and the env var stops meaning anything"
    )
    # The composable it delegates to must still honour that.
    assert "value: null" in _src(COMPOSABLE) or "value }" in _src(COMPOSABLE)


# ---------------------------------------------------------------------------
# 3. every language, or the tab renders raw key paths
# ---------------------------------------------------------------------------


def _locale_files():
    return sorted(p for p in LOCALES.glob("*.json") if ".bak-" not in p.name)


def test_every_locale_has_the_three_new_strings():
    missing = []
    for p in _locale_files():
        block = (
            json.loads(_src(p))
            .get("settings", {})
            .get("accessPage", {})
            .get("deployment")
        )
        if not isinstance(block, dict):
            missing.append(f"{p.name}: no deployment block")
            continue
        for k in DEPLOYMENT_KEYS:
            if not str(block.get(k, "")).strip():
                missing.append(f"{p.name}: {k}")
    assert not missing, (
        "a missing key renders the raw path on screen: " + ", ".join(missing)
    )


def test_english_json_still_ends_without_a_trailing_newline():
    """★Upstream's copy of this file ends WITH one; ours does not.

    Adding one is a whole-file diff on a file upstream also edits, so every
    future port of en.json then conflicts. This is cheap to break by accident
    with any editor or json.dump round-trip.
    """
    raw = (LOCALES / "en.json").read_text(encoding="utf-8")
    assert not raw.endswith("\n"), (
        "locales/en.json gained a trailing newline; every future upstream port "
        "of this file will now conflict"
    )


# ---------------------------------------------------------------------------
# 4. ★ the red proof, run every time rather than once at a shell prompt
# ---------------------------------------------------------------------------


CHECKS = {
    "renders the block": lambda s: "settings.accessPage.deployment.title" in s,
    "gated on super admin": _gated_on_super_admin,
    "fetches": lambda s: "fetchFeatures()" in s,
    "reset clears": lambda s: "setFeature(name, null)" in s,
}


def test_the_original_gap_is_still_detected():
    """Reconstruct the pre-change page and require every check to reject it.

    A guard whose assertions happen to agree with today's file detects nothing.
    The pre-change `access.vue` managed org features only — no super-admin
    gate, no instance-features composable, no deployment strings. Stripping
    those markers back out must make every check above fail; if any of them
    still passes, that check is not measuring what its name claims.
    """
    src = _src(PAGE)
    start = src.index("<!-- Deployment-wide switches")
    end = src.index("settings.accessPage.footnote")
    before = src[:start] + src[end:]
    # Also drop the script wiring that only exists for this block.
    for marker in ("useInstanceFeatures()", "fetchFeatures()", "setFeature(name, null)"):
        before = before.replace(marker, "")

    still_passing = [name for name, check in CHECKS.items() if check(before)]
    assert not still_passing, (
        "these checks pass on a page with the deployment block removed, so they "
        f"are not detecting its absence: {still_passing}"
    )


@pytest.mark.parametrize("name,check", list(CHECKS.items()))
def test_each_check_passes_on_the_real_file(name, check):
    """The positive half. Without it, a check that can never pass would still
    satisfy the red proof above and look like a working guard."""
    assert check(_src(PAGE)), f"{name}: failed against the real page"
