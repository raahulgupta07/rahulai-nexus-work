"""Apply a member's workspace selection to a discovered endpoint list.

Kept apart from the crawl on purpose. The whole risk in workspace scoping is one
three-line branch, and putting it behind a DB session, an OAuth token and a
twenty-workspace network crawl is how it goes untested.

★``None`` and ``[]`` are different answers. See
`app/models/user_data_source_scope.py` for the full statement; the short version
is that reading an empty selection as "no filter" makes deselecting everything
trigger the full crawl — the exact cost the feature removes, at the exact moment
the member asked for none of it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def endpoint_key(endpoint: Dict[str, Any]) -> str:
    """The name a member sees and selects.

    Discovery, the progress `detail` rows and the picker must all agree on this
    string or a selection silently matches nothing. `database` is the lakehouse
    /warehouse name and is what every one of those surfaces already shows.
    """
    return str(endpoint.get("database") or endpoint.get("name") or "")


def select_endpoints(
    endpoints: Sequence[Dict[str, Any]],
    selected: Optional[Sequence[str]],
) -> List[Dict[str, Any]]:
    """Return the endpoints to crawl.

    ``selected is None``  → every endpoint (nobody has chosen; today's behaviour).
    ``selected == []``    → none. The member deselected everything and meant it.
    otherwise             → those whose key is in the selection.
    """
    if selected is None:
        return list(endpoints)
    wanted = {str(s) for s in selected}
    return [e for e in endpoints if endpoint_key(e) in wanted]


def unmatched_selection(
    endpoints: Sequence[Dict[str, Any]],
    selected: Optional[Sequence[str]],
) -> List[str]:
    """Selected names that no longer exist in discovery.

    A workspace can be renamed or a member's access revoked, and a selection
    that quietly matches nothing looks identical to a sync that found nothing.
    The caller reports these so "0 of 3 workspaces" comes with the reason.
    """
    if selected is None:
        return []
    present = {endpoint_key(e) for e in endpoints}
    return sorted({str(s) for s in selected} - present)
