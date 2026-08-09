"""The add-member dialog cannot be double-submitted or closed mid-flight.

Creating a member is several sequential requests — the membership, then each
group assignment, then the quota policy, then three refreshes. Nothing marked
that the dialog was busy except a spinner on the submit button, so the button
was still clickable, the Cancel and X buttons were still live, and the modal
still closed on a backdrop click. A second submit creates a duplicate member; a
mid-flight close leaves the member created with no groups and no quota policy
and no toast to say so.

★This fork's dialog is upstream's invite dialog under a different name — it
posts to `/members/create-user`, not `/members`, and its loading ref is
`creatingUser`, not `inviteLoading`. Upstream's hunk adds `<Spinner>` because
its button had no loading prop; ours already binds `:loading`. The guard is
written against *this* tree's names, so it measures the shipped behaviour rather
than the shape of the upstream patch.

MEASURED, guard logic run against `git show HEAD:<path>` and the working tree:

    test_the_modal_cannot_be_dismissed_mid_flight  HEAD fail -> now pass
    test_every_exit_control_is_disabled            HEAD 1 of 3 -> now 3 of 3
    test_a_second_submit_is_refused                HEAD fail -> now pass

Upstream: 99fed251.
"""

from __future__ import annotations

import re

from vue_source import read_source

MEMBERS = "components/MembersComponent.vue"


def _invite_modal(src: str) -> str:
    start = src.index('<UModal v-model="inviteModalOpen"')
    return src[start : src.index("</UModal>", start)]


def test_the_modal_cannot_be_dismissed_mid_flight():
    src = read_source(MEMBERS)
    opening = src[src.index('<UModal v-model="inviteModalOpen"') :][:200]
    assert re.search(r':prevent-close="creatingUser"', opening), (
        "the modal has no :prevent-close, so a backdrop click or Escape "
        "abandons a create that is already several requests deep."
    )


def test_every_exit_control_is_disabled():
    """Three ways out: the X, Cancel, and submitting again. All three, not one."""
    block = _invite_modal(read_source(MEMBERS))

    x_button = re.search(r"<button[^>]*inviteModalOpen = false[^>]*>", block)
    assert x_button, "could not find the corner close button"
    assert 'disabled="creatingUser"' in x_button.group(0), (
        "the corner X stays live while the create is in flight."
    )

    cancel = re.search(r"<UButton\b(?:(?!</UButton>).)*?inviteModalOpen = false", block, re.DOTALL)
    assert cancel, "could not find the Cancel button"
    assert 'disabled="creatingUser"' in cancel.group(0), (
        "the Cancel button stays live while the create is in flight."
    )

    submit = re.search(r'<UButton\s+type="submit"(?:(?!</UButton>).)*?>', block, re.DOTALL)
    assert submit, "could not find the submit button"
    assert 'disabled="creatingUser"' in submit.group(0), (
        "the submit button is only :loading, which shows a spinner and still "
        "accepts a click — a second submit creates a duplicate member."
    )


def test_a_second_submit_is_refused():
    """The template guard is the visible half. A form submit can also arrive by
    Enter, and a disabled attribute is a suggestion to anyone with devtools."""
    src = read_source(MEMBERS)
    start = src.index("const createUser = async")
    head = src[start : start + 300]
    assert re.search(r"if \(creatingUser\.value\) return", head), (
        "createUser has no re-entrancy guard; only the button's disabled "
        "attribute stands between a fast second Enter and a duplicate member."
    )
    assert head.index("if (creatingUser.value) return") < head.index("creatingUser.value = true")
