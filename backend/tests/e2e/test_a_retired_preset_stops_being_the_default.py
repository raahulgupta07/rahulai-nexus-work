"""A preset whose model id has left the catalog must be stood down.

★Ported behaviour with no upstream test. 0.0.519 replaced every Google preset
because the old ids had been retired — a permanent 404 that no retry or fallback
can heal, which is why the chat surfaced the same error on whichever model the
user picked next. Removing the ids from the catalog is only half the fix: the
sync loop used to skip any row it could not find there, so an existing org kept
its dead preset enabled AND still pinned as the org default. Upstream changed
the loop and shipped no test for the change.

Two things have to hold, and they pull in opposite directions:

  1. a PRESET row whose id left the catalog is disabled and surrenders its
     default flags, so the promotion step can move the org onto a live model
  2. a CUSTOM row is left completely alone — its id is not supposed to be in
     the catalog, and "not in the catalog" is the same signal in both cases

(2) is not hypothetical here. This install runs entirely on custom models
(`x-ai/grok-4.5` is the org default), so a disable branch that keyed off the
catalog alone would switch the whole product off at the next sync.

★What the port actually changes, measured by reverting the branch and rerunning
this file: `is_enabled`, and only that. Upstream's commit says an existing org
"still pinned as the org default" — in our tree the default already moves off a
retired id by another path, so what survives without the branch is a dead model
still OFFERED in the picker rather than a dead default. Only the first test
below discriminates; the second is labelled where it sits.
"""
import asyncio

import pytest

from app.dependencies import async_session_maker
from app.models.llm_model import LLMModel
from app.models.llm_provider import LLMProvider

# Retired for real: Google still lists it, and rejects any project that had not
# already used it. Deliberately a literal, not something derived from the
# catalog — the point is that it is absent from it.
RETIRED = "gemini-2.5-pro"
CURRENT = "gemini-3.6-flash"      # the catalog's Google default after 0.0.519
CUSTOM = "x-ai/some-model-the-catalog-never-heard-of"


def _run(coro):
    return asyncio.run(coro)


async def _seed(org_id):
    async with async_session_maker() as db:
        provider = LLMProvider(
            organization_id=org_id,
            name="Google",
            provider_type="google",
            is_preset=True,
            is_enabled=True,
            use_preset_credentials=True,
        )
        db.add(provider)
        await db.flush()

        # The org as it looks before the port: pinned to a dead id.
        db.add(LLMModel(
            organization_id=org_id, provider_id=provider.id,
            name="Gemini 2.5 Pro", model_id=RETIRED,
            is_preset=True, is_enabled=True, is_default=True,
        ))
        # A live catalog model, so there is somewhere to be promoted to.
        db.add(LLMModel(
            organization_id=org_id, provider_id=provider.id,
            name="Gemini 3.6 Flash", model_id=CURRENT,
            is_preset=True, is_enabled=True, is_default=False,
        ))
        # Not a preset. Not in the catalog. Must survive untouched.
        db.add(LLMModel(
            organization_id=org_id, provider_id=provider.id,
            name="A custom model", model_id=CUSTOM,
            is_preset=False, is_enabled=True, is_default=False,
        ))
        await db.commit()


def _find(models, model_id):
    return next((m for m in models if m["model_id"] == model_id), None)


@pytest.mark.e2e
def test_a_retired_preset_is_disabled_and_gives_up_the_default(
    create_user, login_user, whoami, get_models
):
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]
    _run(_seed(org_id))

    # GET is what re-syncs preset providers against the catalog.
    models = get_models(token, org_id)

    dead = _find(models, RETIRED)
    assert dead is not None, "the row must be KEPT — history and usage records still resolve through it"
    assert dead["is_enabled"] is False, (
        f"{RETIRED} left the catalog and is a permanent 404, but it is still "
        f"offered to users"
    )
    assert dead["is_default"] is False, (
        f"the org is still pinned to {RETIRED}; every question would fail with "
        f"the same error no matter which model the user picks next"
    )


@pytest.mark.e2e
def test_the_catalog_default_is_reachable_after_the_retired_one_stands_down(
    create_user, login_user, whoami, get_models
):
    """★This one passes WITH and WITHOUT the ported branch — measured, twice.

    It is kept anyway, and labelled, because a test that looks like a guard and
    is not is the thing this file is trying to avoid. In our tree the default
    promotion already moves off a retired id by another path, so the only
    behaviour the port actually changes is `is_enabled` — which is what the test
    above pins, and it is the discriminator here. What survives without the
    branch is a dead model still OFFERED in the picker, not a dead default.

    That is a smaller harm than upstream's commit message describes, and worth
    recording rather than repeating their framing as if measured here.

    The assertion below is still worth holding: it fixes the outcome so that a
    future change to promotion cannot strand an org with no usable default and
    have every other test stay green.
    """
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]
    _run(_seed(org_id))

    models = get_models(token, org_id)
    live = _find(models, CURRENT)
    assert live is not None and live["is_enabled"] is True

    # ★Named, not merely "something enabled holds it". The first version of this
    # assertion said `any(m["is_default"] and m["is_enabled"] ...)` and PASSED
    # against the pre-port code — where the dead model was still enabled and
    # still default, which satisfies it perfectly. Measured, not reasoned: with
    # the branch reverted this file gave 1 failed, 2 passed, and this was one of
    # the two. Assert WHICH model holds the default, or the check is about
    # nothing.
    holders = [m["model_id"] for m in models if m["is_default"] and m["is_enabled"]]
    assert RETIRED not in holders, (
        f"{RETIRED} is retired and still holds the default — every question "
        f"fails with the same 404 whichever model the user picks next"
    )
    assert CURRENT in holders, (
        f"the default did not move to the catalog's current model. Holders: "
        f"{holders or 'nothing at all'} — an org with no default is worse than "
        f"the dead pin it replaced"
    )


@pytest.mark.e2e
def test_a_custom_model_is_not_disabled_for_being_absent_from_the_catalog(
    create_user, login_user, whoami, get_models
):
    """★The branch that would take this whole install down.

    A custom model is absent from the catalog by definition. If the disable
    keyed off catalog membership alone rather than on `is_preset`, every custom
    model would switch itself off at the next sync — and this deployment runs
    on nothing else.
    """
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]
    _run(_seed(org_id))

    custom = _find(get_models(token, org_id), CUSTOM)
    assert custom is not None, "the custom model vanished from the listing entirely"
    assert custom["is_enabled"] is True, (
        "a custom model was disabled for not being in the catalog. It is never "
        "in the catalog. Every custom model in the install would go dark."
    )
