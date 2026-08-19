"""Arriving by the directory then by single sign-on must not lock you out.

WHAT THIS COST
--------------
Measured on the dev install, 2026-08-19. Staff provisioned from the company
directory, signing in through the company identity provider, were refused —
permanently — with:

    An account already exists for this email address, and we could not prove it
    belongs to you.

The SAME person arriving in the opposite order was linked with no check at all.
One journey, two code paths:

    SSO first   no account exists -> the CREATE path, which never reads
                `account_email_verified`, stamps is_verified=True and links.
    LDAP first  an account exists -> the LINK path, which refuses unless the
                provider vouches.

So the check fired on exactly one of the two orders, and it fired on the safer
one: a directory-provisioned account was built from a directory entry keyed on
that directory's own mail attribute, while the account the other path links to
was built from the very claim being distrusted.

★★★The refusal exists for one attack, and every step of it begins the same way:
a stranger registers victim@corp.com here before the victim ever signs in. On an
installation that does not admit uninvited sign-ups, that first step is
impossible — `on_after_register` answers "Sign-up is disabled. Ask your admin for
an invite." — so the refusal defends against nothing and costs a real employee
their second way in.

WHAT IS PINNED HERE
-------------------
  * a directory-provisioned account may be linked without provider proof
  * so may any account on an installation that forbids uninvited sign-up
  * ★an installation that ALLOWS uninvited sign-up still REFUSES a plain local
    account — this is the assertion that keeps the change scoped rather than a
    removal, and it is the one to read first if this file is ever edited
  * the reason is returned and recorded, so a link granted on policy is never
    indistinguishable from one the provider proved
  * an unreadable policy is not permission

★These test the decision, not the transport: `oauth_callback` needs a database,
a session and a live provider. `scripts/dev-identity/login-matrix.py` drives the
whole thing against real OpenLDAP and real Keycloak, including both orders.
"""
import inspect
import types

import pytest

from app.core.auth import UserManager
from app.settings.config import settings

decide = UserManager._existing_account_is_not_a_stranger


