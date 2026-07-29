"""`enabled: false` in the Helm values must mean off.

Helm's `default` treats FALSE as empty, so `{{ default true $p.enabled }}`
returns the default for a value that was explicitly written as false. An SSO
provider an operator deliberately switched off rendered as `enabled: true` and
went live. `enabled: false` was not merely ignored — it was unexpressible.

Verified against real `helm template` while fixing it:

    values: enabled: false, pkce: false, discovery: false
    before: enabled: true,  pkce: true,  discovery: true
    after:  enabled: false, pkce: false, discovery: false
    omitted keys, after: all true (unchanged)

The chart's own helm-unittest suite covers the rendering. These tests run in
the fast Python suite so the pattern cannot come back on a machine with no
Helm installed — most of them.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CHART = REPO / "k8s" / "chart"
TEMPLATES = CHART / "templates"

TEMPLATE_FILES = sorted(p for p in TEMPLATES.glob("*") if p.is_file())


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: p.name)
def test_no_boolean_is_defaulted_with_the_broken_idiom(path):
    """★`default true X` cannot express false. Use `hasKey`, which asks the
    real question: did the operator write this value at all?"""
    # Judge the template, not the comment that explains why the idiom is gone.
    src = "\n".join(
        l for l in path.read_text(encoding="utf-8").splitlines()
        if not l.lstrip().startswith("#")
    )
    hits = re.findall(r"default\s+true\s+\$?\.?[\w.$]+", src)
    assert not hits, f"{path.name}: {hits} — false is unexpressible here"


def test_the_oidc_switches_use_haskey():
    src = (TEMPLATES / "config.yaml").read_text(encoding="utf-8")
    for flag in ("enabled", "pkce", "discovery"):
        assert re.search(rf'hasKey \$p "{flag}"', src), f"{flag} is not hasKey-guarded"


def test_the_chart_still_defaults_those_flags_to_on():
    """Guard the guard: dropping the default entirely would also remove the
    broken idiom, and would silently disable every provider on upgrade."""
    src = (TEMPLATES / "config.yaml").read_text(encoding="utf-8")
    for flag in ("enabled", "pkce", "discovery"):
        m = re.search(rf'{flag}: {{{{ if hasKey \$p "{flag}" }}}}.*?{{{{ else }}}}(\w+)', src)
        assert m and m.group(1) == "true", f"{flag} no longer defaults to on"


def test_the_templates_directory_holds_only_templates():
    """★Everything in `templates/` is RENDERED. A backup copy left there
    produced a second, stale ConfigMap in the output — caught only because the
    duplicate still carried the bug that had just been fixed."""
    strays = [p.name for p in TEMPLATE_FILES
              if p.suffix not in (".yaml", ".yml", ".tpl", ".txt")]
    assert not strays, f"non-template files are being rendered: {strays}"


def test_the_disabled_provider_case_is_covered_by_the_charts_own_tests():
    """The Python check above is a pattern guard. The behavioural proof is the
    helm-unittest case, which was confirmed to FAIL against the old template."""
    suite = (CHART / "tests" / "config_test.yaml").read_text(encoding="utf-8")
    assert "honours an explicitly disabled oidc provider" in suite
    assert "still defaults an omitted flag to on" in suite
