import pytest
from example_facts import build_example_facts

from tenk.financials.statements import build_statements


@pytest.fixture
def example_facts():
    return build_example_facts()


@pytest.fixture
def statements(example_facts):
    return build_statements(example_facts, years=5)
