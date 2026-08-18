"""Keyless web search.

★Why this exists at all. The product already ships web search — but only the
provider-executed kind: OpenAI's Responses API `{"type": "web_search"}` server
tool, switched on with `enable_web_search` on the provider. `app/ai/llm/llm.py`
only ever passes that flag on the `openai` (no custom base_url) and `azure`
(`use_responses_api`) branches. A `custom` provider — which is what OpenRouter,
Ollama and every OpenAI-compatible gateway are — falls through to plain Chat
Completions, where the tool does not exist. So for anyone not holding an OpenAI
key the checkbox is inert, and the agent simply cannot search the web.

★This tool needs no API key and no account. It reads DuckDuckGo's HTML-only
endpoint, which returns ordinary markup rather than JSON from a metered API.

★It deliberately reuses the EXISTING org setting `enable_web_fetch` rather than
adding a second toggle. Both tools grant the same thing — the agent reaching the
public internet — and an admin who has decided that question once should not
have to decide it twice, in two places, with the chance of them disagreeing.

★It returns `{query, sources: [{title, url}]}` because that is exactly what
`frontend/components/tools/WebSearchTool.vue` already renders. Matching the
existing shape means this arrives with a finished UI instead of a raw JSON dump.

★Search gives you titles, links and one-line summaries — never page contents.
The description below says so plainly, because a model that believes otherwise
will answer from snippets instead of following the link with `web_fetch`.
"""

import logging
from typing import Any, AsyncIterator, Dict, List, Type
from urllib.parse import urlparse, parse_qs, unquote

from curl_cffi import requests as cf_requests
from curl_cffi.requests.exceptions import RequestException, Timeout
from pydantic import BaseModel
from selectolax.parser import HTMLParser

from app.ai.tools.base import Tool
from app.ai.tools.metadata import ToolMetadata
from app.ai.tools.schemas.web_search import (
    WebSearchInput,
    WebSearchOutput,
    WebSearchResult,
)
from app.ai.tools.schemas import (
    ToolEvent,
    ToolStartEvent,
    ToolProgressEvent,
    ToolEndEvent,
)
from app.ee.audit.tool_audit import log_tool_audit

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = "https://lite.duckduckgo.com/lite/"
REQUEST_TIMEOUT_SECONDS = 20
IMPERSONATE_PROFILE = "chrome131"
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 500


