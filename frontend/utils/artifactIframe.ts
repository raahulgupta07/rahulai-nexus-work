import libManifest from '../public/libs/manifest.json';
/**
 * Shared builder for the artifact iframe HTML.
 *
 * Used by:
 *  - pages/r/[id]/index.vue          (public report view, read-only)
 *  - components/dashboard/ArtifactFrame.vue  (in-app editor; passes polishMode: true)
 *
 * Sister runtime: frontend/public/mcp-artifact-app.html (loaded by Claude / Cursor
 * via MCP Apps spec, gets data via postMessage instead of inlining). That file
 * keeps its own DOCTYPE shell because external hosts load it by URL, but it
 * loads /libs/artifact-globals.js so the runtime surface (useMemo, useState,
 * KPICard, useFilters, …) matches what this template provides.
 */

/**
 * Cache-buster for /libs/artifact-globals.js. The file is served without a
 * content hash, so a browser can pair a cached OLD runtime with NEW artifact
 * code ("DataTable is not defined"). Bump this whenever artifact-globals.js
 * gains or changes a global.
 */
export const ARTIFACT_GLOBALS_VERSION = '2';

export interface ArtifactIframeFile {
  id: string;
  content_type: string;
  filename?: string;
  /** Short-lived, file-scoped token URL. Fine for <img> and <a download>: neither
   *  is a CORS-mode request, so an opaque-origin frame can still use them. */
  url?: string;
  /** data: URI carrying the file bytes (host resolves + injects so the frame
   *  never has to make a cross-origin request for them — see inlinePdfBytes). */
  dataUri?: string;
}

/**
 * Resolve PDF bytes on the HOST and hand them to the frame as a data: URI.
 *
 * ★Why this exists. The artifact frame runs at an OPAQUE origin
 * (`sandbox="allow-scripts"` without `allow-same-origin` — the 0.0.490.14
 * sandbox-escape fix). It therefore sends `Origin: null` and is cross-origin to
 * our own server. `<img src>` and `<a download>` still work, because neither is
 * a CORS-mode request. But `fetch()` IS, and `BowPdfViewer` fetches the PDF
 * bytes so it can hand pdf.js a byte array:
 *
 *   Access to fetch at 'http://…/api/files/…' from origin 'null' has been
 *   blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present
 *   on the requested resource.
 *
 * so every embedded PDF failed to load. A comment in artifact-globals.js
 * asserted "the token URL is same-origin and needs no auth header" — true when
 * it was written, false since the sandbox change, and nothing enforced it.
 *
 * ★Why not just send `Access-Control-Allow-Origin` on the token endpoint.
 * That endpoint serves file bytes to any caller holding the token, and the
 * token travels IN THE URL. Today a third-party page that gets hold of such a
 * URL can *display* the file but cannot *read* its bytes into JavaScript.
 * `Access-Control-Allow-Origin: *` removes exactly that barrier and turns a
 * leaked URL into a silent read-and-exfiltrate primitive. Resolving the bytes
 * here costs one host-side fetch and opens no cross-origin surface at all.
 * (It also happens to be what `dataUri` and the `BowFile` header comment
 * already described — the token-URL change simply never updated them.)
 *
 * ★The pdf.js worker needs no fix. `new Worker('/libs/pdf.worker.min.js')` is
 * indeed refused from a null origin, and CORS headers cannot lift that — a
 * classic dedicated worker script must be SAME-ORIGIN, which an opaque origin
 * can never be. But pdf.js catches that failure and falls back to its
 * main-thread "fake worker", which loads the bundle with a <script> tag rather
 * than a CORS fetch. Verified in Chromium inside a real `allow-scripts` frame:
 * bytes as a data: URI parse and rasterise correctly with the Worker
 * constructor failing. So only the bytes had to change.
 *
 * Only PDFs are inlined. Images already work over the token URL and inlining
 * them would bloat the srcdoc for no benefit. Files larger than the cap keep
 * their URL and fall through to BowPdfViewer's "Open PDF" card, which opens a
 * normal top-level tab.
 */
const PDF_INLINE_MAX_BYTES = 12 * 1024 * 1024;

