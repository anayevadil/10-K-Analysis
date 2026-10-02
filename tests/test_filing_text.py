from pathlib import Path

from tenk.sources.filing_text import extract_sections, filing_sections, html_to_text

HTML = (Path(__file__).parent / "fixtures" / "10k_example.htm").read_text()


def test_html_to_text_joins_inline_tags_and_drops_hidden_xbrl():
    text = html_to_text(HTML)
    assert (
        "Example Corp makes building sensors and sells monitoring software to office landlords."
        in text.splitlines()
    )
    assert "hidden XBRL header" not in text
    assert "\xa0" not in text
    assert "Net sales $ 1,300 $ 1,200" in text.splitlines()


def test_sections_skip_the_table_of_contents():
    sections = filing_sections(HTML)
    assert set(sections) == {"business", "risk_factors", "mdna"}
    assert sections["business"].startswith("Item 1. Business\nExample Corp makes")
    assert "Hardware and Software" in sections["business"]
    assert "chip suppliers" in sections["risk_factors"]
    assert "Unresolved Staff Comments" not in sections["risk_factors"]
    assert "driven by software subscriptions" in sections["mdna"]
    assert "fixed rate" not in sections["mdna"]  # Item 7A is a separate section


def test_cross_reference_inside_a_paragraph_is_not_a_heading():
    sections = filing_sections(HTML)
    assert sections["mdna"].startswith("Item 7. Management")


def test_missing_section_is_left_out():
    text = "Item 1. Business\nWe sell things.\nItem 2. Properties\nOne office."
    assert extract_sections(text) == {"business": "Item 1. Business\nWe sell things."}


def test_heading_variants():
    text = "ITEM 1A — RISK FACTORS\nRisky.\nItem 1B: Unresolved Staff Comments\nNone."
    assert extract_sections(text)["risk_factors"] == "ITEM 1A — RISK FACTORS\nRisky."
