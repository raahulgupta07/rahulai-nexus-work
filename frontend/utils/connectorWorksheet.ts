// connectorWorksheet.ts
// -----------------------------------------------------------------------------
// Client-side generator for the "Connection Setup Worksheet" — a self-contained,
// printable, fillable HTML document an admin can hand to whoever holds the
// credentials (IT / cloud admin / DBA), have them fill in the browser, print to
// PDF, or email back.
//
// It combines:
//   1. the LIVE field schema for a connector, fetched from the backend
//      `GET /data_sources/{type}/fields?auth_policy=all` (every config field +
//      every auth variant's credential fields + auth metadata), and
//   2. curated "where to get it" guidance from connectorDocs.ts.
//
// Because the field list comes from the backend registry, EVERY connector — even
// ones without a curated entry — produces a complete, accurate worksheet; the
// curated docs just make the common ones richer.
// -----------------------------------------------------------------------------

import { getConnectorDoc, type ConnectorDoc } from './connectorDocs'
import { getBranding } from '~/composables/useBranding'

// Product name comes from the instance branding (composables/useBranding.ts).
// It is read at call time, not module load, so it reflects the live setting.
const productName = () => getBranding().productName

// Shape of a single field as returned by the backend (JSON Schema property).
interface SchemaProperty {
  title?: string
  description?: string
  default?: any
  type?: string
  'ui:type'?: string
  [k: string]: any
}
interface JsonSchema {
  properties?: Record<string, SchemaProperty>
  required?: string[]
}
interface FieldsResponse {
  config?: JsonSchema | null
  credentials?: JsonSchema | null
  credentials_by_auth?: Record<string, JsonSchema>
  type?: string
  title?: string
  description?: string
  auth?: {
    default?: string
    by_auth?: Record<string, { title?: string }>
    policy?: string
  }
}

// A connector object as the UI already has it (from /available_data_sources or
// the modal's selectedDataSource). Only type/title are required.
export interface WorksheetConnector {
  type: string
  title?: string
  description?: string
  [k: string]: any
}

// --- helpers -----------------------------------------------------------------

