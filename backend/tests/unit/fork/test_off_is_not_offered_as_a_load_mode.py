""""Disabled" is not offered as a load mode in the instruction editors.

It read as the harder off-switch while being the weaker one: an instruction on
`load_mode: 'disabled'` is left out of the assembled context, but it still
displays as Active and is still returned by the agent's `search_instructions`,
which filters on *status* alone. So the model could fetch and follow an
instruction the UI said was disabled. Setting the status to Inactive is the
switch that actually takes it out of play.

★The option is not deleted, it is conditional: it stays listed while an
instruction is already on that value, so a legacy row shows its real setting
rather than silently displaying as something else — and loses the option once
moved to Always or Smart. That conditionality is the whole fix, which is why
this guard checks the option list is a `computed`, not merely that the literal
array shrank.

MEASURED, guard logic run against `git show HEAD:<path>` and the working tree:

    test_the_option_list_is_conditional        HEAD fail -> now pass
    test_disabled_is_not_unconditionally_offered  HEAD fail -> now pass

Upstream: 3d12fcfa. That commit also touches KnowledgeExplorer.vue, which this
agent does not own; only the editor half is pinned here.
"""

from __future__ import annotations

import re

from vue_source import read_source

PRIVATE_EDITOR = "components/InstructionPrivateCreateComponent.vue"


def _load_mode_options(src: str) -> str:
    start = src.index("const loadModeOptions")
    return src[start : src.index("\n})", start) + 3] if "\n})" in src[start:] else src[start : start + 1200]


def test_the_option_list_is_conditional():
    src = read_source(PRIVATE_EDITOR)
    assert re.search(r"const loadModeOptions = computed\(", src), (
        "loadModeOptions is a static array. It must be a computed so the "
        "'Disabled' entry can appear only for an instruction already on that "
        "value — deleting it outright makes a legacy row display as something "
        "it is not."
    )


def test_disabled_is_not_unconditionally_offered():
    block = _load_mode_options(read_source(PRIVATE_EDITOR))
    assert "'disabled'" in block, (
        "the 'disabled' option vanished entirely; legacy rows on that value "
        "then render as some other mode."
    )
    guard = re.search(r"if\s*\(props\.sharedForm\.load_mode === 'disabled'\)", block)
    assert guard, (
        "'Disabled' is offered unconditionally. It looks like the harder "
        "off-switch and is the weaker one — search_instructions still returns "
        "the instruction, because it filters on status."
    )
    assert block.index("'always'") < guard.start(), (
        "Always/Smart must be the unconditional options; only Disabled is gated."
    )
