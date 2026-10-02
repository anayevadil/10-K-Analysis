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
