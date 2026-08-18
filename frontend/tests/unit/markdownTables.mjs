import assert from 'node:assert/strict'

import { repairMarkdownTables } from '../../utils/markdownTables.ts'

// --- agent tables that forgot their delimiter row -----------------------------
//
// 26 of 195 stored assistant messages containing tables rendered as raw pipe
// text because the model omitted the `|---|---|` line. The renderer is
// vendored, so the string is repaired before it reaches the component.

// The measured live failure: header, then rows, no delimiter.
{
	const broken = [
		'| Metric | City Mart Retail Figure | Industry Benchmark | Performance Assessment |',
		'| **Discount Rate (% of Gross)** | **3.14%** | 5.0%-8.0% | **Ahead in Margin Discipline** |',
	].join('\n')
	const fixed = repairMarkdownTables(broken).split('\n')
	assert.equal(fixed.length, 3)
	assert.equal(fixed[1], '| --- | --- | --- | --- |')
	// The header and the data are untouched, only a line was added between them.
	assert.equal(fixed[0], broken.split('\n')[0])
	assert.equal(fixed[2], broken.split('\n')[1])
}

// Every row of a long table must NOT get its own delimiter: each body row is
// itself a pipe-fenced line followed by another one.
{
	const broken = ['| A | B |', '| 1 | 2 |', '| 3 | 4 |', '| 5 | 6 |'].join('\n')
	const fixed = repairMarkdownTables(broken)
	assert.equal(fixed.split('\n').filter((l) => l.includes('---')).length, 1)
	assert.equal(fixed, ['| A | B |', '| --- | --- |', '| 1 | 2 |', '| 3 | 4 |', '| 5 | 6 |'].join('\n'))
}

// --- idempotence -------------------------------------------------------------

// An already-correct table comes back byte-identical, including alignment
// colons and a trailing newline.
{
	const ok = '| A | B |\n|:---|---:|\n| 1 | 2 |\n'
	assert.equal(repairMarkdownTables(ok), ok)
	assert.equal(repairMarkdownTables(repairMarkdownTables(ok)), ok)
}

// Running the repair twice is the same as running it once.
{
	const broken = '| A | B |\n| 1 | 2 |'
	const once = repairMarkdownTables(broken)
	assert.equal(repairMarkdownTables(once), once)
}

// --- ★THE CRITICAL ONE: code fences are not tables ---------------------------
//
// A pipe inside a fence is a shell pipeline or an ASCII diagram. Inserting a
// delimiter there corrupts the user's code, which is far worse than the bug
// being fixed.
{
	const fenced = [
		'Here is the command:',
		'',
		'```bash',
		'| Name | Count |',
		'| foo | 3 |',
		'cat x | grep y | wc -l',
		'```',
	].join('\n')
	assert.equal(repairMarkdownTables(fenced), fenced)
}

// Tilde fences too, and a fence whose info string names a language.
{
	const fenced = ['~~~text', '| A | B |', '| 1 | 2 |', '~~~'].join('\n')
	assert.equal(repairMarkdownTables(fenced), fenced)
}

// A real table AFTER a fence still gets repaired — the fence state must close.
{
	const mixed = ['```', '| A | B |', '```', '', '| X | Y |', '| 1 | 2 |'].join('\n')
	const fixed = repairMarkdownTables(mixed).split('\n')
	assert.equal(fixed.length, 7)
	assert.equal(fixed[1], '| A | B |') // inside the fence, untouched
	assert.equal(fixed[5], '| --- | --- |') // after it, repaired
}

// ★Inline code holding a pipe does not inflate the column count. The backticks
// must be in the HEADER to test anything — the delimiter's width is derived from
// the header alone, so a backticked body row exercises none of this.
{
	const broken = '| `a | b` | Meaning |\n| x | alternation |'
	const fixed = repairMarkdownTables(broken).split('\n')
	assert.equal(fixed[1], '| --- | --- |') // two cells, not three
}

// A double-backtick span may itself contain a single backtick, and only a run of
// the same length closes it.
{
	const broken = '| ``a | b`` | Meaning |\n| x | y |'
	assert.equal(repairMarkdownTables(broken).split('\n')[1], '| --- | --- |')
}

// An escaped pipe is content, not a cell boundary.
{
	const broken = '| a \\| b | Meaning |\n| x | y |'
	assert.equal(repairMarkdownTables(broken).split('\n')[1], '| --- | --- |')
}

// A line that is entirely inline code is not a header at all.
{
	const src = '`| A | B |`\n`| 1 | 2 |`'
	assert.equal(repairMarkdownTables(src), src)
}

// --- surrounding content -----------------------------------------------------

// A table immediately following a paragraph (no blank line) is still repaired,
// and the paragraph is left alone.
{
	const src = 'Here are the numbers:\n| A | B |\n| 1 | 2 |'
	assert.equal(repairMarkdownTables(src), 'Here are the numbers:\n| A | B |\n| --- | --- |\n| 1 | 2 |')
}

// A blank line between header and rows means they are separate blocks; a
// delimiter inserted there would render an empty table, so nothing happens.
{
	const src = '| A | B |\n\n| 1 | 2 |'
	assert.equal(repairMarkdownTables(src), src)
}

// Ragged rows take the HEADER's column count, not the row's.
{
	const src = '| A | B | C | D |\n| 1 | 2 |'
	assert.equal(repairMarkdownTables(src).split('\n')[1], '| --- | --- | --- | --- |')
}

// --- things that are not tables ----------------------------------------------

// A single pipe line with nothing after it is left untouched.
assert.equal(repairMarkdownTables('| A | B |'), '| A | B |')
assert.equal(repairMarkdownTables('| A | B |\n\nsome prose'), '| A | B |\n\nsome prose')

// Prose that merely contains pipes.
assert.equal(repairMarkdownTables('use a | b in a regex\nand also c | d'), 'use a | b in a regex\nand also c | d')

// A one-cell pipe line is not a header.
assert.equal(repairMarkdownTables('| just this |\n| and this |'), '| just this |\n| and this |')

// A 4-space indented block is code, not a table.
{
	const src = '    | A | B |\n    | 1 | 2 |'
	assert.equal(repairMarkdownTables(src), src)
}

// Empty / pipe-free input short-circuits. Call sites pass `value || ''`, so the
// empty string is the shape an absent value actually arrives in.
assert.equal(repairMarkdownTables(''), '')
assert.equal(repairMarkdownTables('no pipes here'), 'no pipes here')

// --- line endings ------------------------------------------------------------
//
// Content arrives over SSE and out of Postgres; a stray \r must not defeat the
// `endsWith('|')` test, and the inserted line must match its neighbours.
{
	const fixed = repairMarkdownTables('| A | B |\r\n| 1 | 2 |\r\n')
	assert.equal(fixed, '| A | B |\r\n| --- | --- |\r\n| 1 | 2 |\r\n')
}

console.log('agent tables missing a delimiter row are repaired; code fences are not')
