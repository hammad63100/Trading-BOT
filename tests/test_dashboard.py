"""
Tests for Web Dashboard Endpoints
==================================
Verifies that all API routes and HTML views respond properly.
"""

import pytest
from dashboard.app import app, update_bot_status, get_bot_status


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_dashboard_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Trading Command Center" in response.data or b"Dashboard" in response.data


def test_api_status_endpoint(client):
    update_bot_status(mode="TEST_DEMO", symbol="XAUUSDm", running=True)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["mode"] == "TEST_DEMO"
    assert data["symbol"] == "XAUUSDm"
    assert data["running"] is True
    assert "uptime" in data


def test_api_trades_endpoint(client):
    response = client.get("/api/trades")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_positions_endpoint(client):
    response = client.get("/api/positions")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_equity_curve_endpoint(client):
    response = client.get("/api/equity_curve")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_risk_status_endpoint(client):
    response = client.get("/api/risk_status")
    assert response.status_code == 200
    data = response.get_json()
    assert "max_daily_loss" in data or "daily_loss" in data or isinstance(data, dict)

