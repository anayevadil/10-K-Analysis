"""The agent loop: Claude decides which tools to call, the app runs them, repeat.

Each question is its own conversation. The loop sends the question with the
tool definitions, runs every tool Claude asks for, sends the results back,
and stops when Claude answers without calling a tool. Every tool call is
recorded as a ``Step`` so the app can show how the answer was reached.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from tenk.agent.prompts import system_prompt
from tenk.agent.tools import TOOLS, Toolbox
from tenk.service import saved_record

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
MAX_MODEL_CALLS = 10
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TRACE_PREVIEW_CHARS = 4000  # tool output kept in the trace shown to users

AGENT_TOOLS = [{**tool, "strict": True} for tool in TOOLS]


@dataclass
class Step:
    tool: str
    input: dict[str, Any]
    output: str
    is_error: bool = False
    seconds: float = 0.0


@dataclass
class AgentAnswer:
    question: str
    answer: str
    steps: list[Step] = field(default_factory=list)
    model: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: str = ""  # why the loop ended early, empty when Claude finished normally

    @property
    def tools_used(self) -> list[str]:
        return [step.tool for step in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentAnswer:
        steps = [Step(**step) for step in data.get("steps", [])]
        return cls(**{**data, "steps": steps})


def saved_answers(ticker: str) -> list[AgentAnswer]:
    """Answers recorded into ``demo_cache/`` by scripts/record_demo.py, if any."""
    record = saved_record(ticker)
    return [AgentAnswer.from_dict(a) for a in record.get("agent", [])] if record else []


def _run_tool(toolbox: Toolbox, name: str, arguments: dict[str, Any]) -> Step:
    started = time.perf_counter()
    try:
        output, is_error = toolbox.run(name, arguments), False
    except Exception as error:  # any failure goes back to Claude, which can retry or explain
        output, is_error = f"Error: {error}", True
    return Step(name, arguments, output, is_error, round(time.perf_counter() - started, 3))


def ask(
    client: Any,
    toolbox: Toolbox,
    ticker: str,
    company: str,
    question: str,
    on_step: Callable[[Step], None] | None = None,
    model: str = MODEL,
) -> AgentAnswer:
    """Answer one question. ``client`` is an ``anthropic.Anthropic``."""
    result = AgentAnswer(question=question, answer="")
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    system = system_prompt(ticker, company)

    while result.model_calls < MAX_MODEL_CALLS:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},  # reuse the growing prefix on every turn
            system=system,
            tools=AGENT_TOOLS,
            messages=messages,
        )
        result.model_calls += 1
        result.model = response.model
        result.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(response.usage, "output_tokens", 0) or 0

        if response.stop_reason == "refusal":
            result.answer = "Claude declined to answer this question."
            result.stopped = "refusal"
            return result
        if response.stop_reason == "max_tokens":
            result.stopped = "max_tokens"
            result.answer = _text(response) or "The answer was cut off. Try a narrower question."
            return result

        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls:
            result.answer = _text(response)
            return result

        # The whole assistant turn (thinking, text and tool calls) goes back unchanged.
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for call in tool_calls:
            step = _run_tool(toolbox, call.name, dict(call.input))
            result.steps.append(step)
            if on_step:
                on_step(step)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": step.output,
                    "is_error": step.is_error,
                }
            )
            step.output = step.output[:TRACE_PREVIEW_CHARS]
        messages.append({"role": "user", "content": tool_results})

    result.stopped = "max_model_calls"
    result.answer = "I couldn't finish within the step limit. Try asking a narrower question."
    return result


def _text(response: Any) -> str:
    """The answer text, skipping anything written before a fallback model took over."""
    text = ""
    for block in response.content:
        if block.type == "fallback":
            text = ""
        elif block.type == "text":
            text += block.text
    return text.strip()
