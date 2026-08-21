"""DEF-C — the LDAP preview served a bare 500 and kept the reason to itself.

Measured on 0.0.543.13, against a real OpenLDAP:

    GET /api/enterprise/ldap/sync/preview  ->  500  "Internal Server Error"

    app/ee/ldap/routes.py:113        preview_sync
    app/ee/ldap/sync_service.py:193  preview_sync
    app/ee/ldap/connection.py:211    search_groups
    ldap3.core.exceptions.LDAPObjectClassError:
        invalid class in objectClass attribute: group

Three code paths reached that one failure and each answered differently:

    background job   caught it, logged a reason, "completed with errors"
    test-connection  swallowed it, `group_count: null`, HTTP 200
    preview          did not catch it at all, 500, empty body

So an admin pressed Preview, got a blank server error, and the fact they needed
— that `(objectClass=group)` is Active Directory syntax and OpenLDAP has no such
class — existed, in the process, one frame down, where nobody can read it. Same
family as DEF-017 (`web_search` wrote a reason nothing rendered) and DEF-B (the
spreadsheet trailer notice that reached neither the model nor the screen).

★The subtle half is `groups_to_remove`. `seen_dns` is empty after a refused
search, so computing that count unconditionally makes the preview announce it is
about to delete EVERY group the organization has — absence of evidence rendered
as evidence of absence, on the one screen whose entire job is to say what a sync
would do. Guarded by `TestAFailedSearchNeverLooksLikeAnEmptyDirectory`.

★File-scanning only, plus pure calls into `explain_search_failure`. Nothing here
may need a schema — `tests/unit/fork/conftest.py` no-ops the migration fixture.
"""
import ast
import pathlib
import re

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[3]
REPO = BACKEND.parent
LDAP = BACKEND / "app" / "ee" / "ldap"
SYNC = LDAP / "sync_service.py"
ROUTES = LDAP / "routes.py"
CONN = LDAP / "connection.py"
MODAL = REPO / "frontend" / "components" / "settings" / "LdapConfigModal.vue"
COMPOSABLE = REPO / "frontend" / "ee" / "composables" / "useLdapSync.ts"


