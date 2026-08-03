"""Read and write one member's workspace selection for a federated source.

Thin on purpose — the decision logic lives in
`app/services/endpoint_selection.py`, where it can be tested without a database.
This module only moves the value between the row and the caller, preserving the
one distinction that matters: NULL (never chosen) is not [] (chose nothing).
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_data_source_scope import UserDataSourceScope


logger = logging.getLogger(__name__)


async def get_selected_endpoints(
    db: AsyncSession, data_source_id: str, user_id: str,
) -> Optional[List[str]]:
    """The member's selection, or None for "never chosen — sync everything".

    ★Returns None on ANY failure, which means the full crawl. That is the
    slower answer, and it is the right default: the alternative on a read error
    is to sync a subset the member never asked for and report it as complete.
    Wrong-and-slow is recoverable; wrong-and-confident is not.
    """
    try:
        row = (await db.execute(
            select(UserDataSourceScope).where(
                UserDataSourceScope.data_source_id == str(data_source_id),
                UserDataSourceScope.user_id == str(user_id),
            )
        )).scalars().first()
    except Exception:
        logger.warning("user_scope.read_failed", exc_info=True)
        return None
    if row is None:
        return None
    value = row.selected_endpoints
    if value is None:
        return None
    if not isinstance(value, list):
        # A malformed blob is not a selection. Same reasoning as above.
        logger.warning(
            "user_scope.malformed",
            extra={"data_source_id": str(data_source_id), "user_id": str(user_id)},
        )
        return None
    return [str(v) for v in value]


async def set_selected_endpoints(
    db: AsyncSession,
    data_source_id: str,
    user_id: str,
    selected: Optional[Sequence[str]],
) -> Optional[List[str]]:
    """Store a selection. ``None`` clears it back to "sync everything"."""
    row = (await db.execute(
        select(UserDataSourceScope).where(
            UserDataSourceScope.data_source_id == str(data_source_id),
            UserDataSourceScope.user_id == str(user_id),
        )
    )).scalars().first()

    # Deduplicate but keep the member's order — the picker lists them back in
    # the order they were chosen, and a set would reshuffle that on every save.
    normalized: Optional[List[str]] = None
    if selected is not None:
        seen: set[str] = set()
        normalized = []
        for s in selected:
            text = str(s)
            if text and text not in seen:
                seen.add(text)
                normalized.append(text)

    if row is None:
        row = UserDataSourceScope(
            data_source_id=str(data_source_id),
            user_id=str(user_id),
            selected_endpoints=normalized,
        )
        db.add(row)
    else:
        row.selected_endpoints = normalized
    await db.commit()
    return normalized
