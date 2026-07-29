"""Runtime-configurable product branding — pure-logic tests, no database.

WHAT THIS LOCKS DOWN
--------------------
1. Defaults equal today's visible strings. An installation that never opens the
   branding screen must be byte-identical to one that predates the feature, so
   the default values are asserted literally rather than by reference.
2. A partial update MERGES. A PUT carrying only `product_name` must not blank
   the tagline — that is the single easiest way to ship this feature broken.
3. Validation refuses an empty name and a malformed colour.
4. The public feed carries EXACTLY the six branding keys. That endpoint is
   unauthenticated, so a seventh key is a disclosure, not a convenience.

These use no session and no schema, which is why they belong in this
directory. The route-level permission gate and the on-disk upload are exercised
by their own paths; what is testable without a database is the contract.
"""
from __future__ import annotations

import pytest

from app.schemas.branding_schema import (
    BRANDING_KEYS,
    DEFAULT_BRANDING,
    BrandingSchema,
    BrandingUpdate,
    branding_from_stored,
    merge_branding,
)


# ── 1. Defaults ────────────────────────────────────────────────────────────

def test_defaults_are_todays_visible_strings():
    """★ These literals are the whole safety property. If someone changes a
    default, every existing installation is silently rebranded — this test is
    the thing that makes that a deliberate act rather than an accident."""
    b = BrandingSchema()
    assert b.product_name == "CityAgent Insights"
    assert b.tagline == "Your AI analyst for data"
    assert b.footer_text == ""
    assert b.accent_color == "#2563eb"
    assert b.logo_key is None
    assert b.favicon_key is None


def test_nothing_stored_yields_full_defaults():
    for stored in (None, {}, []):
        b = branding_from_stored(stored)
        assert b.model_dump() == DEFAULT_BRANDING


def test_partial_stored_block_fills_the_rest():
    b = branding_from_stored({"product_name": "Acme Analytics"})
    assert b.product_name == "Acme Analytics"
    assert b.tagline == DEFAULT_BRANDING["tagline"]
    assert b.accent_color == DEFAULT_BRANDING["accent_color"]


def test_corrupt_stored_block_degrades_to_defaults_and_never_raises():
    # The unauthenticated settings feed reads this. A bad stored value must not
    # be able to take the sign-in page down.
    b = branding_from_stored({"product_name": "", "accent_color": "not-a-colour"})
    assert b.model_dump() == DEFAULT_BRANDING


def test_unknown_stored_keys_are_dropped():
    b = branding_from_stored({"product_name": "Acme", "api_secret": "hunter2"})
    assert set(b.model_dump()) == set(BRANDING_KEYS)


# ── 2. Partial update merges ───────────────────────────────────────────────

def test_updating_only_the_name_leaves_everything_else_alone():
    stored = {
        "product_name": "Old Name",
        "tagline": "Old tagline",
        "footer_text": "Old footer",
        "accent_color": "#ff0000",
        "logo_key": "instance-logo-abc.png",
        "favicon_key": "instance-favicon-def.ico",
    }
    merged = merge_branding(stored, BrandingUpdate(product_name="New Name"))

    assert merged["product_name"] == "New Name"
    assert merged["tagline"] == "Old tagline"
    assert merged["footer_text"] == "Old footer"
    assert merged["accent_color"] == "#ff0000"
    assert merged["logo_key"] == "instance-logo-abc.png"
    assert merged["favicon_key"] == "instance-favicon-def.ico"


def test_update_onto_nothing_stored_keeps_defaults_for_untouched_fields():
    merged = merge_branding(None, BrandingUpdate(product_name="Acme"))
    assert merged["product_name"] == "Acme"
    assert merged["tagline"] == DEFAULT_BRANDING["tagline"]


def test_empty_update_is_a_no_op():
    stored = dict(DEFAULT_BRANDING, product_name="Acme", tagline="Ship faster")
    assert merge_branding(stored, BrandingUpdate()) == stored


def test_footer_can_be_cleared_to_empty_string():
    # "" is a real value here (no footer), which is exactly why the "leave it
    # alone" signal has to be None rather than falsiness.
    stored = dict(DEFAULT_BRANDING, footer_text="Old footer")
    merged = merge_branding(stored, BrandingUpdate(footer_text=""))
    assert merged["footer_text"] == ""


def test_explicit_null_removes_an_uploaded_image():
    stored = dict(DEFAULT_BRANDING, logo_key="instance-logo-abc.png")
    merged = merge_branding(stored, BrandingUpdate(logo_key=None))
    assert merged["logo_key"] is None


def test_omitting_the_logo_key_does_not_remove_the_logo():
    # The distinction the test above depends on: `exclude_unset` is what
    # separates "sent null" from "did not send the field".
    stored = dict(DEFAULT_BRANDING, logo_key="instance-logo-abc.png")
    merged = merge_branding(stored, BrandingUpdate(product_name="Acme"))
    assert merged["logo_key"] == "instance-logo-abc.png"