def _src(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _py_no_comments(p: pathlib.Path) -> str:
    """★The comments in these files QUOTE the broken form. A scan that keeps
    them matches the thing it exists to forbid — made in this repo repeatedly."""
    tree = ast.parse(_src(p))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                b[0].value.value = ""
    return re.sub(r"^\s*#.*$", "", ast.unparse(tree), flags=re.M)


def _vue_no_comments() -> str:
    return re.sub(r"<!--.*?-->", "", _src(MODAL), flags=re.S)


class _ObjClassError(Exception):
    """Stands in for ldap3's exception without importing ldap3.

    The helper dispatches on `__class__.__name__`, so the name is the contract.
    """
    pass


_ObjClassError.__name__ = "LDAPObjectClassError"


class TestTheSentenceItself:
    def test_it_names_the_filter_and_the_two_real_alternatives(self):
        from app.ee.ldap.connection import explain_search_failure
        msg = explain_search_failure(
            _ObjClassError("invalid class in objectClass attribute: group"),
            what="group",
            search_filter="(objectClass=group)",
            search_base="dc=cityagent,dc=io",
        )
        assert "(objectClass=group)" in msg
        assert "groupOfNames" in msg
        assert "posixGroup" in msg
        assert "dc=cityagent,dc=io" in msg

    def test_it_says_the_server_is_not_the_problem(self):
        """★The measured failure happens AFTER a successful bind. A message that
        reads like a connectivity fault sends the admin to the network."""
        from app.ee.ldap.connection import explain_search_failure
        msg = explain_search_failure(
            _ObjClassError("invalid class in objectClass attribute: group"),
            what="group", search_filter="(objectClass=group)",
        )
        low = msg.lower()
        assert "reachable" in low and "bind succeeded" in low

    def test_an_ordinary_failure_gets_an_ordinary_sentence(self):
        """★Positive control. A helper that returned the objectClass advice for
        everything would pass both tests above and mislead on a timeout."""
        from app.ee.ldap.connection import explain_search_failure
        msg = explain_search_failure(
            TimeoutError("timed out"), what="group",
            search_filter="(objectClass=groupOfNames)",
        )
        assert "groupOfNames" in msg
        assert "posixGroup" not in msg
        assert "timed out" in msg

    def test_a_bare_exception_still_produces_words(self):
        """`str(exc)` is empty for many exception types; a message that degrades
        to ': ' explains nothing."""
        from app.ee.ldap.connection import explain_search_failure
        msg = explain_search_failure(ValueError(), what="user")
        assert "ValueError" in msg

    def test_the_base_named_is_the_base_searched(self):
        """★`search_groups` falls back to `base_dn` when `group_search_base` is
        unset, so a message built from the raw config field would name a base the
        search did not use."""
        src = _py_no_comments(CONN)
        assert "def group_search_base" in src
        block = src[src.index("def group_search_base"):]
        block = block[:block.index("def search_groups")]
        assert "self.config.group_search_base or self.config.base_dn" in block
        after = src[src.index("def search_groups"):]
        after = after[:2000]
        assert "search_base = self.group_search_base" in after


class TestAllThreeSurfacesUseTheSameWords:
    """★The divergence IS the defect. Three vocabularies for one directory is
    how a reason ends up existing everywhere and reaching nobody."""

    @pytest.mark.parametrize("path", [SYNC, ROUTES])
    def test_the_caller_formats_through_the_helper(self, path):
        assert "explain_search_failure" in _py_no_comments(path), path.name

    def test_nobody_hand_rolls_the_old_string(self):
        src = _py_no_comments(SYNC)
        assert 'f"LDAP search failed: {e}"' not in src

    def test_the_connection_test_no_longer_swallows(self):
        """★`except Exception: pass` left both counts null, so "no groups here"
        and "the search was refused" arrived identically — the
        `ConnectionTable.no_rows` defect in another costume."""
        src = _py_no_comments(ROUTES)
        block = src[src.index("if test_result.connected"):]
        block = block[:2500]
        assert "test_result.group_error" in block
        assert "test_result.user_error" in block
        assert re.search(r"except Exception:\s*\n\s*pass", block) is None


class TestThePreviewAnswersInsteadOfCrashing:
    def test_both_searches_are_caught(self):
        src = _py_no_comments(SYNC)
        block = src[src.index("async def preview_sync"):]
        block = block[:block.index("def _resolve_members")]
        assert block.count("except Exception") >= 2

    def test_they_are_caught_separately(self):
        """★One working is useful on its own: on the install that reported this,
        users resolved fine and the whole screen died on the group half."""
        src = _py_no_comments(SYNC)
        block = src[src.index("async def preview_sync"):]
        block = block[:block.index("def _resolve_members")]
        g = block.index("search_groups()")
        u = block.index("search_users()")
        between = block[min(g, u):max(g, u)]
        assert "except Exception" in between, "one try wraps both searches"

    def test_the_reason_is_carried_on_the_response(self):
        from app.ee.ldap.schemas import LDAPSyncPreview
        for f in ("groups_read", "group_error", "users_read", "user_error"):
            assert f in LDAPSyncPreview.model_fields, f

    def test_a_clean_preview_still_reports_read(self):
        """★Positive control: the flags default to True, so an ordinary preview
        is unchanged and the modal keeps rendering its summary."""
        from app.ee.ldap.schemas import LDAPSyncPreview
        p = LDAPSyncPreview()
        assert p.groups_read is True and p.users_read is True
        assert p.group_error is None and p.user_error is None

    def test_the_test_result_carries_it_too(self):
        from app.ee.ldap.schemas import LDAPTestResult
        for f in ("group_error", "user_error"):
            assert f in LDAPTestResult.model_fields, f


class TestAFailedSearchNeverLooksLikeAnEmptyDirectory:
    def test_removals_are_only_counted_when_the_directory_was_read(self):
        """★★★The one that matters most. `seen_dns` is empty after a refused
        search, so an unguarded count announces the deletion of every group the
        organization has — from a search that never ran."""
        src = _py_no_comments(SYNC)
        block = src[src.index("async def preview_sync"):]
        block = block[:block.index("def _resolve_members")]
        guard = block.index("preview.groups_read")
        removal = block.index("groups_to_remove += 1")
        assert guard < removal, "the removal count is not gated on groups_read"
        between = block[guard:removal]
        assert "if" in between or "preview.groups_read" in between

    def test_the_sync_still_aborts_rather_than_writing_from_a_half_read(self):
        """★The preview reports what it can see; the SYNC must not. Writing
        groups from a directory whose user search failed resolves every member
        to nobody and empties every group."""
        src = _py_no_comments(SYNC)
        block = src[src.index("async def sync_groups"):]
        block = block[:block.index("async def preview_sync")]
        head = block[:block.index("dn_to_email")]
        assert head.count("return result") >= 2, "a failed search does not abort the sync"


class TestTheScreenActuallyShowsIt:
    """★A fact that reaches the database and not the screen is the same as no
    fact at all. The backend half alone would leave this defect exactly where it
    was: the reason existing, and nobody able to read it."""

    def test_the_modal_renders_the_group_reason(self):
        src = _vue_no_comments()
        assert "ldap-preview-group-error" in src
        assert "preview.group_error" in src

    def test_the_modal_renders_the_user_reason(self):
        src = _vue_no_comments()
        assert "ldap-preview-user-error" in src
        assert "preview.user_error" in src

    def test_the_connection_test_reason_reaches_the_footer(self):
        src = _vue_no_comments()
        assert "ldap-test-search-error" in src
        assert "testResult.group_error" in src

    def test_counts_are_hidden_when_they_were_never_measured(self):
        """Rendering `0 to remove` after a refused search reads as a clean run."""
        src = _vue_no_comments()
        assert 'v-if="preview.groups_read"' in src

    def test_the_connected_and_failed_pair_stay_adjacent(self):
        """★★★I broke exactly this while writing the fix. Slotting a new `v-if`
        between the `v-if="testResult.connected"` line and its `v-else`
        re-parents the `v-else` onto the new condition — so a HEALTHY connection
        with no search error renders "Failed". A defect of precisely the shape
        this change exists to remove, introduced by the change itself."""
        src = _vue_no_comments()
        connected = src.index('v-if="testResult.connected"')
        failed = src.index("statusFailed")
        between = src[connected:failed]
        assert "v-if=" not in between.replace('v-if="testResult.connected"', "", 1) \
            .replace('v-if="testResult.server"', "") \
            .replace('v-if="testResult.vendor"', "") \
            .replace('v-if="testResult.user_count !== null"', "") \
            .replace('v-if="testResult.group_count !== null"', ""), \
            "a v-if sits between the connected branch and its v-else"

    def test_the_types_were_widened_too(self):
        """A field the TypeScript shape does not declare is a field the next
        person deletes as a typo."""
        src = _src(COMPOSABLE)
        for f in ("groups_read", "group_error", "users_read", "user_error"):
            assert f in src, f


class TestEveryMethodItCallsExists:
    """★★★The second defect, and the one none of the tests above could see.

    With the 500 fixed, `preview_sync` still failed — on the very next line:

        AttributeError: 'LDAPGroupSyncService' object has no attribute
        '_get_org_user_map'. Did you mean: '_get_all_user_map'?

    That method has never existed. So the Preview button has never worked, on
    any install, under any configuration, since it was written — and nobody
    knew, because the group search failed FIRST and escaped uncaught, so the
    error the admin saw was always the LDAP one. Fixing the first defect is what
    made the second visible.

    ★Every guard in this file scans source text. Not one of them could catch a
    call to a method that isn't there; only RUNNING it could, and it was a live
    HTTP probe against the baked image that did. This class is the cheapest
    thing that closes that gap without a database: walk the AST and require that
    every `self._x(...)` resolves to a method the class actually defines.
    """

    def _service_class(self):
        tree = ast.parse(_src(SYNC))
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "LDAPGroupSyncService"
        )

    def test_no_call_to_a_method_that_does_not_exist(self):
        cls = self._service_class()
        defined = {
            n.name for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        missing = set()
        for node in ast.walk(cls):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "self"):
                continue
            # Attributes set in __init__ (self.config, self.connection) are not
            # method calls on this class; only `self._helper(...)` is in scope.
            if not fn.attr.startswith("_"):
                continue
            if fn.attr not in defined:
                missing.add(fn.attr)
        assert not missing, f"calls methods that do not exist: {sorted(missing)}"

    def test_the_original_defect_is_still_detected(self):
        """★Carry the red proof IN the test. A check that has only ever been
        shown to pass is a comment with a test's salary — and this one would
        rot into exactly that once the typo is gone from the tree."""
        broken = ast.parse(
            "class LDAPGroupSyncService:\n"
            "    async def _get_all_user_map(self, db): ...\n"
            "    async def preview_sync(self, db, org):\n"
            "        return await self._get_org_user_map(db, org)\n"
        )
        cls = next(n for n in ast.walk(broken) if isinstance(n, ast.ClassDef))
        defined = {
            n.name for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        missing = {
            n.func.attr for n in ast.walk(cls)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
            and n.func.attr.startswith("_") and n.func.attr not in defined
        }
        assert missing == {"_get_org_user_map"}

    def test_the_preview_models_the_run_it_previews(self):
        """★The repair had a wrong-looking easy option. Scoping the map to
        CURRENT org members would under-report exactly the arrivals the sync is
        about to make — the sync creates a Membership for anyone who turns up in
        an LDAP group. A preview that models a narrower run than the run is a
        different lie, not a fix."""
        src = _py_no_comments(SYNC)
        block = src[src.index("async def preview_sync"):]
        block = block[:block.index("def _resolve_members")]
        assert "self._get_all_user_map(db)" in block