export async function inlinePdfBytes(
  files: ArtifactIframeFile[],
): Promise<ArtifactIframeFile[]> {
  if (!Array.isArray(files) || files.length === 0) return files || [];
  return Promise.all(
    files.map(async (f) => {
      const isPdf = String(f?.content_type || '').toLowerCase().includes('pdf');
      if (!isPdf || !f.url || f.dataUri) return f;
      try {
        // Same-origin from the host page, so no CORS and no auth header needed.
        const res = await fetch(f.url);
        if (!res.ok) return f;
        const buf = await res.arrayBuffer();
        if (buf.byteLength > PDF_INLINE_MAX_BYTES) return f;
        const bytes = new Uint8Array(buf);
        let bin = '';
        // Chunked so a large PDF cannot blow the argument limit of fromCharCode.
        const CHUNK = 0x8000;
        for (let i = 0; i < bytes.length; i += CHUNK) {
          bin += String.fromCharCode.apply(
            null,
            Array.from(bytes.subarray(i, i + CHUNK)) as unknown as number[],
          );
        }
        return { ...f, dataUri: 'data:application/pdf;base64,' + btoa(bin) };
      } catch (e) {
        // Keep the URL — the frame degrades to the "Open PDF" card.
        console.warn('[artifactIframe] could not inline PDF bytes', f.id, e);
        return f;
      }
    }),
  );
}

export interface ArtifactIframeData {
  report: unknown;
  visualizations: unknown[];
  /** Embedded images/PDFs, rendered by the <BowFile> sandbox global. */
  files?: ArtifactIframeFile[];
}

/** The grounded narrative stored at artifact.content.insights. */
export interface ArtifactInsightsPayload {
  headline?: string;
  findings?: Array<{ text?: string; viz_id?: string }>;
  /** How many findings were dropped for citing a figure absent from the data. */
  rejected_count?: number;
  generated_at?: string;
}

export interface ArtifactIframeOptions {
  data: ArtifactIframeData;
  code: string;
  /**
   * ★Rendered INSIDE the document, below the dashboard. It used to be a Vue
   * component sitting beside the iframe, which meant four of the five surfaces
   * a dashboard reaches — fullscreen, the shared /r/<id> page, the PDF export
   * and the card thumbnail — carried no narrative at all.
   *
   * Composed here rather than written into the artifact's own code, so that
   * dashboards generated before insights existed get one too, and so the
   * verified figures are never handed back to a model to restate.
   */
  insights?: ArtifactInsightsPayload | null;
  mode?: 'page' | 'slides';
  /** Inject polish element-picker. Only meaningful in the editor. */
  polishMode?: boolean;
  /** Text shown inside #root before Babel transforms the artifact code. */
  loadingLabel?: string;
  /** Default 'production'. 'development' gives clearer React error messages. */
  reactBuild?: 'production' | 'development';
}

const SC = '</' + 'script>';

/**
 * ★The libraries an artifact document loads come from ONE list —
 * public/libs/manifest.json — because six places assemble that document and
 * they had already drifted: pdf.min.js was loaded here and by nothing else, so
 * a dashboard embedding a PDF rendered in the app while the thumbnail, the PDF
 * export and the planner's preview all showed BowPdfViewer's "nolib" state.
 * Nothing errored; the page simply came out different.
 *
 * ★NO `crossorigin` on these tags. This markup goes into an iframe carrying
 * sandbox="allow-scripts" and NO allow-same-origin, so the frame runs at an
 * opaque origin and sends `Origin: null`. `crossorigin` forces a CORS-mode
 * fetch, our own /libs/ responses carry no Access-Control-Allow-Origin, and the
 * browser refuses the file — "React is not defined", every dashboard blank.
 * Guarded by backend/tests/unit/fork/test_artifact_sandbox_loads_react.py.
 */
function pageLibTags(reactBuild: 'production' | 'development'): string {
  const dev = reactBuild === 'development';
  const resolve = (name: string): string => {
    if (name === 'react-18') return dev ? 'react-18.development.js' : 'react-18.production.min.js';
    if (name === 'react-dom-18') return dev ? 'react-dom-18.development.js' : 'react-dom-18.production.min.js';
    return name;
  };
  return (libManifest.page as string[])
    .map((n) => '  <script src="/libs/' + resolve(n) + '">' + SC)
    .join('\n');
}


/**
 * Legacy slides artifacts stored browser-renderable HTML in content.code;
 * the current pipeline stores python-pptx source that only the backend can
 * execute. Only markup may be injected into the slides iframe — Python
 * dumped into a <body> renders as a wall of source text.
 */
export function isHtmlSlidesCode(code: string): boolean {
  return /<\s*(!doctype|html|head|body|script|style|div|section)\b/i.test(code || '');
}

