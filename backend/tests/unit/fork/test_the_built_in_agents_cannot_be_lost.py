"""The three built-in agents are part of the product, not part of anyone's data.

Microsoft Fabric, Power BI and City Mart Retail are seeded on a fresh install so
a new admin lands on a populated workspace. Until now nothing kept them there:

  * `delete_data_source` is a HARD delete — `await db.delete(data_source)`, no
    `deleted_at`, no tombstone — so a removed built-in left no trace at all.
  * `delete_connection`'s own docstring says "Data sources that only have this
    connection will also be deleted", which is a second door onto the same loss.
  * `seed_default_agents` fires once, for the FIRST org, and stamps
    `default_agents_seeded` into the org's settings. Afterwards it is inert.

Put together: the agents go, the marker still reads `true`, and the workspace
comes up permanently missing agents it considers its own. That is the state this
install was found in — the marker said seeded while Fabric and Power BI were
gone, and nothing anywhere reported it.

★The two halves are deliberately paired. Restore-at-boot without a delete guard
means a deliberate deletion silently undoes itself at the next restart. A delete
guard without restore does nothing for an install that has already lost them.

★Everything here is a source/AST scan or a pure-function call. Nothing in this
file may need a database — `tests/unit/fork/conftest.py` no-ops the migration
fixture, so a schema-needing test fails "no such table" and reads as a product
bug. See the fork-suite note in CLAUDE.md.
"""
import ast
import pathlib
import re

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[3]
APP = BACKEND / "app"
SEEDER = APP / "services" / "default_agents_seeder.py"
DS_SERVICE = APP / "services" / "data_source_service.py"
CONN_SERVICE = APP / "services" / "connection_service.py"
MAIN = BACKEND / "main.py"


