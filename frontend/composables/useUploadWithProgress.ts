/**
 * Upload a file and report how much of it has actually gone up.
 *
 * ★ WHY THIS EXISTS RATHER THAN useMyFetch.
 * The fetch API has no upload-progress event. None. A request body is handed to
 * the browser and the next thing the caller learns is the response — which is
 * why every upload in this app rendered an indeterminate spinner: not a design
 * choice, a limitation of the transport. XMLHttpRequest still exposes
 * `upload.onprogress`, so it is the only way to put a real number on screen.
 *
 * ★ WHAT THE NUMBER MEANS, AND WHAT IT DOES NOT.
 * The percentage covers BYTES SENT. When it reaches 100 the server has not
 * finished: it still parses the file, may split a workbook into one table per
 * sheet, and may re-learn the agent. That work has no progress channel to the
 * browser. So this returns a `stage` as well, and the UI must switch to a named
 * indeterminate state at the seam. A bar that fills to 100% and then sits there
 * for another thirty seconds is the same lie as a spinner, only more confident.
 *
 * Auth mirrors useMyFetch exactly — same bearer token, same X-Organization-Id.
 * Diverging here would be a 401 that only happens on uploads.
 */

export type UploadStage = 'uploading' | 'processing' | 'done' | 'error'

export interface UploadHandle<T = any> {
  /** 0–100, bytes sent. Stays at 100 while `stage` is 'processing'. */
  percent: Ref<number>
  loaded: Ref<number>
  total: Ref<number>
  stage: Ref<UploadStage>
  promise: Promise<{ data: T | null; error: any }>
  abort: () => void
}

export const useUploadWithProgress = () => {
  const config = useRuntimeConfig()
  const { token } = useAuth()
  const { ensureOrganization } = useOrganization()

  /**
   * @param path   API path, as passed to useMyFetch (e.g. `/files`)
   * @param body   FormData to send
   */
  const upload = <T = any>(path: string, body: FormData): UploadHandle<T> => {
    const percent = ref(0)
    const loaded = ref(0)
    const total = ref(0)
    const stage = ref<UploadStage>('uploading')
    let xhr: XMLHttpRequest | null = null

    const promise = (async () => {
      const org = await ensureOrganization()
      return new Promise<{ data: T | null; error: any }>((resolve) => {
        xhr = new XMLHttpRequest()
        xhr.open('POST', `${config.public.baseURL}${path}`, true)
        xhr.setRequestHeader('Authorization', `${token.value}`)
        if (org?.id) xhr.setRequestHeader('X-Organization-Id', org.id)

        xhr.upload.onprogress = (e) => {
          if (!e.lengthComputable) return
          loaded.value = e.loaded
          total.value = e.total
          // Hold at 99 until the response lands: reaching 100 while the server
          // is still working is what makes people think it has hung.
          percent.value = Math.min(99, Math.round((e.loaded / e.total) * 100))
        }

        // The last byte is up; everything after this is server-side and opaque.
        xhr.upload.onload = () => {
          percent.value = 100
          stage.value = 'processing'
        }

        xhr.onload = () => {
          let parsed: any = null
          try { parsed = xhr!.responseText ? JSON.parse(xhr!.responseText) : null } catch { parsed = null }
          if (xhr!.status >= 200 && xhr!.status < 300) {
            stage.value = 'done'
            resolve({ data: parsed as T, error: null })
          } else {
            stage.value = 'error'
            resolve({ data: null, error: { status: xhr!.status, data: parsed } })
          }
        }

        xhr.onerror = () => { stage.value = 'error'; resolve({ data: null, error: { status: 0 } }) }
        xhr.onabort = () => { stage.value = 'error'; resolve({ data: null, error: { aborted: true } }) }

        xhr.send(body)
      })
    })()

    return {
      percent, loaded, total, stage, promise,
      abort: () => { try { xhr?.abort() } catch { /* already finished */ } },
    }
  }

  /** "24.8 MB" — for the byte counter beside the percentage. */
  const formatBytes = (n: number): string => {
    if (!n) return '0 B'
    const u = ['B', 'KB', 'MB', 'GB']
    const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
    const v = n / Math.pow(1024, i)
    return `${i === 0 ? v : v.toFixed(v < 10 ? 1 : 0)} ${u[i]}`
  }

  return { upload, formatBytes }
}
