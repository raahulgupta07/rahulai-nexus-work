from typing import Annotated, Optional
from pydantic import BaseModel, BeforeValidator, Field

from ._lenient import objects_from_scalars


class ClarifyQuestion(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        description="The question shown to the user. Keep it concise.",
    )
    options: Optional[list[str]] = Field(
        None,
        description=(
            "Clickable answer choices rendered as selectable chips. "
            "Omit for free-form text input. "
            "Include an 'Other…' entry when the list may not be exhaustive."
        ),
    )
    multi_select: bool = Field(
        False,
        description=(
            "Set true when the user may pick several of `options` at once "
            "(select-all-that-apply, e.g. 'which metrics should the dashboard include?'). "
            "Ignored for free-form questions. Defaults to single choice."
        ),
    )


class ClarifyInput(BaseModel):
    """Input schema for the clarify tool.

    Each entry in ``questions`` becomes an interactive form row: a chip-picker
    when ``options`` is supplied, a text field otherwise. All questions are
    shown at once; the user submits all answers in a single reply.

    ★``questions`` is deliberately lenient about the SHAPE it is handed, because
    the strict version failed **11 of 14 live calls — 79%**. Models send a list
    of plain strings (``["What would you like ranked as the best?"]``, 9
    failures), the same object under the name a person would give the field
    (``[{"question": …, "options": […]}]``, 2 failures), or the whole thing
    JSON-encoded as a string. And this is the tool the agent reaches for when a
    question is too vague to answer, so every one of those failures is
    user-visible: the person waits ~25s and gets "Unable to complete task due to
    repeated tool validation errors" instead of being asked anything, while the
    completion is still recorded ``status=success``.

    ★:class:`ClarifyQuestion` is untouched and stays the canonical shape — the
    validator only normalises on the way in, so genuine nonsense (a number, an
    object with no text) is still rejected by the model itself and nothing
    downstream sees a widened type.
    """

    questions: Annotated[
        list[ClarifyQuestion],
        BeforeValidator(objects_from_scalars(text_key="text", aliases={"question": "text"})),
    ] = Field(
        ...,
        min_length=1,
        description="One or more questions to ask the user before proceeding.",
    )
    context: Optional[str] = Field(
        None,
        description="Brief internal note about why clarification is needed (not shown to the user).",
    )


class ClarifyOutput(BaseModel):
    """Output schema for the clarify tool."""

    status: str = Field(
        default="awaiting_response",
        description="Status of the clarification request.",
    )
