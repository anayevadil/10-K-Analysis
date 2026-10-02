import pytest

from tenk.demo import example_company_facts
from tenk.financials.statements import build_statements


@pytest.fixture
def example_facts():
    return example_company_facts()


@pytest.fixture
def statements(example_facts):
    return build_statements(example_facts, years=5)
