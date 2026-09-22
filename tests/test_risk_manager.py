"""
Unit Tests for Risk Manager
=============================
Tests position sizing and risk gate logic.
"""

import pytest
from core.risk_manager import position_size


class TestPositionSize:

    def test_basic_sizing(self):
        # $10,000 equity, 0.5% risk, 5.0 SL distance, $10 pip value
        lots = position_size(10000, 0.005, 5.0, 10.0)
        # Risk amount = $50, lots = 50 / (5 * 10) = 1.0
        assert lots == 1.0

    def test_small_account(self):
        # $1,000 equity, 0.5% risk
        lots = position_size(1000, 0.005, 5.0, 10.0)
        # Risk amount = $5, lots = 5 / (5 * 10) = 0.1
        assert lots == 0.1

    def test_minimum_lot_size(self):
        # Unaffordable minimum must be rejected
        lots = position_size(100, 0.005, 10.0, 10.0)
        # Risk amount = $0.50, lots = 0.50 / (10 * 10) = 0.005 â†’ min 0.01
        assert lots == 0.0

    def test_zero_equity(self):
        lots = position_size(0, 0.005, 5.0, 10.0)
        assert lots == 0.0

    def test_zero_sl_distance(self):
        lots = position_size(10000, 0.005, 0, 10.0)
        assert lots == 0.0  # Invalid stop must block entry

    def test_negative_sl_distance(self):
        lots = position_size(10000, 0.005, -5.0, 10.0)
        assert lots == 0.0  # Fail closed

    def test_rounding(self):
        lots = position_size(10000, 0.005, 3.33, 10.0)
        # Should be rounded to 2 decimal places
        assert lots == round(lots, 2)

    def test_higher_risk_larger_position(self):
        lots_low = position_size(10000, 0.005, 5.0, 10.0)
        lots_high = position_size(10000, 0.01, 5.0, 10.0)
        assert lots_high > lots_low

    def test_wider_sl_smaller_position(self):
        lots_tight = position_size(10000, 0.005, 3.0, 10.0)
        lots_wide = position_size(10000, 0.005, 10.0, 10.0)
        assert lots_tight > lots_wide