def test_merge_result_carries_exactly_the_contract_keys():
    merged = merge_branding({"legacy": "junk"}, BrandingUpdate(product_name="Acme"))
    assert set(merged) == set(BRANDING_KEYS)


# ── 3. Validation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_empty_product_name_is_refused(blank):
    with pytest.raises(Exception):
        BrandingUpdate(product_name=blank)
    with pytest.raises(Exception):
        BrandingSchema(product_name=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_tagline_is_refused(blank):
    with pytest.raises(Exception):
        BrandingUpdate(tagline=blank)


def test_names_are_stripped():
    assert BrandingUpdate(product_name="  Acme  ").product_name == "Acme"


@pytest.mark.parametrize(
    "bad",
    ["2563eb", "#25f", "#2563e", "#2563ebb", "blue", "rgb(1,2,3)", "#12345g", ""],
)
def test_malformed_accent_colour_is_refused(bad):
    with pytest.raises(Exception):
        BrandingUpdate(accent_color=bad)
    with pytest.raises(Exception):
        BrandingSchema(accent_color=bad)


@pytest.mark.parametrize("good", ["#2563eb", "#FFFFFF", "  #AbCdEf  "])
def test_valid_accent_colour_is_accepted_and_normalised(good):
    assert BrandingUpdate(accent_color=good).accent_color == good.strip().lower()


def test_over_length_values_are_refused():
    with pytest.raises(Exception):
        BrandingUpdate(product_name="x" * 500)
    with pytest.raises(Exception):
        BrandingUpdate(tagline="x" * 500)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.png", "..", "x\\y.png"])
def test_traversal_shaped_upload_keys_are_refused(bad):
    # The key is interpolated into a URL and resolved against a directory by
    # the icon route. That route guards itself; this refuses the value earlier.
    with pytest.raises(Exception):
        BrandingUpdate(logo_key=bad)


# ── 4. The public feed exposes exactly six keys ────────────────────────────

def test_public_payload_has_exactly_the_six_keys_and_no_more():
    """The shape `/api/settings` republishes. That endpoint is unauthenticated
    by design, so this asserts the key set exactly — a new field added to
    BrandingSchema without thinking about disclosure fails here."""
    payload = BrandingSchema().model_dump()
    assert set(payload) == {
        "product_name",
        "tagline",
        "footer_text",
        "accent_color",
        "logo_key",
        "favicon_key",
    }
    assert len(payload) == 6


def test_public_payload_is_always_fully_populated():
    # Consumers must never have to handle a missing key, so every key is
    # present even when nothing has ever been stored.
    payload = branding_from_stored(None).model_dump()
    for key in BRANDING_KEYS:
        assert key in payload
    assert payload["product_name"]  # non-empty
    assert payload["tagline"]


def test_feed_payload_never_leaks_a_stored_secret():
    payload = branding_from_stored(
        {"product_name": "Acme", "client_secret_enc": "gAAAAAsecret"}
    ).model_dump()
    assert "client_secret_enc" not in payload
    assert "gAAAAAsecret" not in str(payload)


# ── 5. The SERVICE merges, not just the helper ─────────────────────────────
#
# The helper tests above prove `merge_branding`. These prove the service
# actually calls it — which is where a "replace instead of merge" bug would
# really live. A fake session stands in for the database: the service's only
# database interaction is `InstanceSettings.get_or_create` plus add/commit/
# refresh, so faking those exercises the real merge logic without a schema.


class _FakeInstanceSettings:
    def __init__(self, config=None):
        self.id = "instance-settings-1"
        self.config = config


class _FakeSession:
    def __init__(self):
        self.commits = 0

    def add(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


@pytest.fixture
def branding_service(monkeypatch):
    """BrandingService wired to an in-memory singleton row."""
    from app.services import branding_service as mod

    row = _FakeInstanceSettings()

    async def _get_or_create(cls, db):
        return row

    monkeypatch.setattr(
        mod.InstanceSettings, "get_or_create", classmethod(_get_or_create)
    )
    # flag_modified expects a real SQLAlchemy instance; the fake row is not one.
    monkeypatch.setattr(mod, "flag_modified", lambda obj, key: None)
    return mod.BrandingService(), row


@pytest.mark.asyncio
async def test_service_returns_defaults_when_nothing_is_stored(branding_service):
    service, row = branding_service
    result = await service.get_branding(_FakeSession())
    assert result.model_dump() == DEFAULT_BRANDING


@pytest.mark.asyncio
async def test_service_partial_update_merges(branding_service):
    """★ The one that matters: a PUT of only `product_name` must not blank the
    tagline, the colour or the uploaded logo."""
    service, row = branding_service
    db = _FakeSession()

    await service.update_branding(
        db,
        BrandingUpdate(
            product_name="Acme Analytics",
            tagline="Ship faster",
            accent_color="#ff0000",
            logo_key="instance-logo-abc.png",
        ),
    )
    after = await service.update_branding(db, BrandingUpdate(product_name="Acme BI"))

    assert after.product_name == "Acme BI"
    assert after.tagline == "Ship faster"
    assert after.accent_color == "#ff0000"
    assert after.logo_key == "instance-logo-abc.png"


@pytest.mark.asyncio
async def test_service_writes_only_the_branding_key(branding_service):
    """Other InstanceSettings config (SSO lives in the same JSON blob) must
    survive a branding write untouched."""
    service, row = branding_service
    row.config = {"auth_mode": "hybrid", "google": {"enabled": True}}

    await service.update_branding(_FakeSession(), BrandingUpdate(product_name="Acme"))

    assert row.config["auth_mode"] == "hybrid"
    assert row.config["google"] == {"enabled": True}
    assert set(row.config["branding"]) == set(BRANDING_KEYS)


@pytest.mark.asyncio
async def test_service_read_refreshes_the_synchronous_product_name(branding_service):
    from app.services import branding_service as mod

    service, row = branding_service
    saved = mod._cached_product_name
    try:
        await service.update_branding(_FakeSession(), BrandingUpdate(product_name="Acme"))
        assert mod.product_name() == "Acme"
    finally:
        mod._cached_product_name = saved


# ── 6. The synchronous product-name accessor ───────────────────────────────

def test_product_name_defaults_to_todays_string_before_any_read():
    from app.services import branding_service

    # Whatever the cache currently holds, an unset/blank value must degrade to
    # the packaged default rather than to an empty product name.
    saved = branding_service._cached_product_name
    try:
        branding_service._cached_product_name = ""
        assert branding_service.product_name() == "CityAgent Insights"
    finally:
        branding_service._cached_product_name = saved


def test_product_name_reflects_a_refreshed_value():
    from app.services import branding_service

    saved = branding_service._cached_product_name
    try:
        branding_service._remember_product_name("Acme Analytics")
        assert branding_service.product_name() == "Acme Analytics"
    finally:
        branding_service._cached_product_name = saved


def test_email_subject_fallback_uses_the_configured_name():
    """The one that proves the wiring, not just the accessor: a message built
    with no subject carries the configured product name."""
    from app.services import branding_service
    from app.services.email.message_builder import _normalize_subject

    saved = branding_service._cached_product_name
    try:
        branding_service._remember_product_name("Acme Analytics")
        assert _normalize_subject(None, is_reply=False) == "Acme Analytics"
        assert _normalize_subject("", is_reply=True) == "Re: Acme Analytics"
        # An explicit subject is untouched.
        assert _normalize_subject("Weekly report", is_reply=False) == "Weekly report"
    finally:
        branding_service._cached_product_name = saved


def test_notification_email_footer_carries_the_configured_name():
    """Footers say "Sent via <product>" in every locale. The translated prose
    around the name must survive the swap."""
    from app.services import branding_service
    from app.schemas.notification_schema import NotificationType
    from app.services.email_strings import strings_for

    saved = branding_service._cached_product_name
    try:
        branding_service._remember_product_name("Acme Analytics")
        en = strings_for("en", NotificationType.SHARE_DASHBOARD)
        es = strings_for("es", NotificationType.SHARE_DASHBOARD)
        assert en["footer"] == "Sent via Acme Analytics"
        assert es["footer"] == "Enviado desde Acme Analytics"
        # Non-branding strings are untouched.
        assert "{report_title}" in en["subject"]
    finally:
        branding_service._cached_product_name = saved


def test_default_branding_leaves_email_strings_byte_identical():
    """An installation that never touches branding must be unchanged — same
    object returned, not merely an equal copy."""
    from app.services import branding_service, email_strings
    from app.schemas.notification_schema import NotificationType

    saved = branding_service._cached_product_name
    try:
        branding_service._remember_product_name("CityAgent Insights")
        got = email_strings.strings_for("en", NotificationType.SHARE_DASHBOARD)
        assert got is email_strings.STRINGS["en"][NotificationType.SHARE_DASHBOARD]
    finally:
        branding_service._cached_product_name = saved


def test_external_platform_from_name_resolves_at_instantiation():
    """★ A plain field default is evaluated at import, which is exactly what
    stops it seeing runtime branding. This asserts the factory behaviour."""
    from app.services import branding_service
    from app.schemas.external_platform_schema import EmailConfig

    saved = branding_service._cached_product_name
    try:
        branding_service._remember_product_name("Acme Analytics")
        assert EmailConfig().from_name == "Acme Analytics Analyst"
        branding_service._remember_product_name("CityAgent Insights")
        assert EmailConfig().from_name == "CityAgent Insights Analyst"
    finally:
        branding_service._cached_product_name = saved