function buildSlidesHtml(data: ArtifactIframeData, code: string): string {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="/libs/tailwindcss-3.4.16.js">${SC}
  <style>
    html, body { height: 100%; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; }
    .slide { transition: opacity 0.3s ease-in-out; }
  </style>
</head>
<body class="bg-slate-900">
  <script>window.ARTIFACT_DATA = ${JSON.stringify(data)};${SC}

  ${code}
</body>
</html>`;
}


function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * The "What this means" section, as plain markup inside the artifact document.
 *
 * Returns '' when there is nothing to say — an empty shell under a dashboard is
 * worse than no section, and every artifact made before insights existed has no
 * payload at all.
 *
 * ★Escaped, not trusted. The text is model-written and lands in a document that
 * also runs the model's own code; an unescaped finding could close the section
 * and open a script tag.
 */
function insightsSection(
  insights: ArtifactInsightsPayload | null | undefined,
  visualizations: unknown[],
): string {
  if (!insights) return '';
  const headline = (insights.headline || '').trim();
  const findings = (insights.findings || []).filter((f) => (f?.text || '').trim());
  if (!headline && !findings.length) return '';

  const titleOf = (vizId?: string): string => {
    if (!vizId) return '';
    const hit = (visualizations as Array<Record<string, any>>).find((v) => v && v.id === vizId);
    const t = hit && (hit.title || hit.name);
    return typeof t === 'string' ? t : '';
  };

  const bullets = findings
    .map((f) => {
      const src = titleOf(f.viz_id);
      const cite = src
        ? `<span class="bow-insight-src">${escapeHtml(src)}</span>`
        : '';
      return `<li>${escapeHtml((f.text || '').trim())}${cite}</li>`;
    })
    .join('');

  // ★Dropped findings are stated, not hidden. A narrative that lost four of
  // five points is not a short one, it is a warning.
  const rejected = (insights.rejected_count || 0) > 0
    ? `<p class="bow-insight-rejected">${insights.rejected_count} finding(s) were dropped for citing a figure that is not in the data.</p>`
    : '';

  return `
  <section id="artifact-insights" data-polish-ignore="true">
    <div class="bow-insight-label">What this means</div>
    ${headline ? `<p class="bow-insight-headline">${escapeHtml(headline)}</p>` : ''}
    ${bullets ? `<ul class="bow-insight-list">${bullets}</ul>` : ''}
    ${rejected}
  </section>`;
}

const INSIGHTS_CSS = `
    /* ★Scoped hard. The dashboard above is model-written Tailwind and will
       happily restyle a bare <section>; every rule here is prefixed. */
    #artifact-insights {
      border-top: 1px solid #e5e7eb;
      background: #ffffff;
      padding: 16px 20px 20px;
      font-family: system-ui, -apple-system, sans-serif;
      color: #111827;
    }
    #artifact-insights .bow-insight-label {
      font-size: 10px; font-weight: 700; letter-spacing: .12em;
      text-transform: uppercase; color: #6b7280;
    }
    #artifact-insights .bow-insight-headline {
      margin: 6px 0 0; font-size: 15px; font-weight: 600; line-height: 1.45;
    }
    #artifact-insights .bow-insight-list {
      margin: 10px 0 0; padding-left: 18px;
      display: flex; flex-direction: column; gap: 5px;
    }
    #artifact-insights .bow-insight-list li {
      font-size: 12.5px; line-height: 1.55; color: #4b5563;
    }
    #artifact-insights .bow-insight-src {
      margin-left: 6px; font-size: 10px; color: #9ca3af; white-space: nowrap;
    }
    #artifact-insights .bow-insight-rejected {
      margin: 10px 0 0; font-size: 10px; color: #b45309;
    }`;

function polishScript(): string {
  return `
    // Polish mode: element pick, highlight & custom cursor
    (function() {
      var polishActive = false;
      var currentHighlight = null;

      var polishStyle = document.createElement('style');
      polishStyle.textContent = [
        '.__polish-highlight { outline: 2px solid #6366f1 !important; outline-offset: 2px; }',
        '.__polish-active { cursor: crosshair !important; }',
        '.__polish-active * { cursor: crosshair !important; }',
        '.__polish-cursor { position: fixed; pointer-events: none; z-index: 99999; display: none; }',
        '.__polish-cursor-inner { display: flex; align-items: center; gap: 6px; background: #4f46e5; color: white; font-size: 12px; font-weight: 500; font-family: system-ui, sans-serif; padding: 5px 10px 5px 8px; border-radius: 20px; box-shadow: 0 4px 12px rgba(79,70,229,0.35); white-space: nowrap; }'
      ].join('\\n');
      document.head.appendChild(polishStyle);

      var cursorEl = document.createElement('div');
      cursorEl.className = '__polish-cursor';
      cursorEl.innerHTML = '<div class="__polish-cursor-inner"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.37 2.63 14 7l-1.59-1.59a2 2 0 0 0-2.82 0L8 7l9 9 1.59-1.59a2 2 0 0 0 0-2.82L17 10l4.37-4.37a2.12 2.12 0 1 0-3-3Z"/><path d="M9 8c-2 3-4 3.5-7 4l8 10c2-1 6-5 6-7"/><path d="M14.5 17.5 4.5 15"/></svg>Click to select</div>';
      document.body.appendChild(cursorEl);

      function onMouseMove(e) {
        cursorEl.style.left = (e.clientX + 12) + 'px';
        cursorEl.style.top = (e.clientY + 12) + 'px';
      }

      function snapToMeaningful(el) {
        var selfTag = (el.tagName || '').toLowerCase();
        if (/^(h[1-6]|table|ul|ol|img|svg|canvas|section|article|header|footer|nav|main)$/.test(selfTag)) {
          return el;
        }
        var node = el;
        var maxDepth = 6;
        while (node && node !== document.body && node.id !== 'root' && maxDepth-- > 0) {
          var cls = node.className || '';
          if (typeof cls === 'string' && (
            /rounded-(lg|xl|2xl)/.test(cls) ||
            /shadow/.test(cls) ||
            /\\bp-[4-9]\\b/.test(cls) ||
            /\\bp-1[0-9]/.test(cls) ||
            node.getAttribute('role') ||
            node.hasAttribute('data-section') ||
            node.hasAttribute('data-card')
          )) {
            return node;
          }
          if (node.parentElement && node.parentElement !== document.body && node.parentElement.id !== 'root') {
            node = node.parentElement;
          } else {
            break;
          }
        }
        return el;
      }

      // ★Anything under [data-polish-ignore] is off-limits. Polish sends the
      // picked element back so the MODEL can rewrite it — which only makes
      // sense for markup the model wrote. The "What this means" section is
      // composed server-side from verified figures and is re-emitted on every
      // render, so a rewrite of it would be discarded, and asking for one
      // would put verified numbers back in front of a model to restate.
      //
      // ★It was also the single easiest thing on the page to select:
      // snapToMeaningful returns any <section> on sight, and the narrative is
      // a <section>. The attribute existed on the markup already and NOTHING
      // read it — emitted in one place, honoured in none.
      function isPolishIgnored(el) {
        return !!(el && el.closest && el.closest('[data-polish-ignore]'));
      }

      function onHover(e) {
        if (!polishActive) return;
        if (currentHighlight) currentHighlight.classList.remove('__polish-highlight');
        if (isPolishIgnored(e.target)) { currentHighlight = null; return; }
        var target = snapToMeaningful(e.target);
        target.classList.add('__polish-highlight');
        currentHighlight = target;
      }
      function onOut(e) {
        if (currentHighlight) currentHighlight.classList.remove('__polish-highlight');
        currentHighlight = null;
      }
      function onClick(e) {
        if (!polishActive) return;
        e.preventDefault();
        e.stopPropagation();
        // ★Swallowed, not passed through: the click is cancelled but polish
        // mode STAYS on, so a mis-aimed click on the narrative costs nothing
        // and the next click on the dashboard still works.
        if (isPolishIgnored(e.target)) return;
        var target = snapToMeaningful(e.target);
        var rect = target.getBoundingClientRect();
        if (currentHighlight) currentHighlight.classList.remove('__polish-highlight');
        polishActive = false;
        document.body.classList.remove('__polish-active');
        cursorEl.style.display = 'none';
        document.removeEventListener('mousemove', onMouseMove, true);
        window.parent.postMessage({
          type: 'POLISH_ELEMENT_SELECTED',
          element: {
            tag: target.tagName,
            classes: target.className.replace(/__polish-highlight/g, '').trim(),
            text: (target.textContent || '').slice(0, 100).trim(),
            htmlSnippet: target.outerHTML.replace(/ class="[^"]*__polish[^"]*"/g, function(m) { return m.replace(/__polish-highlight/g, '').replace(/\\s+/g, ' '); }).slice(0, 500),
            rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height }
          }
        }, '*');
      }

      window.addEventListener('message', function(e) {
        if (e.data && e.data.type === 'POLISH_ENTER') {
          polishActive = true;
          document.body.classList.add('__polish-active');
          cursorEl.style.display = 'block';
          document.addEventListener('mousemove', onMouseMove, true);
          document.body.addEventListener('mouseover', onHover, true);
          document.body.addEventListener('mouseout', onOut, true);
          document.body.addEventListener('click', onClick, true);
        } else if (e.data && e.data.type === 'POLISH_EXIT') {
          polishActive = false;
          document.body.classList.remove('__polish-active');
          cursorEl.style.display = 'none';
          document.removeEventListener('mousemove', onMouseMove, true);
          if (currentHighlight) currentHighlight.classList.remove('__polish-highlight');
          currentHighlight = null;
          document.body.removeEventListener('mouseover', onHover, true);
          document.body.removeEventListener('mouseout', onOut, true);
          document.body.removeEventListener('click', onClick, true);
        }
      });
    })();
