// /composables/useMyFetch.ts

// Concurrent identical GETs share one network call. Different components
// routinely request the same endpoint within milliseconds of each other on
// page load (measured: organization/members twice at a 0ms gap, review-hunks
// twice 97ms apart, whoami/settings/llm-models each twice during report boot)
// and every duplicate response triggered its own state update and re-render —
// the visible "flicker" while a page settles. In-flight-only: once a response
// lands the entry is gone. Each caller gets its own clone of the body so one
// component mutating its copy (sorting in place, etc.) cannot alias another's.
//
// ★★★"In-flight-only, therefore never stale" — which was written here, and is
// WRONG the moment a write happens.
//
// An in-flight GET is a request whose answer was computed BEFORE it landed. Join
// a caller to it AFTER a mutation and that caller is handed the pre-mutation
// world, from a cache that believes it holds nothing:
//
//     t0  GET  /instructions/X/review-hunks      <- starts, 1 suggestion pending
//     t1  POST /instructions/X/resolve           <- the member accepts it
//     t2  GET  /instructions/X/review-hunks      <- JOINS t0. Answer: 1 pending.
//
// The screen then shows a pending change the server no longer has, and no retry
// ever comes, because as far as this map is concerned the request succeeded. It
// clears on a page reload — the map is gone — which is exactly the shape of the
// "I accepted it and the badge stays until I refresh" report. Measured on
// 0.0.543.9: the database, `?pending_only=true`, `/counts` and `/review-hunks`
// all said zero pending while the toolbar still said "1 pending".
//
// ★A write is a fence. Every mutation bumps `writeEpoch`, and a GET may only
// join an entry created in the CURRENT epoch — so a request issued after a write
// always goes to the network. Callers already waiting on the older promise still
// get its answer: they asked before the write, and that is the answer to the
// question they asked. Nothing is cancelled and nothing is retried; the fence
// only decides who is allowed to share.
const inflightGets = new Map<string, { epoch: number; promise: Promise<any> }>()
let writeEpoch = 0

const cloneBody = (data: any) => {
  try { return structuredClone(data) } catch { return data }
}

export const useMyFetch: typeof useFetch = async (request, opts?) => {
  const config = useRuntimeConfig()
  const { token } = useAuth()
  const { organization, ensureOrganization } = useOrganization()

  const isClient = process.client

  // Ensure organization is loaded before making the request
  const orgResult = await ensureOrganization()

  opts = opts || {}
  opts.headers = {
    ...opts.headers,
    Authorization: `${token.value}`,
  }

  // Add the organization ID to the headers if it's set
  // Use the returned organization from ensureOrganization to avoid timing issues
  if (orgResult?.id) {
    opts.headers['X-Organization-Id'] = orgResult.id
  } else {
    // Still make the request but without org header - let backend handle the error
    console.warn('No organization ID available for API request:', request)
  }

  if (opts.stream) {
    // ★A streamed POST is still a write (this is how a chat turn is sent), and
    // the fence has to go up for it too — otherwise the reads that follow a
    // streamed mutation can be joined to answers computed before it.
    if (String((opts as any).method || 'GET').toUpperCase() !== 'GET') writeEpoch += 1
    const { stream: _, headers: rawHeaders, ...fetchOpts } = opts as any
    const headers = { ...(rawHeaders as Record<string, string>), 'Accept': 'text/event-stream', 'Cache-Control': 'no-cache' }
    return new Promise((resolve, reject) => {
      fetch(`${config.public.baseURL}${request}`, {
        ...fetchOpts,
        headers,
      }).then(response => {
        if (!response.ok) {
          reject(new Error(`HTTP error! status: ${response.status}`))
        } else {
          resolve({ data: response })
        }
      }).catch(reject)
    })
  }

  // This app is a client-side SPA. On the client, prefer $fetch so calls made
  // from onMounted/watch/event handlers do not register new async-data entries
  // during route transitions.
  if (isClient) {
    try {
      const method = String((opts as any).method || 'GET').toUpperCase()
      const canDedupe =
        method === 'GET' && typeof request === 'string' && !(opts as any).body
      // ★The fence, raised BEFORE the write is sent rather than after it
      // answers: a GET issued while the mutation is still in flight is already
      // asking about a world the write is changing, and must not be handed a
      // reply computed before it started.
      if (method !== 'GET') writeEpoch += 1
      let data: any
      if (canDedupe) {
        const key = `${orgResult?.id || ''}|${request}|` +
          JSON.stringify((opts as any).query ?? (opts as any).params ?? null)
        const epoch = writeEpoch
        let entry = inflightGets.get(key)
        if (!entry || entry.epoch !== epoch) {
          const promise = $fetch(request, { baseURL: config.public.baseURL, ...opts })
            // Only retract our OWN entry: a later epoch may have replaced it,
            // and deleting that one would leave a live request unshareable and
            // let the next caller open a third.
            .finally(() => { if (inflightGets.get(key)?.promise === promise) inflightGets.delete(key) })
          entry = { epoch, promise }
          inflightGets.set(key, entry)
        }
        data = cloneBody(await entry.promise)
      } else {
        data = await $fetch(request, {
          baseURL: config.public.baseURL,
          ...opts
        })
      }
      return {
        data: ref(data),
        error: ref(null),
        pending: ref(false),
        refresh: () => {},
        status: ref('success')
      }
    } catch (error) {
      return {
        data: ref(null),
        error: ref(error),
        pending: ref(false),
        refresh: () => {},
        status: ref('error')
      }
    }
  }

  return useFetch(request, { baseURL: config.public.baseURL, ...opts })
    .then(response => {
      return response
    })
    .catch(error => {
      throw error
    });
};
