"""Momentum breakout bot for the cbBTC 24h race (agent-fun/bars-replay/v1).

Idea, designed and tuned on this competition's dev window (1,440 one-minute
cbBTC bars, price falling ~4% from $67,216 to $64,524):

* Short-term momentum persists at the 5-minute scale on this pool: after a
  5-minute rise of 75 bps or more with the 30-minute trend also up, the next
  30 minutes averaged about +110 bps (median +168 bps), against -9 bps
  unconditional. Fixed take-profit / stop-loss exits chop these moves up
  (every fixed TP/SL variant lost money on the dev window), so positions are
  exited on trend reversal instead of a fixed target.
* Long only, one position at a time, full cash per trade. A round trip costs
  ~26 bps (4 bps fee + 2 bps slippage + price impact each way), so entries
  need a real dislocation, not noise.

Entry (flat, no pending exit, at least 61 closes of history):
  - 5-minute close return >= +75 bps AND 30-minute close return > 0.

Exit (while holding, checked on every batch close):
  - 10-minute close return <= -50 bps (the up-move has reversed), or
  - close falls 80 bps below the highest close since entry (trailing stop), or
  - position is 200 bps under its entry fill price (catastrophic stop), or
  - held for 240 bars (time stop).

All arithmetic is integer basis points, so the bot is exactly deterministic:
no clock, no environment, no randomness. Orders decided on a batch fill at
the next bar's open; max_slippage_bps 300 bounds the move from the latest
close (the largest 1-minute move on the dev window was ~80 bps).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Entry: 5-minute return >= 75 bps with the 30-minute return positive.
ENTRY_R5_BPS = 75
ENTRY_R30_BPS = 0
# Exit: 10-minute return <= -50 bps (reversal).
EXIT_R10_BPS = -50
# Exit: trailing stop, 80 bps below the highest close since entry.
TRAIL_BPS = 80
# Exit: catastrophic stop, 200 bps below the entry fill price.
STOP_BPS = 200
# Exit: time stop after 240 bars held.
TIME_STOP_BARS = 240
# Orders may fill at most 300 bps away from the latest close.
MAX_SLIP_BPS = 300
# History needed: 30 bars back for the entry filter.
HISTORY_BARS = 61

# Closes of CBBTC, oldest first, as integer prices (price_scale 5).
_closes = []
# Entry fill price and fill-bar time (ms) of the open position, or None when flat.
_entry_price = None
_entry_bar_t = None
# Highest close seen since the entry fill (for the trailing stop).
_peak_since_entry = None


def _ret_bps(now, then):
    """Return of `now` over `then` in basis points, truncated toward zero."""
    return (now - then) * 10000 // then


def _note_fills(batch):
    """Fold this batch's fills into the entry state."""
    global _entry_price, _entry_bar_t, _peak_since_entry
    for fill in batch.get("fills", []):
        if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
            continue
        if fill.get("side") == "buy":
            _entry_price = int(fill["price"])
            _entry_bar_t = int(fill["fill_bar"])
            _peak_since_entry = _entry_price
        elif fill.get("side") == "sell":
            _entry_price = None
            _entry_bar_t = None
            _peak_since_entry = None


def on_batch(state, batch):
    global _peak_since_entry
    _note_fills(batch)
    for bar in batch.get("bars", []):
        if bar.get("symbol") == SYMBOL:
            _closes.append(int(bar["c"]))
            if len(_closes) > HISTORY_BARS:
                del _closes[: len(_closes) - HISTORY_BARS]
    if state.warming_up:
        return []
    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0 and _entry_price is not None:
        close = state.close(SYMBOL)
        if close is not None:
            if close > _peak_since_entry:
                _peak_since_entry = close
            held = (state.t - _entry_bar_t) // 60000 if _entry_bar_t is not None else 0
            sell = False
            if len(_closes) >= 11 and _ret_bps(close, _closes[-11]) <= EXIT_R10_BPS:
                sell = True  # up-move reversed
            elif close * 10000 <= _peak_since_entry * (10000 - TRAIL_BPS):
                sell = True  # trailing stop
            elif close * 10000 <= _entry_price * (10000 - STOP_BPS):
                sell = True  # catastrophic stop
            elif held >= TIME_STOP_BARS:
                sell = True  # time stop
            if sell:
                return [
                    {
                        "side": "sell",
                        "symbol": SYMBOL,
                        "qty": holding.qty,
                        "max_slippage_bps": MAX_SLIP_BPS,
                    }
                ]
        return []
    # Flat: look for a momentum entry.
    if len(_closes) >= 31 and state.cash > 0:
        close = _closes[-1]
        r5 = _ret_bps(close, _closes[-6])
        r30 = _ret_bps(close, _closes[-31])
        if r5 >= ENTRY_R5_BPS and r30 > ENTRY_R30_BPS:
            return [
                {
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": state.cash,
                    "max_slippage_bps": MAX_SLIP_BPS,
                }
            ]
    return []


makefun_bars.run(on_batch)