def _src(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _no_comments(path: pathlib.Path) -> str:
    """Source with `#` lines and docstrings removed.

    ★A source-scanning test that reads its own explanation has been written in
    this repo at least four times. The comments in every file below QUOTE the
    broken form they replaced — `await db.delete(data_source)` appears verbatim
    in a comment — so a scan that keeps them matches the thing it exists to
    forbid.
    """
    tree = ast.parse(_src(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body[0].value.value = ""
    stripped = ast.unparse(tree)
    return re.sub(r"^\s*#.*$", "", stripped, flags=re.M)


class TestOneListDecidesWhatIsPermanent:
    def test_the_three_names_are_declared_once(self):
        from app.services.default_agents_seeder import (
            DEFAULT_AGENT_NAMES, FABRIC_AGENT_NAME, POWERBI_AGENT_NAME,
            CITYMART_AGENT_NAME,
        )
        assert set(DEFAULT_AGENT_NAMES) == {
            FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME
        }

    def test_the_membership_test_is_a_pure_function(self):
        from app.services.default_agents_seeder import is_default_agent_name
        assert is_default_agent_name("Power BI") is True
        assert is_default_agent_name("Microsoft Fabric") is True
        assert is_default_agent_name("City Mart Retail") is True

    def test_it_tolerates_case_and_whitespace(self):
        """A stray trailing space must not quietly make a permanent agent
        deletable."""
        from app.services.default_agents_seeder import is_default_agent_name
        assert is_default_agent_name("  power bi ") is True
        assert is_default_agent_name("MICROSOFT FABRIC") is True

    def test_an_ordinary_agent_is_not_protected(self):
        """★The positive control. A guard that protected everything would pass
        every other assertion in this class and break the product."""
        from app.services.default_agents_seeder import is_default_agent_name
        for ordinary in ("Sales", "UAT City Mart", "Power BI Reports", "", "Fabric"):
            assert is_default_agent_name(ordinary) is False, ordinary

    def test_a_non_string_is_not_protected(self):
        from app.services.default_agents_seeder import is_default_agent_name
        assert is_default_agent_name(None) is False
        assert is_default_agent_name({"name": "Power BI"}) is False


class TestBothDeleteDoorsRefuse:
    """★Two doors, one rule. `delete_connection` removes any agent that has only
    that connection, so guarding the agent alone leaves a way around it."""

    def test_the_agent_delete_refuses(self):
        src = _no_comments(DS_SERVICE)
        block = src[src.index("async def delete_data_source"):]
        block = block[:block.index("async def ", 10)]
        assert "is_default_agent_name" in block

    def test_the_connection_delete_refuses(self):
        src = _no_comments(CONN_SERVICE)
        block = src[src.index("async def delete_connection"):]
        assert "is_default_agent_name" in block[:12000]

    def test_the_refusal_is_409_not_403(self):
        """★The caller's permissions are fine; it is the target that cannot be
        deleted. A 403 sends an admin hunting for a role to grant."""
        for path, anchor in ((DS_SERVICE, "async def delete_data_source"),
                             (CONN_SERVICE, "async def delete_connection")):
            src = _no_comments(path)
            block = src[src.index(anchor):]
            guard = block[block.index("is_default_agent_name"):]
            guard = guard[:2000]
            assert "status_code=409" in guard, path.name
            assert "status_code=403" not in guard, path.name

    def test_the_message_names_the_agent_and_offers_the_alternative(self):
        """A refusal that does not say what to do instead is a dead end. The
        agent can be turned off, and the sentence has to say so."""
        for path in (DS_SERVICE, CONN_SERVICE):
            src = _src(path)
            block = src[src.index("is_default_agent_name"):]
            block = block[:2000]
            assert "built-in" in block, path.name
            assert "turn" in block.lower() or "off" in block.lower(), path.name


class TestTheyComeBackIfTheyAreEverLost:
    def test_the_healer_exists_and_is_not_the_signup_seeder(self):
        from app.services import default_agents_seeder as mod
        assert hasattr(mod, "ensure_default_agents")
        assert hasattr(mod, "ensure_default_agents_all_orgs")
        assert mod.ensure_default_agents is not mod.seed_default_agents

    def test_the_healer_ignores_the_seeded_marker(self):
        """★The load-bearing assertion. The marker records that signup ran,
        which is a DIFFERENT question from whether the agents are present now —
        and it is exactly the marker that made this loss permanent. A healer
        that consulted it would be inert on every install that has ever booted,
        including the one that reported the bug.
        """
        src = _no_comments(SEEDER)
        block = src[src.index("async def ensure_default_agents"):]
        block = block[:block.index("async def ensure_default_agents_all_orgs")]
        assert "SEEDED_MARKER_KEY" not in block
        assert "_already_seeded" not in block

    def test_the_signup_seeder_still_uses_the_marker(self):
        """★Positive control for the assertion above: the marker must keep doing
        its own job. A change that deleted it outright would satisfy that test
        and make signup re-seed on every retry."""
        src = _no_comments(SEEDER)
        block = src[src.index("async def seed_default_agents"):]
        assert "_already_seeded" in block

    def test_boot_calls_the_healer(self):
        src = _no_comments(MAIN)
        assert "ensure_default_agents_all_orgs" in src

    def test_boot_gates_it_on_the_scheduler_leader(self):
        """★N workers all run the startup event. Ungated, they race to create
        the same agent and collide on the (organization_id, name) unique slot."""
        src = _no_comments(MAIN)
        call = src.index("ensure_default_agents_all_orgs")
        preceding = src[:call]
        assert "is_scheduler_leader" in preceding[-1500:]

    def test_boot_cannot_be_crashed_by_it(self):
        """A healing step that can take the boot down is worse than the gap it
        closes."""
        tree = ast.parse(_src(MAIN))
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if "ensure_default_agents_all_orgs" in ast.unparse(node):
                found = True
                assert node.handlers, "the call is inside a try with no handler"
        assert found, "the boot call is not wrapped at all"

    def test_an_unreadable_existence_check_does_not_create(self):
        """★An existence check that RAISED must not be read as 'it is missing'.
        Creating then would collide on the unique slot and, worse, could shadow
        the real agent."""
        src = _no_comments(SEEDER)
        block = src[src.index("async def ensure_default_agents"):]
        block = block[:block.index("async def ensure_default_agents_all_orgs")]
        stage = block[block.index("_ds_name_exists"):]
        stage = stage[:stage.index("try:", stage.index("except"))] if "try:" in stage[stage.index("except"):] else stage[:1200]
        assert "continue" in stage

    def test_the_healer_never_raises(self):
        """Both entry points swallow. The caller is a boot event and a failure
        there costs the whole application, not one agent."""
        tree = ast.parse(_src(SEEDER))
        for name in ("ensure_default_agents", "ensure_default_agents_all_orgs"):
            fn = next(
                n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name
            )
            raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
            assert not raises, f"{name} can raise"


class TestWhatThisDoesNotChange:
    """★Recorded so nobody widens this by accident later."""

    def test_turning_an_agent_off_is_untouched(self):
        """The refusal offers 'turn it off' as the alternative, so the field it
        points at has to still be there."""
        from app.models.data_source import DataSource
        assert "is_active" in DataSource.__table__.c

    def test_the_healer_does_not_reconfigure_an_existing_agent(self):
        """It restores what is MISSING. An agent that is present must be left
        completely alone — config, credentials, tables and instructions
        included."""
        src = _no_comments(SEEDER)
        block = src[src.index("async def ensure_default_agents"):]
        block = block[:block.index("async def ensure_default_agents_all_orgs")]
        # ★Anchor on the APPEND, not on the word "present" — the first match of
        # that is the summary dict's own initialiser, and the slice then covers
        # the wrong 400 characters entirely. My first version of this assertion
        # did exactly that and failed against correct code.
        marker = "'present'].append"
        assert marker in block, "the present-branch is not recorded"
        present = block[block.index(marker):block.index(marker) + 300]
        assert "continue" in present
        for mutation in ("update(", "db.add(", "flag_modified"):
            assert mutation not in block, f"the healer mutates existing state ({mutation})"