`;
}

function errorBoundaryScript(): string {
  return `
    // Error reporting: forward compile/runtime errors to the parent.
    window.__artifactErrorSent = false;
    function reportArtifactError(msg) {
      if (window.__artifactErrorSent) return;
      window.__artifactErrorSent = true;
      window.parent.postMessage({ type: 'ARTIFACT_ERROR', payload: { message: msg } }, '*');
    }

    window.onerror = function(msg, source, line, col, err) {
      var message = (err && err.message) || String(msg);
      if (message.indexOf('ResizeObserver') !== -1) return false;
      reportArtifactError(message);
    };
    window.addEventListener('unhandledrejection', function(e) {
      reportArtifactError(e.reason && e.reason.message ? e.reason.message : String(e.reason));
    });

    // Wrap ReactDOM.render with an error boundary so a thrown component
    // surfaces via reportArtifactError instead of producing a blank iframe.
    (function() {
      class ArtifactErrorBoundary extends React.Component {
        constructor(props) { super(props); this.state = { hasError: false }; }
        static getDerivedStateFromError() { return { hasError: true }; }
        componentDidCatch(error) { reportArtifactError(error.message || String(error)); }
        render() { return this.state.hasError ? null : this.props.children; }
      }
      var origRender = ReactDOM.render;
      ReactDOM.render = function(element, container) {
        var wrapped = React.createElement(ArtifactErrorBoundary, null, element);
        return origRender.call(ReactDOM, wrapped, container);
      };
    })();