def _clean_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo's redirector.

    ★Results come back as `//duckduckgo.com/l/?uddg=<percent-encoded target>`.
    Handing that to the model would be handing it a link that says nothing about
    where it goes, and would defeat the host allow-listing anyone layers on top.
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except ValueError:
        return ""
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            href = unquote(target[0])
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def _parse_results(html: str, limit: int) -> List[WebSearchResult]:
    """Pull (title, url, snippet) triples out of the lite HTML.

    ★The lite page is a flat <table> of rows, not nested result cards: a link
    row is followed by its snippet row. So the snippet is matched by ORDER, not
    by containment — walking the anchors alone loses every summary line.
    """
    tree = HTMLParser(html)
    results: List[WebSearchResult] = []
    seen: set[str] = set()

    snippets = [
        (node.text() or "").strip()
        for node in tree.css(".result-snippet")
    ]

    for index, anchor in enumerate(tree.css("a.result-link")):
        url = _clean_ddg_url(anchor.attributes.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        title = (anchor.text() or "").strip()[:MAX_TITLE_CHARS]
        snippet = snippets[index][:MAX_SNIPPET_CHARS] if index < len(snippets) else None
        results.append(WebSearchResult(title=title or None, url=url, snippet=snippet or None))
        if len(results) >= limit:
            break

    if results:
        return results

    # ★Fallback for when the markup shifts. A scrape has exactly one failure
    # mode worth planning for — the page changing — and returning nothing at
    # all reads to the model as "the web has no answer" rather than "the parse
    # broke". Anything that is plainly an external link will do.
    for anchor in tree.css("a"):
        url = _clean_ddg_url(anchor.attributes.get("href") or "")
        if not url or url in seen:
            continue
        host = urlparse(url).hostname or ""
        if host.endswith("duckduckgo.com"):
            continue
        seen.add(url)
        results.append(
            WebSearchResult(title=(anchor.text() or "").strip()[:MAX_TITLE_CHARS] or None, url=url)
        )
        if len(results) >= limit:
            break
    return results


class WebSearchTool(Tool):
    """Search the public web and return links. Keyless."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="web_search",
            description="""
Purpose:
Search the public web and get back a ranked list of results: title, URL and a
one-line summary for each. Needs no API key.

Use when:
    - The user asks about something you would need the open web to answer
    - You need to FIND a page before you can read it

Then:
    - Call web_fetch on the URL you chose. Search returns summaries only —
      never the contents of a page. Do not answer factual questions from the
      snippets alone; open the source first.

Do not use when:
    - The answer is in the user's own data (use create_data)
    - The user already gave you the URL (go straight to web_fetch)
            """,
            category="research",
            version="1.0.0",
            input_schema=WebSearchInput.model_json_schema(),
            output_schema=WebSearchOutput.model_json_schema(),
            tags=["web", "search", "research"],
            timeout_seconds=REQUEST_TIMEOUT_SECONDS + 20,
            idempotent=True,
        )

    @property
    def input_model(self) -> Type[BaseModel]:
        return WebSearchInput

    @property
    def output_model(self) -> Type[BaseModel]:
        return WebSearchOutput

    async def run_stream(
        self, tool_input: Dict[str, Any], runtime_ctx: Dict[str, Any]
    ) -> AsyncIterator[ToolEvent]:
        data = WebSearchInput(**tool_input)
        report = runtime_ctx.get("report")
        report_id = str(report.id) if report else None

        def _end(output: WebSearchOutput, summary: str) -> ToolEndEvent:
            return ToolEndEvent(
                type="tool.end",
                payload={
                    "output": output.model_dump(),
                    "observation": {"summary": summary, "success": output.success},
                },
            )

        organization_settings = runtime_ctx.get("settings")
        if not organization_settings:
            yield _end(
                WebSearchOutput(
                    query=data.query,
                    error_message="Web search is unavailable (missing organization settings).",
                ),
                "Missing settings context",
            )
            return

        from app.core.feature_flags import setting_enabled

        if not setting_enabled(organization_settings, "enable_web_fetch"):
            await log_tool_audit(
                runtime_ctx,
                action="tool.access_blocked_by_policy",
                resource_type="report",
                resource_id=report_id,
                details={"tool": "web_search", "policy": "enable_web_fetch"},
            )
            yield _end(
                WebSearchOutput(
                    query=data.query,
                    error_message=(
                        "Web access is disabled for this organization. An administrator can "
                        "turn it on with the Web Fetch setting."
                    ),
                ),
                "web_search blocked: enable_web_fetch is disabled",
            )
            return

        yield ToolStartEvent(
            type="tool.start",
            payload={"title": f"Searching the web for “{data.query}”", "query": data.query},
        )
        yield ToolProgressEvent(type="tool.progress", payload={"stage": "searching"})

        try:
            async with cf_requests.AsyncSession(
                impersonate=IMPERSONATE_PROFILE, timeout=REQUEST_TIMEOUT_SECONDS
            ) as session:
                response = await session.post(
                    SEARCH_ENDPOINT,
                    data={"q": data.query},
                    allow_redirects=True,
                )
        except Timeout:
            yield _end(
                WebSearchOutput(query=data.query, error_message="The search timed out."),
                "Search timed out",
            )
            return
        except RequestException as exc:
            logger.warning("web_search: request failed for %r: %s", data.query, exc)
            yield _end(
                WebSearchOutput(query=data.query, error_message="Could not reach the search service."),
                "Search request failed",
            )
            return

        if response.status_code != 200:
            yield _end(
                WebSearchOutput(
                    query=data.query,
                    error_message=f"The search service answered {response.status_code}.",
                ),
                f"Search HTTP {response.status_code}",
            )
            return

        try:
            results = _parse_results(response.text or "", data.max_results)
        except Exception as exc:  # noqa: BLE001 — a parse failure must not kill the turn
            logger.warning("web_search: parse failed for %r: %s", data.query, exc)
            results = []

        await log_tool_audit(
            runtime_ctx,
            action="tool.web_search_executed",
            resource_type="report",
            resource_id=report_id,
            details={"tool": "web_search", "query": data.query, "results": len(results)},
        )

        if not results:
            # ★Distinct from an error on purpose: "nothing matched" is a real
            # and useful answer, and dressing it up as a failure would push the
            # model into retrying a search that will keep returning nothing.
            yield _end(
                WebSearchOutput(success=True, query=data.query, sources=[]),
                f"No results for “{data.query}”",
            )
            return

        yield _end(
            WebSearchOutput(success=True, query=data.query, sources=results),
            f"{len(results)} result(s) for “{data.query}”; open one with web_fetch to read it",
        )
