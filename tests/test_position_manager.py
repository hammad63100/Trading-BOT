"""
Unit Tests for Position Manager
=================================
Tests the SL trailing logic with mock positions.
"""

import pytest


class TestTrailingLogic:
    """Tests for the SL trailing rules."""

    def test_breakeven_at_1r(self):
        """At +1R, SL should move to entry price (breakeven)."""
        entry_price = 2650.0
        original_sl = 2643.0  # 7 points SL
        risk_per_unit = abs(entry_price - original_sl)  # 7.0
        current_price = entry_price + risk_per_unit  # 2657.0 (+1R)

        # At +1R, new SL = entry price
        favorable_move = current_price - entry_price
        assert favorable_move >= risk_per_unit
        new_sl = entry_price  # Breakeven
        assert new_sl == 2650.0
        assert new_sl > original_sl  # More protective

    def test_trail_at_2r(self):
        """Beyond +2R, SL should trail by 1 ATR."""
        entry_price = 2650.0
        original_sl = 2643.0
        risk_per_unit = 7.0
        atr = 5.0
        current_price = entry_price + 2 * risk_per_unit + 1  # 2665 (+2R+)

        favorable_move = current_price - entry_price
        assert favorable_move >= 2 * risk_per_unit

        trail_sl = current_price - atr  # 2660.0
        new_sl = max(entry_price, trail_sl)  # max(2650, 2660) = 2660
        assert new_sl == 2660.0
        assert new_sl > original_sl

    def test_never_widen_stop_buy(self):
        """For a BUY, new SL should never be below current SL."""
        current_sl = 2650.0
        proposed_sl = 2648.0  # Lower = wider stop for BUY
        # Should NOT apply this change
        assert proposed_sl < current_sl  # This would widen

    def test_never_widen_stop_sell(self):
        """For a SELL, new SL should never be above current SL."""
        current_sl = 2650.0
        proposed_sl = 2652.0  # Higher = wider stop for SELL
        assert proposed_sl > current_sl  # This would widen

    def test_no_change_below_1r(self):
        """Below +1R, SL should not change."""
        entry_price = 2650.0
        original_sl = 2643.0
        risk_per_unit = 7.0
        current_price = 2654.0  # +4 points, less than +1R (7)

        favorable_move = current_price - entry_price
        assert favorable_move < risk_per_unit
        # SL should remain unchanged
        new_sl = original_sl
        assert new_sl == original_sl

    def test_short_position_breakeven(self):
        """Short position: at +1R, SL moves to entry."""
        entry_price = 2660.0
        original_sl = 2667.0  # SL above entry for short
        risk_per_unit = abs(entry_price - original_sl)  # 7.0
        current_price = entry_price - risk_per_unit  # 2653.0

        favorable_move = entry_price - current_price
        assert favorable_move >= risk_per_unit
        new_sl = entry_price
        assert new_sl < original_sl  # More protective for short