def _user(**kw):
    base = dict(ldap_dn=None, scim_external_id=None, is_verified=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def signups(monkeypatch):
    """Set the installation's sign-up policy for one test, and put it back."""
    features = settings.dash_config.features

    def _set(allowed: bool):
        monkeypatch.setattr(features, "allow_uninvited_signups", allowed, raising=False)

    return _set


# --- the defect --------------------------------------------------------------


def test_a_directory_account_may_be_linked_without_provider_proof(signups):
    """The reported case: provisioned from the directory, refused at SSO."""
    signups(False)
    reason = decide(_user(ldap_dn="CN=Rahul Gupta,OU=Users,OU=IT,DC=chl,DC=local"))

    assert reason, (
        "a directory-provisioned account was treated as possibly a stranger's — "
        "it was created from a directory entry and cannot be self-registered"
    )
    assert "directory" in reason


def test_an_account_may_be_linked_where_nobody_can_sign_themselves_up(signups):
    """★No door for a stranger means no stranger. Covers the admin-created
    local accounts, which are neither directory rows nor already linked."""
    signups(False)
    reason = decide(_user())

    assert reason, "an installation with no sign-up route still refused the link"
    assert "uninvited" in reason


def test_a_scim_provisioned_account_counts_too(signups):
    signups(False)
    assert decide(_user(scim_external_id="ext-9911"))


# --- what must NOT change: the attack is real where the door exists ----------


def test_an_open_signup_install_still_refuses_a_plain_local_account(signups):
    """★★★THE ASSERTION THAT KEEPS THIS SCOPED. Where anyone can register an
    address before its owner arrives, the squatted row is real and linking to it
    is the takeover. Read this first if the rule is ever widened.

    It is also a positive control: a change that simply always returned a reason
    would pass every other test in this file and fail here.
    """
    signups(True)
    assert decide(_user()) is None, (
        "an installation that admits uninvited sign-ups allowed a link to an "
        "account that may have been registered by a stranger — this is "
        "CVE-2026-53516 / nOAuth"
    )


def test_a_directory_account_is_safe_even_on_an_open_signup_install(signups):
    """A directory row cannot be self-registered whatever the sign-up policy is,
    so the two reasons are independent rather than one gating the other."""
    signups(True)
    assert decide(_user(ldap_dn="uid=x,dc=y"))


def test_the_policy_is_read_live_not_captured(signups):
    """★The whole point of reading the installation's own policy: turning
    self-registration on must re-arm the check by itself, in the same moment the
    attack becomes possible. A value captured at import would leave the product
    in the unsafe combination with nothing to show for it."""
    signups(False)
    assert decide(_user()) is not None
    signups(True)
    assert decide(_user()) is None


def test_an_unreadable_policy_is_not_permission(monkeypatch):
    """★Fail CLOSED. A config read that throws must not be mistaken for a
    configuration of trust."""
    class _Explodes:
        @property
        def features(self):
            raise RuntimeError("config unavailable")

    import app.core.auth as auth_module
    monkeypatch.setattr(auth_module.settings, "dash_config", _Explodes())

    assert decide(_user()) is None


# --- the shape of the answer -------------------------------------------------


def test_the_answer_is_a_reason_not_a_boolean():
    """★It is logged. "True" tells an operator nothing; the sentence tells them
    whether the link rested on the directory or on a sign-up policy that may be
    changed tomorrow."""
    reason = decide(_user(ldap_dn="uid=x,dc=y"))
    assert isinstance(reason, str) and len(reason.split()) > 2


def test_a_link_without_provider_proof_is_recorded(signups):
    """★A link the provider did not prove must not look identical in the log to
    one it did — only the first changes meaning when the policy changes."""
    source = inspect.getsource(UserManager.oauth_callback)

    assert "without provider" in source, (
        "nothing distinguishes a link granted on policy from one the identity "
        "provider vouched for"
    )
    assert "trusted_because" in source


def test_the_gate_still_requires_our_own_side(signups):
    """★The local half is untouched: `user.is_verified` remains part of the
    condition. This change is about the PROVIDER's half."""
    source = inspect.getsource(UserManager.oauth_callback)
    assert "user.is_verified" in source
    assert "account_email_verified is True or trusted_because" in source


def test_the_rule_reads_markers_rather_than_deriving_them():
    """★`is_verified` is stamped True on every account when `verify_emails` is
    off, so it proves nothing and must not be what this rule leans on."""
    source = inspect.getsource(UserManager._existing_account_is_not_a_stranger)

    assert "ldap_dn" in source and "scim_external_id" in source
    assert "allow_uninvited_signups" in source
    assert "is_verified" not in source.split('"""')[-1], (
        "the rule leans on is_verified, which is True for every account on an "
        "installation with email verification off"
    )


def test_the_rule_that_was_removed_is_still_detected(signups):
    """★Carry the red proof IN the test.

    Run against the previous release these assertions fail at IMPORT — the
    helper does not exist — which proves only that, and nothing about whether
    they detect the defect. A red proof done once at a shell prompt rots into a
    comment; one that runs every time cannot.

    So the pre-fix condition is reconstructed here and required to still produce
    the reported outcome: a directory-provisioned employee, signing in through
    the company identity provider, refused.
    """
    def _pre_fix(user, idp_verified):
        # core/auth.py, verbatim in effect, before this change.
        return bool(user.is_verified and idp_verified is True)

    signups(False)
    employee = _user(ldap_dn="CN=Rahul Gupta,OU=Users,OU=IT,DC=chl,DC=local")

    assert _pre_fix(employee, False) is False, (
        "the reconstruction no longer reproduces the defect, so this file has "
        "stopped proving anything"
    )
    assert _pre_fix(employee, True) is True, "the ordinary case must still pass"

    # And the same employee, same unverified provider, through the shipped rule.
    assert decide(employee), "the fix does not admit the case it was written for"

    # ★The squatter is still refused by BOTH, which is what makes the change a
    # narrowing rather than a removal.
    signups(True)
    assert _pre_fix(_user(), False) is False
    assert decide(_user()) is None
