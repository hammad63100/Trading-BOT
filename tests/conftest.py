import pytest


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    from utils import trade_journal
    from config import settings
    monkeypatch.setattr(trade_journal, "DB_PATH", tmp_path / "journal.db")
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "ALERT_CHAT_ID", "")
    trade_journal.initialize_journal()
    # Unit tests must never initialize a real terminal or contact a broker.
    import sys
    from unittest.mock import Mock
    api = Mock()
    api.initialize.side_effect = AssertionError("Real broker connection forbidden in tests")
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