`;
}

function readySignalScript(): string {
  return `
    // After Babel processes text/babel scripts, signal readiness to the parent.
    window.addEventListener('DOMContentLoaded', function() {
      setTimeout(function() {
        if (window.__artifactErrorSent) return;
        var root = document.getElementById('root');
        if (root && root.children.length > 0) {
          window.parent.postMessage({ type: 'ARTIFACT_READY' }, '*');
        } else {
          reportArtifactError('Dashboard code did not render any content');
        }
      }, 500);
    });
`;
}

export function buildArtifactIframeHtml(opts: ArtifactIframeOptions): string {
  const mode = opts.mode ?? 'page';
  if (mode === 'slides') return buildSlidesHtml(opts.data, opts.code);

  const loadingLabel = opts.loadingLabel ?? 'Loading...';
  const reactBuild = opts.reactBuild ?? 'production';

  const embeddedData = JSON.stringify(opts.data);
  const polish = opts.polishMode ? polishScript() : '';

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
${pageLibTags(reactBuild)}
  <style>
    /* ★#root gets NO height rule. It grows with its content and the narrative
       follows AFTER all of it, in normal flow. Three attempts got here:
         - body as a flex column with #root{flex:1} gave #root 753px and the
           section 207px, which added up — and cost every dashboard 207px of
           height for good, leaving it to scroll inside itself.
         - #root{height:100vh} pinned the box to one viewport while the content
           OVERFLOWED it, so the section landed on top of the overflow. Rect
           comparison said no overlap, because a bounding box does not include
           what spills out of it. Only the screenshot showed the collision.
         - #root{min-height:100vh} broke nothing and still had to go: it padded
           short dashboards out to a full viewport, opening a white hole
           BETWEEN the dashboard and the narrative — 532px on the shortest, and
           392px on one with no narrative at all, where it bought nothing.
       ★Measured on all 19 stored page artifacts, rendered with the rule and
       without: 15 pixel-identical, and the 4 that differed all read better
       without it. Nothing collapsed — the fear that a bare-auto #root would
       kill h-full / height:100% panels was zero-for-19 in both modes. */
    html { height: 100%; }
    body { min-height: 100%; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; }
${INSIGHTS_CSS}
  </style>
</head>
<body>
  <div id="root"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#9ca3af;">${loadingLabel}</div></div>

  <script>window.ARTIFACT_DATA = ${embeddedData};${SC}
  <script src="/libs/artifact-globals.js?v=${ARTIFACT_GLOBALS_VERSION}">${SC}

  <script>${polish}${errorBoundaryScript()}${SC}

  ${opts.code}

  <script>${readySignalScript()}${SC}
${insightsSection(opts.insights, opts.data.visualizations || [])}
</body>
</html>`;
}
