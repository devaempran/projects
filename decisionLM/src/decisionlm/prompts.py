"""Prompt construction for both tiers, including neutralising of caller-supplied text."""
from __future__ import annotations

import re
from typing import Sequence

from decisionlm import settings


def sanitize(text: str) -> str:
    """Defuse text that would be read as chat-template control tokens. The prompt is tokenized with
    special=True, so a user string containing '<|im_end|>' would otherwise close the turn and let the
    caller inject their own instructions. '<|' becomes '< |' and think markers are broken up too."""
    text = text.replace("<|", "< |")
    return re.sub(r"<(/?)think>", lambda m: "< " + m.group(1) + "think>", text, flags=re.I)


def oneline(text: str) -> str:
    """Option text goes on a single 'X: ...' line; newlines could fake extra options."""
    return " ".join(sanitize(text).split())


def _question_block(context: str, question: str, choices: Sequence[dict]) -> list[str]:
    lines = []
    if context and context.strip():
        lines.append(f"Context: {sanitize(context.strip())}\n")
    lines.append(f"Question: {sanitize(question.strip())}\n")
    lines.append("Choices:")
    for c in choices:
        lines.append(f"{c['label']}: {oneline(c['text'])}")
    return lines


def tier1_prompt(context: str, question: str, choices: Sequence[dict]) -> str:
    """choices: list of {"label", "text"}. ChatML with thinking disabled (Qwen3's documented way to skip
    its <think> block), so the very next token is the answer letter."""
    labels = [c["label"] for c in choices]
    lines = _question_block(context, question, choices)
    lines.append(
        f"\nRespond with exactly one letter ({', '.join(labels)}) and nothing else. "
        f"Do not explain your answer."
    )
    body = "\n".join(lines)
    return (
        f"<|im_start|>system\n{settings.get().system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{body}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def tier2_prompt(context: str, question: str, choices: Sequence[dict]) -> str:
    """Same question, but the assistant turn is left open so the model thinks before answering."""
    lines = _question_block(context, question, choices)
    lines.append("\nThink it through, then finish with a line of the form 'Answer: <letter>'.")
    return (
        f"<|im_start|>system\n{settings.get().system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n" + "\n".join(lines) + "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
