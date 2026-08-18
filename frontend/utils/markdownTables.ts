// Best-effort repair for the single most common LLM-authored Markdown mistake
// in agent answers: a table written WITHOUT its delimiter row.
//
// GitHub-Flavoured Markdown requires a `|---|---|` line directly under the
// header; every parser (markstream-vue on the report/share pages, MDC in the
// completion component) treats a header row that lacks one as an ordinary
// paragraph that happens to contain pipe characters. What the user sees is the
// raw source:
//
//   | Metric | City Mart Retail Figure | Industry Benchmark |
//   | **Discount Rate** | **3.14%** | 5.0%-8.0% |
//
// Measured on the live instance 2026-08-17: 26 of 195 stored assistant
// messages containing tables (13%) were missing that line. The renderer is
// vendored (`markstream-vue`), so the parser is not ours to change — the only
// place to fix it is the markdown STRING, before it reaches the component.
//
// ★This runs on AGENT-AUTHORED text only. Never apply it to what a user typed:
// silently rewriting someone's own message as they look at it is surprising,
// and a user pasting pipe-delimited data is not asking for a table.
//
// Scope, deliberately narrow — the way a fix like this causes damage is by
// "repairing" something that was never a table:
//   - fenced code blocks (``` and ~~~) are skipped entirely. A pipe inside a
//     fence is a shell pipeline or an ASCII diagram, not a table header.
//   - inline code spans are skipped when splitting cells, so a backticked
//     `a | b` neither inflates the column count nor turns a line into a table.
//   - a 4-space-indented line is an indented code block, not a table.
//   - a pipe line with nothing table-shaped after it is left alone.
// Everything else is passed through byte-for-byte, which is what makes the
// function idempotent: correct markdown comes back unchanged.

interface Fence {
	char: string
	len: number
}

// Leading whitespace, and the line with its trailing CR removed. Content
// arrives over SSE and out of Postgres, so CRLF is possible; splitting on \n
// alone would leave a \r that defeats every `endsWith('|')` test below.
function strip(line: string): string {
	return line.replace(/\r$/, '').trim()
}

function indentOf(line: string): string {
	return /^[ \t]*/.exec(line)?.[0] ?? ''
}

/**
 * Split `| a | b |` into its cells, ignoring pipes that are escaped (`\|`) or
 * live inside an inline code span. Assumes the line already passed
 * isTableLine(). Returns the cells with surrounding pipes removed.
 */
function splitCells(trimmed: string): string[] {
	const inner = trimmed.slice(1, -1)
	const cells: string[] = []
	let cur = ''
	let codeLen = 0 // length of the backtick run that opened a span; 0 = outside code
	let i = 0
	while (i < inner.length) {
		const ch = inner[i]
		if (ch === '\\' && inner[i + 1] === '|' && codeLen === 0) {
			cur += '\\|'
			i += 2
			continue
		}
		if (ch === '`') {
			let j = i
			while (inner[j] === '`') j++
			const run = j - i
			// A span closes only on a backtick run of the SAME length (CommonMark).
			if (codeLen === 0) codeLen = run
			else if (run === codeLen) codeLen = 0
			cur += inner.slice(i, j)
			i = j
			continue
		}
		if (ch === '|' && codeLen === 0) {
			cells.push(cur)
			cur = ''
			i++
			continue
		}
		cur += ch
		i++
	}
	cells.push(cur)
	return cells
}

// A line that could belong to a table: pipe-fenced on both sides. Body rows are
// allowed to be ragged, so cell count is not checked here.
function isTableLine(trimmed: string): boolean {
	return trimmed.length >= 2 && trimmed.startsWith('|') && trimmed.endsWith('|')
}

function isDelimiterRow(trimmed: string): boolean {
	if (!isTableLine(trimmed)) return false
	const cells = splitCells(trimmed)
	return cells.length >= 1 && cells.every((c) => /^:?-+:?$/.test(c.trim()))
}

// The header of a table we might be able to rescue: pipe-fenced, at least two
// cells, and not itself a delimiter (a table cannot start with one).
function isHeaderCandidate(trimmed: string): boolean {
	if (!isTableLine(trimmed)) return false
	if (isDelimiterRow(trimmed)) return false
	return splitCells(trimmed).length >= 2
}

// Opening fence, if this line is one. Backtick fences may not carry a backtick
// in their info string; tilde fences may carry anything.
function openingFence(line: string, trimmed: string): Fence | null {
	if (indentOf(line).length >= 4) return null
	const m = /^(`{3,}|~{3,})(.*)$/.exec(trimmed)
	if (!m) return null
	if (m[1][0] === '`' && m[2].includes('`')) return null
	return { char: m[1][0], len: m[1].length }
}

function closesFence(fence: Fence, trimmed: string): boolean {
	const m = /^(`{3,}|~{3,})\s*$/.exec(trimmed)
	return !!m && m[1][0] === fence.char && m[1].length >= fence.len
}

/**
 * Insert the missing GFM delimiter row under any table header that lacks one.
 * Returns the source unchanged when there is nothing to repair — running it on
 * already-correct markdown is a byte-identical no-op.
 *
 * Takes and returns a plain string. Every call site guards on the value being
 * present before it renders anything, so `|| ''` at the call site is
 * unreachable rather than a coercion that could swallow a null.
 */
export function repairMarkdownTables(src: string): string {
	if (!src || !src.includes('|')) return src

	const lines = src.split('\n')
	const out: string[] = []
	let fence: Fence | null = null
	let i = 0

	while (i < lines.length) {
		const line = lines[i]
		const trimmed = strip(line)

		if (fence) {
			out.push(line)
			i++
			if (closesFence(fence, trimmed)) fence = null
			continue
		}

		const opened = openingFence(line, trimmed)
		if (opened) {
			fence = opened
			out.push(line)
			i++
			continue
		}

		const next = i + 1 < lines.length ? strip(lines[i + 1]) : null
		// ★The delimiter must sit IMMEDIATELY under the header — a blank line
		// between them ends the block, so inserting there would produce a
		// zero-row table with the data stranded in a paragraph beneath it. That
		// is worse than the raw pipes, so a blank line means: leave it alone.
		if (
			indentOf(line).length < 4 &&
			isHeaderCandidate(trimmed) &&
			next !== null &&
			isTableLine(next)
		) {
			out.push(line)
			if (!isDelimiterRow(next)) {
				// Column count comes from the HEADER, which is what the parser
				// uses; ragged body rows are padded or truncated by the renderer.
				const cols = splitCells(trimmed).length
				const cr = line.endsWith('\r') ? '\r' : ''
				out.push(`${indentOf(line)}| ${Array(cols).fill('---').join(' | ')} |${cr}`)
			}
			i++
			// ★Consume the whole contiguous run of table lines. Without this the
			// first BODY row is itself a valid header candidate followed by another
			// table line, and every row of a 20-row table gets its own delimiter.
			while (i < lines.length && isTableLine(strip(lines[i]))) {
				out.push(lines[i])
				i++
			}
			continue
		}

		out.push(line)
		i++
	}

	return out.join('\n')
}
