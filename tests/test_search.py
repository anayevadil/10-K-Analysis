from tenk.demo import example_10k_sections
from tenk.nlp.search import TenKIndex, split_passages, tokenize


def test_tokenize_drops_stopwords_and_folds_plurals():
    assert tokenize("What are the company's chip suppliers?") == ["chip", "supplier"]
    assert tokenize("Liabilities and Business") == ["liability", "business"]


def test_passages_keep_their_section_and_whole_lines():
    sections = {"business": "Line one.\nLine two.", "mdna": "Only line."}
    passages = split_passages(sections, words=3)
    assert [(p.section, p.text) for p in passages] == [
        ("business", "Line one.\nLine two."),
        ("mdna", "Only line."),
    ]
    assert passages[0].section_label == "Item 1. Business"


def test_search_ranks_the_passage_with_the_rare_words_first():
    index = TenKIndex(example_10k_sections())
    top, _ = index.search("chip suppliers shortage")[0]
    assert top.section == "risk_factors"
    top, _ = index.search("why was 2023 net income restated")[0]
    assert top.section == "mdna"
    assert "income tax provision" in top.text


def test_search_returns_nothing_for_unknown_words():
    assert TenKIndex(example_10k_sections()).search("cryptocurrency") == []


def test_empty_index():
    assert TenKIndex({}).search("anything") == []
