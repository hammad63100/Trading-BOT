"""
Tests for Automation & Health Diagnostic Scripts
=================================================
Verifies health_check and performance_report utilities.
"""

from scripts.health_check import check_config, check_database, check_disk_and_logs
from scripts.performance_report import fetch_trades, format_report, generate_ascii_chart


def test_health_check_subroutines():
    assert check_config() is True
    assert check_database() is True
    assert check_disk_and_logs() is True


def test_performance_report_generation():
    trades = fetch_trades(days=None)
    assert isinstance(trades, list)

    report = format_report(trades, days=None)
    assert "XAU/USD BOT PERFORMANCE REPORT" in report
    assert "SUMMARY METRICS" in report


def test_ascii_chart_builder():
    chart = generate_ascii_chart([100.0, 105.0, 95.0, 110.0])
    assert "$" in chart
    assert "Trade" in chart

