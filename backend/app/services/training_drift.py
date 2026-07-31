"""Notice when an agent's overview no longer describes its data.

An agent's overview is its briefing — loaded on every question, naming its
tables and telling the model how to use them. CRM's says, in as many words, to
UNION six named month tables. Remove one and the schema updates; the overview
does not. The agent goes on following a description of data that has moved on,
and nothing anywhere says so: a wrong answer built on a stale briefing looks
exactly like a right one.

Training in this product has only ever followed what you ADD — first run, first
model key, an upload, a per-user sign-in. Nothing watches for change. This
module is the missing half, and it is deliberately the cheap half: it compares a
fingerprint taken at training time against the schema as it stands now. No model
call, no crawl, nothing to schedule.

What counts as drift is a judgement, not a detail:

* a table appearing or disappearing — the overview names tables by name,
* a column appearing, disappearing or changing type — the overview gives
  per-column guidance (CRM's warns that one measure is BIGINT in some months and
  DOUBLE in others, and says to cast before aggregating),
* NOT row counts. The overview describes what the data *is*, not how much of it
  there is. Retraining because a table grew would fire constantly, change the
  text not at all, and teach people that the notice is noise.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODE_MANUAL = "manual"
MODE_NOTIFY = "notify"
MODE_AUTO = "auto"
VALID_MODES = (MODE_MANUAL, MODE_NOTIFY, MODE_AUTO)

# Noticing is free, so it is on by default; re-learning costs a model call every
# time the data moves, so it is not.
DEFAULT_MODE = MODE_NOTIFY


def _column_map(table: Any) -> dict:
    cols = getattr(table, "columns", None) or []
    out = {}
    for c in cols:
        if isinstance(c, dict):
            name, dtype = c.get("name"), c.get("dtype")
        else:
            name, dtype = getattr(c, "name", None), getattr(c, "dtype", None)
        if name:
            out[str(name)] = str(dtype or "")
    return out


def schema_shape(tables) -> dict:
    """The shape a training run read: table name -> {column: dtype}.

    Only ACTIVE tables. An inactive table is one the agent was told not to use,
    so it is absent from the overview and changing it is not drift — flagging it
    would mean an agent could never be current while any unused table existed.
    """
    shape = {}
    for t in tables or []:
        if not getattr(t, "is_active", False):
            continue
        name = getattr(t, "name", None)
        if name:
            shape[str(name)] = _column_map(t)
    return shape


def signature(shape: dict) -> str:
    """A stable fingerprint of a shape.

    Sorted keys, so two runs over the same schema in a different order agree —
    otherwise every training would look like drift and the notice would be
    permanent.
    """
    return hashlib.sha256(
        json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def encode(shape: dict) -> str:
    """Stored form: the shape itself, not just its hash.

    A hash alone can answer "has this changed?" but not "what changed?", and the
    difference between those two is the difference between a warning people act
    on and one they dismiss.
    """
    return json.dumps({"v": 1, "sig": signature(shape), "shape": shape},
                      sort_keys=True, separators=(",", ":"))


def decode(stored: Optional[str]) -> Optional[dict]:
    if not stored:
        return None
    try:
        payload = json.loads(stored)
    except (TypeError, ValueError):
        return None
    shape = payload.get("shape") if isinstance(payload, dict) else None
    return shape if isinstance(shape, dict) else None


def diff(trained: Optional[dict], current: dict) -> dict:
    """What has changed since training, in the terms the user thinks in.

    Returns ``known: False`` when there is nothing to compare against, which is
    NOT the same as "no drift" and must not be presented as up to date. Every
    agent predating this feature is in that state.
    """
    if trained is None:
        return {"known": False, "stale": False, "tables_added": [], "tables_removed": [],
                "columns_added": [], "columns_removed": [], "columns_retyped": []}

    tables_added = sorted(set(current) - set(trained))
    tables_removed = sorted(set(trained) - set(current))
    columns_added, columns_removed, columns_retyped = [], [], []

    for name in sorted(set(trained) & set(current)):
        before, after = trained[name] or {}, current[name] or {}
        for col in sorted(set(after) - set(before)):
            columns_added.append(f"{name}.{col}")
        for col in sorted(set(before) - set(after)):
            columns_removed.append(f"{name}.{col}")
        for col in sorted(set(before) & set(after)):
            if before[col] != after[col]:
                # A measure that was an integer last month and a float this one
                # is exactly what the overview warns about; a silent retype
                # makes that warning wrong.
                columns_retyped.append(f"{name}.{col} {before[col]}→{after[col]}")

    stale = bool(tables_added or tables_removed or columns_added
                 or columns_removed or columns_retyped)
    return {"known": True, "stale": stale, "tables_added": tables_added,
            "tables_removed": tables_removed, "columns_added": columns_added,
            "columns_removed": columns_removed, "columns_retyped": columns_retyped}


def summarize(d: dict) -> str:
    """One line a person can act on, or "" when there is nothing to say."""
    if not d.get("stale"):
        return ""
    parts = []
    def add(n, one, many):
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    add(len(d["tables_removed"]), "table removed", "tables removed")
    add(len(d["tables_added"]), "table added", "tables added")
    add(len(d["columns_removed"]), "column removed", "columns removed")
    add(len(d["columns_added"]), "column added", "columns added")
    add(len(d["columns_retyped"]), "column changed type", "columns changed type")
    return " · ".join(parts)


def mode_of(data_source) -> str:
    settings = getattr(data_source, "training_settings", None)
    if isinstance(settings, dict):
        mode = settings.get("mode")
        if mode in VALID_MODES:
            return mode
    return DEFAULT_MODE


async def record_trained(db, data_source, tables) -> None:
    """Remember what this training read. Best-effort — a failure here costs the
    drift notice, never the training that just succeeded."""
    try:
        shape = schema_shape(tables)
        data_source.trained_schema_signature = encode(shape)
        data_source.trained_at = datetime.utcnow()
        db.add(data_source)
        await db.commit()
        logger.info(
            f"training: recorded {len(shape)} table(s) for data source {data_source.id}"
        )
    except Exception as err:
        logger.warning(f"could not record trained schema for {data_source.id}: {err}")


def drift_for(data_source, tables) -> dict:
    """The full status the agent page shows."""
    current = schema_shape(tables)
    d = diff(decode(getattr(data_source, "trained_schema_signature", None)), current)
    trained_at = getattr(data_source, "trained_at", None)
    return {
        **d,
        "mode": mode_of(data_source),
        "summary": summarize(d),
        "trained_at": trained_at.isoformat() if trained_at else None,
        "active_tables": len(current),
    }


# ── auto mode ───────────────────────────────────────────────────────────────
# Re-learning without being asked is the one part of this that spends money, so
# it carries every guard the manual path does not need.

# How long the schema must sit still before an automatic re-learn. A migration
# that adds nine columns arrives as nine separate changes seconds apart; without
# a quiet period that is nine model calls to describe one edit.
DEFAULT_QUIET_MINUTES = 30

# A ceiling, so a connector that rewrites its schema in a loop cannot spend all
# day. Reached, it stops and says so rather than falling back to silence.
DEFAULT_MAX_PER_DAY = 4


def _settings(data_source) -> dict:
    s = getattr(data_source, "training_settings", None)
    return dict(s) if isinstance(s, dict) else {}


def quiet_minutes(data_source) -> int:
    try:
        return max(0, int(_settings(data_source).get("quiet_minutes", DEFAULT_QUIET_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_QUIET_MINUTES


def max_per_day(data_source) -> int:
    try:
        return max(0, int(_settings(data_source).get("max_per_day", DEFAULT_MAX_PER_DAY)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_DAY


def auto_decision(data_source, tables, now: datetime) -> dict:
    """Should this agent re-learn itself right now, and if not, why not?

    Returns a reason in every case rather than a bare boolean: a sweep that
    skips silently is indistinguishable from one that never ran, which is the
    failure this whole session kept turning up.
    """
    if mode_of(data_source) != MODE_AUTO:
        return {"run": False, "reason": "not in auto mode"}

    status = drift_for(data_source, tables)
    if not status["known"]:
        # Never trained by a version that recorded a fingerprint. There is
        # nothing to compare against, so there is no evidence of drift — and
        # spending a model call on an agent that may be perfectly current is not
        # something to do unasked.
        return {"run": False, "reason": "nothing recorded to compare against"}
    if not status["stale"]:
        return {"run": False, "reason": "up to date"}

    cfg = _settings(data_source)
    today = now.date().isoformat()
    if cfg.get("auto_day") == today and int(cfg.get("auto_runs", 0) or 0) >= max_per_day(data_source):
        return {"run": False, "reason": "daily limit reached", "summary": status["summary"]}

    # The quiet period is measured from when THIS shape was first seen, and the
    # marker is keyed to the shape itself. So a further change restarts the
    # clock instead of letting a half-finished migration be described.
    current_sig = signature(schema_shape(tables))
    seen_sig, seen_at = cfg.get("drift_sig"), cfg.get("drift_seen_at")
    if seen_sig != current_sig or not seen_at:
        return {"run": False, "reason": "waiting for the schema to settle",
                "mark": {"drift_sig": current_sig, "drift_seen_at": now.isoformat()},
                "summary": status["summary"]}

    try:
        waited = (now - datetime.fromisoformat(seen_at)).total_seconds() / 60
    except (TypeError, ValueError):
        return {"run": False, "reason": "waiting for the schema to settle",
                "mark": {"drift_sig": current_sig, "drift_seen_at": now.isoformat()}}

    if waited < quiet_minutes(data_source):
        return {"run": False, "reason": f"settling ({int(waited)}m of {quiet_minutes(data_source)}m)",
                "summary": status["summary"]}

    return {"run": True, "reason": "data changed and has settled", "summary": status["summary"]}


def note_auto_run(data_source, now: datetime) -> dict:
    """The settings blob after an automatic run, with the day's counter rolled.

    Returned rather than assigned so the caller does the write — SQLAlchemy does
    not track in-place edits to a JSON column, and a mutation here would be
    silently dropped at commit.
    """
    cfg = _settings(data_source)
    today = now.date().isoformat()
    cfg["auto_runs"] = (int(cfg.get("auto_runs", 0) or 0) + 1) if cfg.get("auto_day") == today else 1
    cfg["auto_day"] = today
    cfg["last_auto_at"] = now.isoformat()
    # The drift marker is spent — the next change starts its own quiet period.
    cfg.pop("drift_sig", None)
    cfg.pop("drift_seen_at", None)
    return cfg
