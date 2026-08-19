"""The database host must never default to another stack's container.

Two stacks of this product commonly run on one host — a live one and a
staging one — sharing a network so a reverse proxy can reach both. Their
compose files are then edited locally so each names its own containers
(`dash-postgres` and `dash-postgres-dev`).

★A default of `dash-postgres` in DASH_DATABASE_URL is therefore correct for
exactly one of them and points the OTHER at the first one's database. Silently.
That is the same fault as the hostname collision it was meant to fix, aimed the
other way, and it would have been far worse: not refused connections, but a
staging application reading and writing production data.

The default is deliberately the unchanged `postgres`. Setting
DASH_POSTGRES_CONTAINER in .env removes the ambiguity for a stack that needs
it; leaving it unset behaves exactly as before. preflight.sh fails loudly if
the name in use resolves to more than one container.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
COMPOSE = [REPO / "docker-compose.yaml", REPO / "docker-compose.dev.yaml"]


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_the_database_host_does_not_default_to_a_container_name(path):
    src = path.read_text(encoding="utf-8")
    url = [l for l in src.splitlines() if "DASH_DATABASE_URL=postgresql" in l]
    assert url, f"{path.name} has no DASH_DATABASE_URL"
    host = re.search(r"@\$\{DASH_POSTGRES_CONTAINER:-([^}]+)\}", url[0])
    assert host, f"{path.name}: the database host is not overridable via DASH_POSTGRES_CONTAINER"
    assert not host.group(1).startswith("dash-postgres"), (
        f"{path.name}: the host defaults to '{host.group(1)}', a specific "
        "container. On a host running two stacks that is one stack pointed at "
        "the other's database."
    )


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_the_host_is_still_overridable(path):
    """★The override is the whole fix — without it there is no way to
    disambiguate two stacks that share a network."""
    src = path.read_text(encoding="utf-8")
    assert "${DASH_POSTGRES_CONTAINER:-" in src


def test_preflight_still_detects_an_ambiguous_host():
    """★Because the default is now unchanged, DETECTION is what protects
    anyone who has not set the override."""
    src = (REPO / "preflight.sh").read_text(encoding="utf-8")
    assert "resolves to $COUNT containers" in src, (
        "preflight no longer reports a database hostname answering to more "
        "than one container"
    )
