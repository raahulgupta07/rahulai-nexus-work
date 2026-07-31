// Loading instruction rows for list / tree surfaces.
//
// Every caller used to fire a single capped request and render `items` as if it
// were the whole set — no pagination, and `total` was thrown away. On an org
// past the cap that silently dropped the oldest instructions with nothing in the
// UI to say so, which reads as "some of my instructions are just gone".
//
// These helpers page until `items.length >= total`, so a surface either has
// every row it is entitled to or throws.
//
// Two projections, because the server caps them differently and the surfaces
// genuinely need different fields:
//
//   view=light (default) — no instruction body, no nested user record, a
//     `preview` prefix instead. A few hundred bytes a row, so the server allows
//     pages of up to LIGHT_MAX_LIMIT. Right for trees and label lists.
//   view=full — the complete list row. Some surfaces render the author chip and
//     an inline body excerpt, neither of which survives the light projection, so
//     they page the full row instead. The server refuses a full page above
//     FULL_MAX_LIMIT (it does NOT silently trim), hence the smaller page size —
//     paging still removes the truncation, it just costs more bytes.

/** Page size for `view=light`. Must stay <= the server's LIGHT_MAX_LIMIT. */
export const INSTRUCTION_PAGE_SIZE = 500
/** Page size for `view=full`. Must stay <= the server's FULL_MAX_LIMIT, which
 *  rejects rather than trims — an over-large page here is a 400, not a short
 *  list, so this is a hard ceiling and not a preference. */
export const INSTRUCTION_FULL_PAGE_SIZE = 200

export interface InstructionListResult<T = any> {
  items: T[]
  total: number
}

export interface FetchAllInstructionsOptions {
  /** Projection to page through. Defaults to 'light'. */
  view?: 'light' | 'full'
}

/**
 * Fetch every instruction matching `query`, paging until the set is complete.
 *
 * `query` takes the same filters as `GET /instructions` minus paging — `skip`,
 * `limit` and `view` are managed here and any caller-supplied values for them
 * are ignored. Pick the projection with `opts.view`.
 */
export async function fetchAllInstructions<T = any>(
  query: Record<string, any>,
  opts: FetchAllInstructionsOptions = {},
): Promise<InstructionListResult<T>> {
  const { skip: _s, limit: _l, view: _v, ...filters } = query || {}
  const view = opts.view === 'full' ? 'full' : 'light'
  const pageSize = view === 'full' ? INSTRUCTION_FULL_PAGE_SIZE : INSTRUCTION_PAGE_SIZE
  const items: T[] = []
  let total = 0

  for (let skip = 0; ; skip += pageSize) {
    const { data, error } = await useMyFetch<any>('/api/instructions', {
      method: 'GET',
      query: { ...filters, view, skip, limit: pageSize },
    })
    // Surface the failure instead of returning a short list that looks complete
    // — a partial result presented as whole is the bug this replaces.
    if (error?.value) throw error.value

    const payload: any = (data as any)?.value
    const page: T[] = payload?.items || []
    total = Number(payload?.total ?? page.length)
    items.push(...page)

    // An empty page also terminates: without it a `total` that disagrees with
    // what the filters actually return (a post-query cut such as the per-user
    // table-accessibility filter) would loop forever.
    if (!page.length || items.length >= total) break
  }

  return { items, total }
}

/** The label a tree/list row shows: title, else the body prefix, else a stub. */
export function instructionRowLabel(ins: any, max = 60): string {
  const title = (ins?.title || '').trim()
  if (title) return title
  const body = (ins?.preview ?? ins?.text ?? '') as string
  return body.split('\n')[0].slice(0, max) || 'Untitled'
}

/** Text a client-side filter can match on. The full body is not loaded here;
 *  `preview` covers the head of it, and the server `search` filter covers the
 *  rest for callers that need it. */
export function instructionSearchText(ins: any): string {
  return `${ins?.title || ''}\n${ins?.preview ?? ins?.text ?? ''}`.toLowerCase()
}