function esc(s: any): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function prettyLabel(fieldName: string, schema?: SchemaProperty): string {
  if (schema?.title) return schema.title
  return fieldName
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// True when an auth variant represents a per-user sign-in (delegated OAuth /
// Kerberos delegation) rather than a shared system/service credential.
function isPerUserAuth(authKey: string): boolean {
  const k = authKey.toLowerCase()
  return k === 'oauth' || k.includes('delegated') || k.includes('_sso')
}

// Name-derived generic "where to get it" hint for fields with no curated entry.
function genericWhereToGet(fieldName: string, schema?: SchemaProperty): string {
  if (schema?.description) return schema.description
  const n = fieldName.toLowerCase()
  const pairs: [RegExp, string][] = [
    [/tenant/, 'Azure Portal -> Microsoft Entra ID -> Overview -> Directory (tenant) ID.'],
    [/client_secret|clientsecret/, 'The OAuth/app client secret from your identity provider or app registration. Shown once at creation — copy immediately.'],
    [/client_id|clientid/, 'The OAuth/application (client) ID from your identity provider or app registration.'],
    [/access_key/, 'AWS Console -> IAM -> Users -> Security credentials -> Create access key (Access key ID).'],
    [/secret_key/, 'The AWS secret access key shown once when the access key was created.'],
    [/session_token/, 'Optional. Only for temporary (STS) credentials.'],
    [/role_arn|assume/, 'AWS Console -> IAM -> Roles -> (role) -> copy the Role ARN.'],
    [/account/, 'The account / tenant identifier for this service (see the provider console).'],
    [/(^|_)host($|_)|hostname|server/, 'Hostname or IP of the server — from your DBA / infrastructure team. No protocol or port.'],
    [/(^|_)port($|_)/, 'The TCP port the service listens on. Use the default unless changed by your admin.'],
    [/database|catalog/, 'The database / catalog name to connect to — ask your DBA.'],
    [/schema/, 'Optional. Restrict discovery to one schema; blank indexes all visible schemas.'],
    [/warehouse/, 'The compute warehouse name (see your data warehouse console).'],
    [/(^|_)url|endpoint|uri/, 'The service base URL / endpoint — from the provider console or your admin.'],
    [/region/, 'The cloud region code, e.g. us-east-1 (from the provider console).'],
    [/api[_-]?key|apikey/, 'Generate an API key in the service\'s settings / developer area. Copy it immediately.'],
    [/token/, 'Generate a personal access / API token in the service\'s user or developer settings. Copy it immediately.'],
    [/password|passphrase/, 'The password for the account above — from your admin. Stored encrypted.'],
    [/user|login/, 'A dedicated read-only service account username — ask your DBA / admin to create one.'],
    [/(^|_)path($|_)|file/, 'The file path or location on the server / share where the data lives.'],
    [/domain/, 'The Active Directory / login domain (from your admin).'],
    [/project/, 'The cloud project identifier (from the provider console).'],
    [/dataset/, 'The dataset name within the project (from the provider console).'],
    [/bucket/, 'The object-storage bucket name (from the provider console).'],
  ]
  for (const [re, hint] of pairs) if (re.test(n)) return hint
  return 'Provided by your administrator or the owner of this system.'
}

function isSecret(fieldName: string, schema?: SchemaProperty): boolean {
  const ui = (schema?.['ui:type'] || '').toLowerCase()
  if (ui === 'password') return true
  const n = fieldName.toLowerCase()
  return /password|secret|token|private_key|passphrase|secret_key/.test(n)
}

interface RenderField {
  name: string
  label: string
  required: boolean
  where: string
  isSecret: boolean
  isLong: boolean
}

function toRenderFields(schema: JsonSchema | null | undefined, doc?: ConnectorDoc): RenderField[] {
  const props = schema?.properties || {}
  const required = new Set(schema?.required || [])
  return Object.entries(props)
    .filter(([, s]) => !(s && (s as any)['ui:hidden'] === true))
    .map(([name, s]) => {
      const curated = doc?.whereToGet?.[name]
      const ui = (s?.['ui:type'] || '').toLowerCase()
      return {
        name,
        label: prettyLabel(name, s),
        required: required.has(name),
        where: curated || genericWhereToGet(name, s),
        isSecret: isSecret(name, s),
        isLong: ui === 'textarea' || /_json$|credentials_json|private_key_pem/.test(name),
      }
    })
}

function fieldRowsHtml(fields: RenderField[]): string {
  if (!fields.length) {
    return `<tr><td colspan="3" class="empty">No fields to enter for this section.</td></tr>`
  }
  return fields
    .map((f) => {
      const req = f.required
        ? '<span class="req" title="Required">*</span>'
        : '<span class="opt">optional</span>'
      const secret = f.isSecret ? '<span class="lock" title="Sensitive — stored encrypted">🔒</span>' : ''
      const input = f.isLong
        ? `<textarea class="fill" rows="3" placeholder="Paste value here"></textarea>`
        : `<input class="fill" type="text" placeholder="Enter value" />`
      return `
      <tr>
        <td class="fname"><code>${esc(f.name)}</code><div class="flabel">${esc(f.label)} ${req} ${secret}</div></td>
        <td class="fwhere">${esc(f.where)}</td>
        <td class="finput">${input}</td>
      </tr>`
    })
    .join('')
}

// --- HTML document ------------------------------------------------------------

export function generateWorksheetHtml(
  connector: WorksheetConnector,
  fields: FieldsResponse | null,
  doc?: ConnectorDoc
): string {
  const title = fields?.title || connector.title || connector.type
  const description = fields?.description || connector.description || ''
  const curated = doc || getConnectorDoc(connector.type)

  const configFields = toRenderFields(fields?.config, curated)

  // Auth variants: prefer per-variant credential schemas; fall back to the flat
  // credentials schema when the backend didn't split them.
  const byAuth = fields?.credentials_by_auth || {}
  const authTitles = fields?.auth?.by_auth || {}
  const authKeys = Object.keys(byAuth)
  const authSections =
    authKeys.length > 0
      ? authKeys.map((key) => ({
          key,
          title: authTitles[key]?.title || prettyLabel(key),
          perUser: isPerUserAuth(key),
          fields: toRenderFields(byAuth[key], curated),
        }))
      : fields?.credentials
      ? [
          {
            key: fields?.auth?.default || 'default',
            title: authTitles[fields?.auth?.default || '']?.title || 'Credentials',
            perUser: false,
            fields: toRenderFields(fields?.credentials, curated),
          },
        ]
      : []

  const hasPerUser = authSections.some((a) => a.perUser)
  const hasSystem = authSections.some((a) => !a.perUser)

  const authSectionsHtml = authSections
    .map((a) => {
      const badge = a.perUser
        ? '<span class="badge badge-user">Per-user sign-in</span>'
        : '<span class="badge badge-sys">System / service account</span>'
      const note = a.perUser
        ? 'Each user signs in with their own account when they first use this connection. No shared secret is stored; the admin only registers the app below (if any fields are shown).'
        : 'The admin enters one shared credential here; everyone queries through it.'
      return `
      <section class="authblock">
        <h3>${esc(a.title)} ${badge}</h3>
        <p class="authnote">${esc(note)}</p>
        <table class="grid">
          <thead><tr><th>Field</th><th>Where to get it</th><th>Your value</th></tr></thead>
          <tbody>${fieldRowsHtml(a.fields)}</tbody>
        </table>
      </section>`
    })
    .join('')

  const flowHtml =
    curated?.authFlow && curated.authFlow.length
      ? `<ol class="flow">${curated.authFlow.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`
      : `<ul class="flow">
          ${hasSystem ? '<li><strong>System / service account:</strong> the admin enters one shared credential; all users query through it.</li>' : ''}
          ${hasPerUser ? '<li><strong>Per-user sign-in:</strong> each user authorizes with their own account the first time they use this connection.</li>' : ''}
          ${!hasSystem && !hasPerUser ? '<li>Follow the connector\'s in-app instructions to authenticate.</li>' : ''}
        </ul>`

  const notesHtml = curated?.notes
    ? `<div class="notes"><strong>Notes &amp; prerequisites:</strong> ${esc(curated.notes)}</div>`
    : ''

  const modelSummary = [
    hasSystem ? 'a shared <strong>system / service account</strong>' : '',
    hasPerUser ? 'individual <strong>per-user sign-in</strong>' : '',
  ]
    .filter(Boolean)
    .join(' and/or ')

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${esc(title)} — Connection Setup Worksheet</title>
<style>
  :root { --ink:#1f2933; --muted:#6b7280; --line:#e5e7eb; --accent:#2563eb; --accent-soft:#eff6ff; --bg:#f9fafb; }
  * { box-sizing: border-box; }
  html,body { margin:0; padding:0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: var(--ink); background: var(--bg); line-height: 1.45; font-size: 13px; }
  .page { max-width: 820px; margin: 24px auto; background:#fff; padding: 40px 44px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); border-radius: 8px; }
  header.masthead { border-bottom: 3px solid var(--accent); padding-bottom: 16px; margin-bottom: 20px; }
  .product { font-size: 12px; letter-spacing:.08em; text-transform: uppercase; color: var(--accent); font-weight: 700; }
  h1 { font-size: 24px; margin: 6px 0 2px; }
  h1 .sub { color: var(--muted); font-weight: 500; }
  .desc { color: var(--muted); margin: 4px 0 0; max-width: 60ch; }
  h2 { font-size: 15px; margin: 26px 0 10px; padding-bottom:4px; border-bottom:1px solid var(--line); }
  h3 { font-size: 13.5px; margin: 18px 0 6px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .returnbox { display:grid; grid-template-columns: 1fr 1fr; gap: 10px 18px;
               background: var(--accent-soft); border:1px solid #dbeafe; border-radius:8px; padding:14px 16px; margin-bottom: 6px; }
  .returnbox label { font-size: 11px; color: var(--muted); display:block; margin-bottom:3px; text-transform:uppercase; letter-spacing:.04em; }
  .returnbox input { width:100%; border:none; border-bottom:1px solid #9db6e6; background:transparent; padding:4px 2px; font-size:13px; color:var(--ink); }
  table.grid { width:100%; border-collapse: collapse; margin: 4px 0 8px; table-layout: fixed; }
  table.grid th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
                  border-bottom:2px solid var(--line); padding:6px 8px; }
  table.grid td { border-bottom:1px solid var(--line); padding:8px; vertical-align: top; }
  table.grid th:nth-child(1), td.fname { width: 26%; }
  table.grid th:nth-child(2), td.fwhere { width: 42%; }
  table.grid th:nth-child(3), td.finput { width: 32%; }
  td.fname code { font-size:11px; background:#f3f4f6; padding:1px 5px; border-radius:4px; color:#111827; word-break: break-all; }
  .flabel { font-size:11px; color:var(--muted); margin-top:3px; }
  .fwhere { font-size:12px; color:#374151; }
  .req { color:#dc2626; font-weight:700; }
  .opt { font-size:10px; color:#9ca3af; text-transform:uppercase; }
  .lock { font-size:11px; }
  input.fill, textarea.fill { width:100%; border:1px solid #cbd5e1; border-radius:5px; padding:6px 8px; font-size:12px;
                              background:#fcfdff; font-family: inherit; resize: vertical; }
  input.fill:focus, textarea.fill:focus { outline:2px solid var(--accent); border-color:var(--accent); }
  td.empty { color:var(--muted); font-style:italic; }
  .badge { font-size:10px; font-weight:600; padding:2px 8px; border-radius:999px; text-transform:uppercase; letter-spacing:.03em; }
  .badge-sys { background:#ecfdf5; color:#047857; border:1px solid #a7f3d0; }
  .badge-user { background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; }
  .authnote { font-size:12px; color:var(--muted); margin:2px 0 6px; }
  .authblock { margin-bottom: 6px; }
  .modelcard { background:#fafafa; border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin:8px 0; }
  ol.flow, ul.flow { margin:8px 0; padding-left: 20px; }
  ol.flow li, ul.flow li { margin:4px 0; font-size:12.5px; }
  .notes { background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:12px 14px; font-size:12.5px; margin-top:10px; }
  .legend { font-size:11px; color:var(--muted); margin: 4px 0 0; }
  footer { margin-top: 28px; padding-top: 14px; border-top:1px solid var(--line); font-size:11px; color:var(--muted);
           display:flex; justify-content: space-between; gap: 12px; flex-wrap:wrap; }
  .toolbar { position: sticky; top: 0; text-align:center; padding: 10px; z-index: 10; }
  .printbtn { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:10px 20px; font-size:14px;
              font-weight:600; cursor:pointer; box-shadow:0 2px 6px rgba(37,99,235,.35); }
  .printbtn:hover { background:#1d4ed8; }
  .datefill { border:none; border-bottom:1px solid #9ca3af; background:transparent; padding:2px; font: inherit; color:var(--ink); }
  @media print {
    body { background:#fff; font-size: 11.5px; }
    .page { box-shadow:none; margin:0; max-width:none; padding: 0 8mm; border-radius:0; }
    .toolbar { display:none !important; }
    input.fill, textarea.fill { background:#fff; }
    table.grid, section, .modelcard, .notes { page-break-inside: avoid; }
    @page { size: A4; margin: 14mm 10mm; }
  }
</style>
</head>
<body>
  <div class="toolbar">
    <button class="printbtn" onclick="window.print()">🖨 Print / Save as PDF</button>
  </div>
  <div class="page">
    <header class="masthead">
      <div class="product">${esc(productName())}</div>
      <h1>${esc(title)} <span class="sub">— Connection Setup Worksheet</span></h1>
      ${description ? `<p class="desc">${esc(description)}</p>` : ''}
    </header>

    <h2>Fill &amp; return to</h2>
    <div class="returnbox">
      <div><label>Requested by (admin name)</label><input type="text" /></div>
      <div><label>Return to (admin email)</label><input type="text" /></div>
      <div><label>Credentials held by (name / team)</label><input type="text" /></div>
      <div><label>Date completed</label><input type="text" placeholder="YYYY-MM-DD" /></div>
    </div>
    <p class="legend"><span class="req">*</span> = required &nbsp;•&nbsp; 🔒 = sensitive (stored encrypted, never shown again) &nbsp;•&nbsp; Fill the "Your value" column, then print, save as PDF, or return this file.</p>

    <h2>1 · Connection details</h2>
    <p class="legend">Where the ${esc(title)} instance lives. Usually filled by the admin / infrastructure team.</p>
    <table class="grid">
      <thead><tr><th>Field</th><th>Where to get it</th><th>Your value</th></tr></thead>
      <tbody>${fieldRowsHtml(configFields)}</tbody>
    </table>

    <h2>2 · Authentication</h2>
    <div class="modelcard">
      This connector supports ${modelSummary || 'authentication as described below'}.
      ${flowHtml}
    </div>
    ${authSectionsHtml || '<p class="legend">No credential fields — this connector authenticates without stored secrets.</p>'}
    ${notesHtml}

    <footer>
      <span>${esc(productName())} · Connection Setup Worksheet · ${esc(title)}</span>
      <span>Generated <input class="datefill" type="text" value="${esc(new Date().toISOString().slice(0, 10))}" /> · Keep completed credentials confidential.</span>
    </footer>
  </div>
</body>
</html>`
}

// --- download orchestration ---------------------------------------------------

function slugify(s: string): string {
  return String(s || 'connector')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'connector'
}

function triggerDownload(html: string, filename: string) {
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}

/**
 * Fetch the connector's live field schema, build the worksheet HTML, and
 * trigger a browser download. Safe to call from a click handler (client-side).
 * Falls back to a curated-only worksheet if the field fetch fails.
 */
export async function downloadWorksheet(
  connector: WorksheetConnector,
  _docsMeta?: any
): Promise<void> {
  const type = connector?.type
  const doc = getConnectorDoc(type)
  let fields: FieldsResponse | null = null

  if (type) {
    try {
      // auth_policy=all → the backend returns every auth variant (system + per-user),
      // not just system-scoped ones. See get_data_source_fields().
      const res = await useMyFetch(
        `/data_sources/${encodeURIComponent(type)}/fields?auth_policy=all` as any,
        { method: 'GET' }
      )
      fields = ((res as any)?.data?.value as FieldsResponse) || null
    } catch (e) {
      // Non-fatal: still produce a worksheet from curated/connector info.
      console.warn('[worksheet] field schema fetch failed, using curated fallback', e)
    }
  }

  const html = generateWorksheetHtml(connector, fields, doc)
  const name = slugify(fields?.title || connector.title || type || 'connector')
  triggerDownload(html, `${name}-setup-worksheet.html`)
}
