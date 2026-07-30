"""The files that describe an installation must not disagree with each other.

Every defect this locks down was found by actually installing from a fresh
clone of the published repository and watching what happened, not by reading
the files. Each one is the same shape: two places describe one thing, they
drift, and the disagreement is invisible until it reaches a server.

  * docker-compose.yaml defaulted the database to `dash` / `dash_insights`
    while .env.example and docker-compose.dev.yaml said `bow` / `bagofwords`.
    An operator who copied .env.example and then ran the SSL file got a
    working stack on a different database from the one the runbook describes.

  * install.sh generated BOW_ENCRYPTION_KEY and substituted it into a file
    that only contains DASH_ENCRYPTION_KEY. The substitution matched nothing,
    the check that followed failed, and a first install aborted every time on
    a message about writing rather than about the name.

  * README.md told the reader to clone a repository under its previous name,
    so the very first command failed.

These are cheap to assert and were expensive to find.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]

# ★Two, and only two, by design. There were four. docker-compose.shadow.yaml
# and docker-compose.dev-fast.yaml were developer-only stacks that nothing
# referenced and that had both rotted — each bind-mounted a configuration file
# that no longer exists, and Docker creates a DIRECTORY for a missing bind
# source, so they would have started and read no configuration at all. An
# installation now chooses between exactly two files: this one with SSL, or the
# dev one without.
COMPOSE_FILES = (
    "docker-compose.yaml",
    "docker-compose.dev.yaml",
)


def _read(name: str) -> str:
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present in this checkout")
    return path.read_text(encoding="utf-8")


def _env_example_value(key: str) -> str:
    for line in _read(".env.example").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f".env.example does not set {key}")


def _compose_defaults(text: str, var: str) -> set:
    """Every `${VAR:-default}` written for VAR anywhere in the file."""
    return set(re.findall(r"\$\{" + re.escape(var) + r":-([^}]*)\}", text))


@pytest.mark.parametrize("var", ["POSTGRES_USER", "POSTGRES_DB"])
def test_every_compose_default_matches_env_example(var):
    """★The database name is decided once, at first boot, and never again.

    Postgres applies these only when it initialises an empty data directory,
    so a file that disagrees does not create a second database — it points the
    application at one that does not exist, and the failure reads as an
    authentication problem rather than a naming one.
    """
    expected = _env_example_value(var)
    for name in COMPOSE_FILES:
        found = _compose_defaults(_read(name), var)
        assert found, f"{name} never defaults {var}"
        assert found == {expected}, (
            f"{name} defaults {var} to {sorted(found)}, .env.example says "
            f"{expected!r}. Every occurrence must agree — the service block, "
            f"the healthcheck and the connection string are all read."
        )


@pytest.mark.parametrize("var", ["POSTGRES_USER", "POSTGRES_DB"])
def test_a_compose_file_is_internally_consistent(var):
    """The healthcheck and the connection string must name the same database
    as the service that creates it. They are three separate lines."""
    for name in COMPOSE_FILES:
        found = _compose_defaults(_read(name), var)
        assert len(found) <= 1, (
            f"{name} uses more than one default for {var}: {sorted(found)}. "
            f"pg_isready would then probe a different database from the one "
            f"the application connects to, and the stack reports healthy while "
            f"the app cannot reach its data."
        )


def test_install_tooling_writes_the_variable_that_actually_exists():
    """★install.sh substitutes into .env.example. If it edits a key that file
    does not contain, it silently produces a .env with an empty secret."""
    install = _read("install.sh")
    for key in ("DASH_ENCRYPTION_KEY", "POSTGRES_PASSWORD"):
        assert f"s|^{key}=" in install, (
            f"install.sh does not substitute {key}, but .env.example defines it"
        )
        assert f"{key}=" in _read(".env.example")
    assert "s|^BOW_ENCRYPTION_KEY=" not in install, (
        "install.sh is substituting the pre-rename key name again. "
        ".env.example contains DASH_ENCRYPTION_KEY, so this matches nothing "
        "and the install writes an empty key."
    )


def test_the_shipped_container_names_are_the_documented_ones():
    """The scripts default to a container name. It must be one the compose
    files actually create — this defaulted to `bow-app-cai` long after the
    rename, so the 'is a stack already running?' guard could never fire."""
    install = _read("install.sh")
    m = re.search(r'APP="\$\{CITYAGENT_APP_CONTAINER:-([^}]*)\}"', install)
    assert m, "install.sh no longer declares a default app container name"
    default_app = m.group(1)
    dev = _read("docker-compose.dev.yaml")
    names = set(re.findall(r"\$\{DASH_APP_CONTAINER:-([^}]*)\}", dev)) or set(
        re.findall(r"^\s*container_name:\s*(\S+)\s*$", dev, re.M)
    )
    assert default_app in names, (
        f"install.sh defaults to container {default_app!r}, which "
        f"docker-compose.dev.yaml does not create (it makes {sorted(names)})"
    )


def test_the_readme_clones_this_repository():
    """★The first command a new operator runs. It named the repository under
    its previous name and simply failed."""
    readme = _read("README.md")
    clones = re.findall(r"git clone \S+", readme)
    assert clones, "README has no clone command"
    for c in clones:
        assert "cityagent-coworker-ai" not in c, (
            f"README still clones the previous repository name: {c}"
        )


def test_the_operator_runbooks_do_not_hardcode_one_installs_database():
    """★Installs created before the rename hold bow/bagofwords, newer ones
    dash/dash_insights. A runbook that hardcodes either is wrong on half the
    fleet, so the commands read the names out of .env instead."""
    for name in ("DOCKER.md", "UPGRADE.md"):
        text = _read(name)
        bad = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"-U\s+(bow|dash)\b(?!.*POSTGRES_USER)", line)
            and not line.lstrip().startswith(">")
        ]
        assert not bad, (
            f"{name} hardcodes a database user in a runnable command:\n  "
            + "\n  ".join(bad)
        )


def test_no_compose_file_defaults_the_database_password():
    """★A default password in a compose file is a password in the repository.

    docker-compose.shadow.yaml defaulted POSTGRES_PASSWORD to `bowpassword` —
    the literal string published here — on a host-mapped port, for a database
    holding a CLONE of live data. Left empty, Postgres refuses to initialise
    and says so, which is the correct outcome for a missing password.
    """
    for name in COMPOSE_FILES:
        found = _compose_defaults(_read(name), "POSTGRES_PASSWORD")
        assert found <= {""}, (
            f"{name} defaults POSTGRES_PASSWORD to {sorted(found)}"
        )


def test_every_compose_bind_mount_source_exists():
    """★A missing bind source is not an error. Docker creates a DIRECTORY in
    its place, so the container starts, mounts an empty directory over the
    path, and runs with no configuration — reporting healthy throughout."""
    import re
    missing = []
    for name in COMPOSE_FILES:
        for src in re.findall(r"^\s*-\s+(\./[^:]+):", _read(name), re.M):
            if not (ROOT / src).exists():
                missing.append(f"{name}  ->  {src}")
    assert not missing, "bind mount sources that do not exist:\n  " + "\n  ".join(missing)
