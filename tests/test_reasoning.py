"""Reasoning tag handling: the tags must be real angle brackets."""

from __future__ import annotations

from custom_components.nous_research.const import REASONING_SYSTEM_PROMPT
from custom_components.nous_research.entity import (
    PREFILL_THINKING,
    THINKING_CLOSE_TAGS,
    THINKING_OPEN_TAGS,
    _split_reasoning,
)

# Built from code points on purpose: an editor or a copy step that eats angle
# brackets around "think" must not be able to quietly rewrite this expectation.
LT, GT = chr(60), chr(62)
OPEN = f"{LT}think{GT}"
CLOSE = f"{LT}/think{GT}"
OPEN_LONG = f"{LT}thinking{GT}"
CLOSE_LONG = f"{LT}/thinking{GT}"


def test_prefill_opens_a_real_reasoning_block() -> None:
    """The prefill must put the model inside the block it is asked to open."""
    assert PREFILL_THINKING == OPEN
    assert PREFILL_THINKING in THINKING_OPEN_TAGS
    assert CLOSE in THINKING_CLOSE_TAGS
    assert OPEN_LONG in THINKING_OPEN_TAGS
    assert CLOSE_LONG in THINKING_CLOSE_TAGS


def test_documented_prompt_names_both_tags() -> None:
    """The prompt tells the model which tags to use, so they have to be there."""
    assert OPEN in REASONING_SYSTEM_PROMPT
    assert CLOSE in REASONING_SYSTEM_PROMPT


def test_the_word_thinking_in_prose_stays_visible() -> None:
    """A normal answer is never mistaken for the start of a reasoning block."""
    answer = "I'm thinking the hallway light is still on."

    text, thinking, in_reasoning, carry = _split_reasoning(answer, False, "")

    assert text == answer
    assert thinking == ""
    assert in_reasoning is False
    assert carry == ""


def test_an_inline_reasoning_block_is_split_out() -> None:
    """Text before the tag is the answer, the inside is thinking."""
    text, thinking, in_reasoning, carry = _split_reasoning(
        f"Sure. {OPEN}weigh the options", False, ""
    )

    assert text == "Sure. "
    assert thinking == "weigh the options"
    assert in_reasoning is True
    assert carry == ""


def test_a_prefilled_reply_keeps_its_answer() -> None:
    """With the prefill the model starts inside the block and closes it herself."""
    text, thinking, in_reasoning, carry = _split_reasoning(
        f"weigh the options{CLOSE}The hallway light is on.", True, ""
    )

    assert thinking == "weigh the options"
    assert text == "The hallway light is on."
    assert in_reasoning is False
    assert carry == ""


def test_a_closing_tag_cut_by_streaming_is_held_back() -> None:
    """Half a tag must not leak into the answer."""
    text, thinking, in_reasoning, carry = _split_reasoning(
        f"weigh it over{CLOSE[:4]}", True, ""
    )

    assert text == ""
    assert thinking == "weigh it over"
    assert in_reasoning is True
    assert carry == CLOSE[:4]

    text, thinking, in_reasoning, carry = _split_reasoning(
        f"{CLOSE[4:]}Done.", True, carry
    )

    assert text == "Done."
    assert thinking == ""
    assert in_reasoning is False
    assert carry == ""
