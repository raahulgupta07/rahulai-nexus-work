"""A second stack on one host must be describable in .env alone.

WHAT THIS COST
--------------
Two deployments — production and dev — run on one server. Both had reached the
state where `docker-compose.yaml` was locally modified: the network renamed to a
shared external one, the service renamed, container names changed, the caddy
service and its two volumes COMMENTED OUT, and on dev the volumes renamed.

`upgrade.sh` refuses to run on a dirty working tree, and it is right to — it
will not build a pile of loose edits. So the consequence of those edits was that
the script could not be used at all, and **every upgrade on that host was done
by hand**. A `git pull` also risked a conflict in the one file the stack cannot
boot without, at exactly the moment somebody is trying to deploy.

★The edits were not wrong. They were per-installation facts written into a
tracked file because the file offered nowhere else to put them.

★★★And one of them was actively harmful. Compose gives every service its own
name as a network alias, so once both stacks joined one shared network they BOTH
answered to `postgres`. Measured live: each app resolved `postgres` to the other
environment's database roughly half the time. It failed closed (the passwords
differ) and surfaced as ~810 authentication failures a day, read for weeks as a
rotated credential. Pointing DASH_DATABASE_URL at a stack's own container name
is the fix, and it only helps if a second stack can SET that without editing
this file — which is what this test is about.

WHAT IS PINNED HERE
-------------------
1. Every setting that differs between two stacks on one host reads from an
   environment variable.
2. The default of each is unchanged, so a normal install renders exactly as
   before. A knob that silently changes behaviour for everyone else is not a
   knob, it is a migration.
3. caddy is disabled by a PROFILE, not by deletion — so "we terminate SSL
   elsewhere" stops being a reason to edit the file.
4. .env.example documents each variable, because a knob nobody can find is one
   they will keep solving with an editor.

★These are source assertions. `docker compose config` would be the stronger
check and needs a docker daemon, which the fork suite deliberately does not
have. `scripts/release-test.sh` renders both shapes for real.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "docker-compose.yaml"
ENV_EXAMPLE = REPO / ".env.example"


def _src() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Executable YAML only — comment lines dropped.

    ★This file explains its own landmines at length, and those explanations
    quote the very strings being scanned for. A guard that reads its own
    documentation passes on a file that does nothing; this codebase has made
    that mistake four separate times.
    """
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# --------------------------------------------------------------- the knobs

# (variable, default it must keep, what it is for)
KNOBS = [
    ("DASH_PROJECT_NAME", "cityagentinsights", "the compose project name"),
    ("DASH_APP_CONTAINER", "dash-app", "the app container name"),
    ("DASH_POSTGRES_CONTAINER", "dash-postgres", "the postgres container name"),
    ("DASH_NETWORK_NAME", None, "the network name"),
    ("DASH_NETWORK_EXTERNAL", "false", "whether the network is external"),
    ("DASH_POSTGRES_VOLUME", None, "the database volume"),
    ("DASH_UPLOADS_VOLUME", None, "the uploads volume"),
    ("DASH_BRANDING_VOLUME", None, "the branding volume"),
    ("DASH_LOGS_VOLUME", None, "the logs volume"),
    ("DASH_IMAGE", "cityagentinsights:local", "the image tag"),
]


def test_every_per_install_setting_reads_from_the_environment():
    code = _code(_src())
    missing = [name for name, _, _ in KNOBS if "${" + name not in code]
    assert not missing, (
        "these must be settable from .env or a second stack has to edit this "
        "tracked file, which makes upgrade.sh refuse to run: " + ", ".join(missing)
    )


def test_each_knob_keeps_the_default_it_had():
    """★A knob that changes behaviour when unset is not a knob, it is a migration."""
    code = _code(_src())
    for name, default, purpose in KNOBS:
        if default is None:
            continue
        assert "${" + name + ":-" + default in code, (
            "%s (%s) no longer defaults to %r — an existing install would "
            "change shape on upgrade with nothing in .env to explain it"
            % (name, purpose, default)
        )


def test_the_volume_names_are_pinned_and_follow_the_project():
    """★★★An unpinned volume name MOVES when the project is renamed, and Compose
    then creates a fresh empty one. Postgres initialises it and reports healthy,
    so the failure presents as a blank installation with no error anywhere."""
    code = _code(_src())
    for vol in ("postgres_data", "uploads_data", "branding_data", "logs_data"):
        pat = (
            r"^\s+" + vol + r":\s*\n\s+name:\s*\$\{DASH_[A-Z_]+VOLUME:-"
            r"\$\{DASH_PROJECT_NAME:-cityagentinsights\}_" + vol + r"\}"
        )
        assert re.search(pat, code, re.M), (
            "volume %r has no pinned name, or its default no longer tracks the "
            "project name — renaming the project would point the stack at an "
            "empty volume" % vol
        )


def test_the_database_host_is_the_container_not_the_service():
    """★★★The measured outage: two stacks on one shared network both answer to
    the service alias `postgres`, and Docker round-robins between them."""
    code = _code(_src())
    url = [l for l in code.splitlines() if "DASH_DATABASE_URL" in l]
    assert url, "DASH_DATABASE_URL is not set in this file at all"
    assert "${DASH_POSTGRES_CONTAINER:-postgres}:5432" in url[0], (
        "the database host is hardcoded. On a shared network the service alias "
        "is ambiguous between stacks: " + url[0].strip()
    )


def test_caddy_is_disabled_by_a_profile_not_by_deletion():
    """★Both servers had commented this service out by hand — 26 lines of local
    edit in a tracked file, purely to say 'something else terminates SSL'."""
    code = _code(_src())
    m = re.search(r"^  caddy:\s*\n((?:\s+.*\n)+?)(?=^  \S|\Z)", code, re.M)
    assert m, "the caddy service is gone — disable it with a profile, do not delete it"
    assert re.search(r'profiles:\s*\[\s*"caddy"\s*\]', m.group(1)), (
        "caddy has no profile, so a plain `up` starts it and an install that "
        "terminates SSL elsewhere has a reason to edit this file again"
    )


def test_the_default_install_still_gets_its_own_private_network():
    code = _code(_src())
    assert "${DASH_NETWORK_EXTERNAL:-false}" in code, (
        "the network now defaults to external — a fresh install would fail to "
        "start against a network nobody has created"
    )


def test_env_example_documents_every_knob():
    """★A setting nobody can find is one they will keep solving with an editor."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [name for name, _, _ in KNOBS if name not in text]
    assert not missing, (
        ".env.example does not mention: " + ", ".join(missing)
    )


def test_the_shared_network_warning_is_written_down_next_to_the_switch():
    """★★★The two settings are safe apart and dangerous together. Someone who
    sets only the network gets the DNS collision this whole file exists for, so
    the warning has to live where they are reading, not in a commit message."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    start = text.index("DASH_NETWORK_NAME")
    window = text[max(0, start - 2000):start + 500]
    assert "DASH_POSTGRES_CONTAINER" in window, (
        "the shared-network switch is documented without pointing at "
        "DASH_POSTGRES_CONTAINER, which is what stops the two stacks resolving "
        "each other's database"
    )
