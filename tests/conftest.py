from types import SimpleNamespace

import pytest

from tenk.demo import example_company_facts
from tenk.financials.statements import build_statements
from tenk.nlp.summarize import MODEL


@pytest.fixture
def example_facts():
    return example_company_facts()


@pytest.fixture
def statements(example_facts):
    return build_statements(example_facts, years=5)


def text(value):
    return SimpleNamespace(type="text", text=value)


class FakeClaude:
    """Stands in for anthropic.Anthropic; records each request it receives."""

    def __init__(self, content, stop_reason="end_turn", model=MODEL):
        self.requests = []
        self._response = SimpleNamespace(content=content, stop_reason=stop_reason, model=model)
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def request(self):
        return self.requests[-1] if self.requests else None

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._response


def tool_use(name, tool_input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=tool_input)


def reply(*content, stop_reason=None, model=MODEL, input_tokens=100, output_tokens=20):
    """One Claude response; stop_reason defaults to tool_use when a tool is called."""
    if stop_reason is None:
        calls_tool = any(block.type == "tool_use" for block in content)
        stop_reason = "tool_use" if calls_tool else "end_turn"
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, model=model, usage=usage)


class ScriptedClaude(FakeClaude):
    """Returns the given responses in order, recording each request."""

    def __init__(self, *responses):
        super().__init__([])
        self._responses = list(responses)

    def _create(self, **kwargs):
        # Snapshot messages: the loop keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)
