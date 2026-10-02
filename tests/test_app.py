from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "app" / "streamlit_app.py")


def run_app():
    return AppTest.from_file(APP, default_timeout=30).run()


def test_start_page_asks_for_a_ticker():
    at = run_app()
    assert not at.exception
    assert "Enter a US ticker" in at.info[0].value


def test_sample_mode_renders_company():
    at = run_app()
    at.toggle[0].set_value(True)
    at.button[0].click().run()
    assert not at.exception
    assert at.header[0].value == "Example Corp (sample data)"
    labels = [m.label for m in at.metric]
    assert labels == ["Revenue", "Net income", "Free cash flow", "Net margin"]
    assert at.metric[0].value == "$1.30B"
    assert len(at.dataframe) == 4


def test_missing_user_agent_shows_error(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    at = run_app()
    at.button[0].click().run()
    assert not at.exception
    assert "User-Agent" in at.error[0].value
