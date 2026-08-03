# Latency — measured, not estimated

Measured 2026-08-03 against the running database (`dash-postgres`), from
`tool_executions.sub_timings_json` and `plan_decisions.metrics_json`. No new
instrumentation was needed: the breakdown was already being persisted, so every
past run can be split retroactively.

★**The plan's framing was wrong, and the numbers say so.** Phase 12 was written
around a 70-second incident with `inspect_data` at 35.3s, and asked whether that
time was the warehouse or our own schema/sampling overhead. It is neither.

---

## Where the time actually goes

| tool | runs | avg | codegen LLM | execution | LLM share |
|---|---|---|---|---|---|
| `create_artifact` | 48 | **98.7s** (max 294.1s) | — *(see below)* | — | **~99%** |
| `inspect_data` | 43 | 19.0s (max 61.5s) | **12.3s** | 4.0s | **65%** |
| `write_csv` | 3 | 17.6s | — | — | — |
| `create_data` | 210 | 14.3s (max 79.0s) | **10.0s** | **0.96s** | **91%** |
| `edit_artifact` | 8 | 10.0s | — | — | — |
| `read_query` | 11 | 0.88s | — | — | — |
| `describe_tables` | 24 | 0.18s | — | — | — |

**`create_data` runs 210 times at 14.3s, and 0.96s of that is executing the
code.** The other 13 seconds are a model writing it. `inspect_data` is the same
shape. Whatever the warehouse costs, it is not what anyone is waiting for.

## `create_artifact` is the real outlier, and it was invisible

It is **five times slower than anything else in the product** and it never
appeared in the incident breakdown. Its stage timings, worst run of 294.1s:

```
init                        19.7 ms
loading_visualizations     112.4 ms
building_profiles            0.6 ms
visualizations_resolved      0.2 ms
building_context            27.8 ms
building_prompt              1.2 ms
llm_generating            4209.8 ms
llm_generating          141749.3 ms
executing_pptx_code          3.6 ms
llm_generating          145770.8 ms
saving_artifact             30.6 ms
```

**291.7 of 294.1 seconds is `llm_generating`.** Everything this codebase
controls — loading, profiling, context building, prompt building, executing the
generated pptx, saving — comes to **under 200 milliseconds combined**.

There is no optimization to find in our code here. The only levers are: make
fewer model calls, send smaller prompts, or use a faster model.

### Most runs make more than one model call, and nothing counts them as retries

| `llm_generating` stages | runs | avg duration |
|---|---|---|
| 1 | 16 | 107.8s |
| 2 | 29 | 88.6s |
| 3 | 2 | 221.4s |

★`retry_count` is **0 on all 48 runs**. So either these second and third passes
are a designed multi-stage generation, or `retry_count` is not counting what its
name says. Worth settling before anyone optimizes against it — a retry counter
that reads zero while the tool makes three model calls will send the next person
looking in the wrong place.

---

## PERF-2 — prompt caching *(12.1)*

**Implemented, and not reachable on this install.**

`anthropic_client.py:301-318` places `cache_control: ephemeral` breakpoints on
the system block and on the last tool, which caches the whole static
(system + tools) prefix. It is on by default (`enable_cache: bool = True`) and
the shape is right — exactly the static-vs-warm split `ContextHub` already has.

But it exists **only in the Anthropic client**. This install's provider is
`custom`, which `llm.py:165` routes to the OpenAI-compatible client, and that
client sets no cache breakpoints. OpenAI-compatible endpoints generally do
automatic prefix caching server-side, so there is no per-request lever to pull
here — but there is also no explicit control, and nothing measures whether it is
working.

**Verdict:** not the largest available lever on this install, because it is not
available on this install. It would be if the provider were Anthropic.

## PERF-3 / E12 — model routing *(12.3)*

**The Auto router is inactive here, so nothing routes anywhere.**

`resolve_routing_candidates` only offers models that carry a non-empty
`config['routing_hint']` (`model_router.py:31-42, 72`) — the product decision
being that the planner routes on an admin's stated intent, not on a bare model
list. Measured against the live database:

```
x-ai/grok-4.5        is_default=t  is_small_default=t  config={}
openai/gpt-5.6-luna  is_default=f  is_small_default=f  config={}
```

Neither model has a hint, so the candidate set is empty and `route_model` is
removed from the catalog entirely. **E12 — "can `model_router` route codegen
down, firing the cheap-model failure on accounts that never chose one?" — is
refuted for this install**: no routing happens at all.

★Note also that `x-ai/grok-4.5` is **both** the default and the small default.
Even with hints configured there is nothing smaller to route to.

## PERF-1 — gating `search_mcps` *(deferred from phase 7)*

Still worth doing, still not done, and the reason is worth recording. The
capability mechanism (`ToolMetadata.requires_capability`) gates on capabilities
exposed by an attached **connection**. MCP tool availability is not a connection
capability — it depends on per-tool enable flags, admin policy and user
preferences, resolved in `schema_context_builder.py:845-874`.

Answering "does this run have MCP tools?" in the capability path therefore means
either duplicating that resolution or restructuring it. Duplicating it would
recreate exactly the divergence phase 7 spent its time removing. Measured cost
of the thing being saved is 134ms plus one planner round-trip.

---

## What to do with this

1. **`create_artifact` first, by a factor of five.** 98.7s average, ~99% model
   time, and 31 of 48 runs make two or three model calls. Settle what those
   passes are and whether the second one can be conditional.
2. **Codegen is the latency, not execution.** `create_data` spends 10s
   generating and 0.96s running. Caching, smaller prompts and model choice are
   the levers; nothing in the execution path is worth tuning.
3. **The incident's 35.3s `inspect_data` was not anomalous** — the fleet average
   is 19s and the max is 61s. It is one draw from an ordinary distribution, and
   two-thirds of it was the model.
4. **Fix or remove `retry_count`** on `create_artifact` before anyone optimizes
   against it.
