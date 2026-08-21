"""0.0.543.20 — a member can accept suggestions on their own private rule.

Measured on production (2026-08-21, instruction b92ae2b6): every accept of an
AI suggestion on a PRIVATE instruction returned 409 "These changes moved since
you viewed them", and refreshing never helped. The accept path built the patch,
then `_auto_finalize_build` — called WITHOUT `force_publish` — only submitted
it for approval, and the compare-and-swap check that follows demanded the new
build be main. A member can never satisfy that, so the handler misfiled a
permission wall as staleness and threw the reviewer's patch away
(`status = "rejected"`).

The create path already encodes the correct rule: a private instruction is
visible only to its author, so the author's own change publishes immediately
(`force_publish=True`). These tests pin three facts about the fix:

* the accept path computes a force_publish flag from the instruction's privacy
  and ownership and passes it to `_auto_finalize_build`;
* a finalize that legitimately parked the build in pending_approval is
  reported as its own status ("needs_approval") and the build survives for the
  admin — the rejected-stamp is reserved for real CAS losses;
* both accept routes translate that status into an honest message instead of
  the stale one.

AST checks, same reasoning as test_a_review_action_names_what_it_reviewed:
a comment mentioning force_publish must not satisfy them.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SERVICE = REPO / "backend" / "app" / "services" / "instruction_service.py"
ROUTES = REPO / "backend" / "app" / "routes" / "instruction.py"


@pytest.fixture(scope="module")
def service_tree() -> ast.Module:
    return ast.parse(SERVICE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def routes_text() -> str:
    return ROUTES.read_text(encoding="utf-8")


def _method(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from instruction_service.py")


def _calls_of(func: ast.AST, attr: str):
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr):
            yield node


class TestTheServicePublishesAnOwnedPrivateAccept:
    def test_accept_all_passes_force_publish_to_finalize(self, service_tree):
        func = _method(service_tree, "accept_all_hunks")
        calls = list(_calls_of(func, "_auto_finalize_build"))
        assert calls, "accept_all_hunks no longer finalizes its build"
        kw = {k.arg for c in calls for k in c.keywords}
        assert "force_publish" in kw, (
            "accept_all_hunks calls _auto_finalize_build without force_publish "
            "— a member accepting their own private instruction stalls in "
            "pending_approval and is misreported as stale"
        )

    def test_the_flag_is_derived_from_privacy_and_ownership(self, service_tree):
        """The assignment must read both is_private and user_id — publishing
        every accept unconditionally would bypass admin review of shared
        instructions."""
        func = _method(service_tree, "accept_all_hunks")
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "force_publish" for t in node.targets
            ):
                src = ast.unparse(node.value)
                assert "is_private" in src and "user_id" in src
                return
        raise AssertionError("accept_all_hunks never derives force_publish")

    def test_a_parked_build_returns_needs_approval_not_stale(self, service_tree):
        func = _method(service_tree, "accept_all_hunks")
        returned = {
            node.value.elts[1].value
            for node in ast.walk(func)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Tuple)
            and len(node.value.elts) == 2
            and isinstance(node.value.elts[1], ast.Constant)
        }
        assert "needs_approval" in returned, (
            "accept_all_hunks cannot distinguish 'submitted for review' from "
            "'content moved' — the permission wall reads as staleness again"
        )

    def test_the_pending_build_is_kept_for_the_admin(self, service_tree):
        """The needs_approval return must occur BEFORE any status='rejected'
        stamp in source order, so a legitimate submission is never discarded."""
        src = ast.unparse(_method(service_tree, "accept_all_hunks"))
        needs = src.find("needs_approval")
        rejected = src.find("'rejected'")
        assert needs != -1 and rejected != -1
        assert needs < rejected, (
            "the rejected-stamp runs before the needs_approval branch, so a "
            "member's submitted review is thrown away"
        )


class TestTheRoutesSayWhatActuallyHappened:
    @pytest.mark.parametrize("route_marker", [
        '/hunks/accept"', '/hunks/accept-all"',
    ])
    def test_each_accept_route_maps_needs_approval(self, routes_text, route_marker):
        start = routes_text.find(route_marker)
        assert start != -1, f"route {route_marker} is gone"
        # The window from the decorator to the next route definition.
        end = routes_text.find("@router.", start)
        body = routes_text[start:end if end != -1 else None]
        assert 'needs_approval' in body, (
            f"route {route_marker} does not map needs_approval — the honest "
            "message is unreachable and the client sees 'moved since you viewed'"
        )
