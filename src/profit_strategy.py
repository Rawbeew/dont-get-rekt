"""
Profit-taking strategy for shitcoins.
Two buckets: SELL_BAG and HOLD_BAG.

Sell Bag: Sell initial investment at 2x. Captures principal + 100% profit.
Moon Bag: Hold remainder for moonshot (10x+). Never sells fully.

Entry/Exit Ledger: Every trade logged with entry price, exit prices,
  partial sells, unrealized P&L, and realized P&L.

Rules:
1. Buy 0.1 SOL → splits into 0.05 SOL value (Sell Bag) + 0.05 SOL value (Moon Bag)
2. At 2x: Sell Bag sells 50% of holdings (回收 initial investment)
3. Moon Bag holds with trailing stop:
   - Sells 25% at 5x
   - Sells 25% at 10x
   - Sells remainder at 0.5x from peak (trailing stop)
4. If price drops 50% from entry: Sell Bag sells, Moon Bag holds with smaller position

This is how real shitcoin traders compound: always get principal back,
then ride free tokens for the moonshot.
"""
import os
import time
import json
from datetime import datetime, timezone
from config import STATE_DIR

LEDGER_PATH = os.path.join(STATE_DIR, "profit_ledger.json")

# ── Position sizing constants ──────────────────────────────────────
SELL_BAG_PCT = 0.50      # Sell 50% of tokens at 2x (recovers principal)
MOON_BAG_PCT = 0.50      # Hold 50% for moonshot

# Sell schedule for Moon Bag
MOON_BAG_SCHEDULE = [
    (5.0, 0.25),   # Sell 25% of moon bag at 5x entry
    (10.0, 0.25),  # Sell 25% of moon bag at 10x entry
    (25.0, 0.50),  # Sell remaining 50% of moon bag at 25x (or trailing stop)
]

# Trailing stop for moon bag (sell 10% of remaining at each 15% drop from peak)
TRAILING_STOP_PCT = 0.15   # 15% drop from peak triggers sell
TRAILING_SELL_PCT = 0.10   # Sell 10% of remaining per trigger

# Sell Bag: hard sell at 2x (recovers 100% of initial SOL)
SELL_BAG_TRIGGER = 2.0
SELL_BAG_AMOUNT = 0.50     # Sell 50% of sell bag

# Moon Bag: trailing stop after 5x
MOON_BAG_TRAIL_TRIGGER = 5.0


class ProfitLedger:
    """Tracks all trades with entry, exit, partial sells, P&L."""
    
    def __init__(self):
        self.trades = self._load()
    
    def _load(self):
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH) as f:
                return json.load(f)
        return []
    
    def save(self):
        with open(LEDGER_PATH, "w") as f:
            json.dump(self.trades, f, indent=2)
    
    def add_trade(self, trade):
        """Add a new trade to the ledger."""
        self.trades.append(trade)
        self.save()
    
    def record_sell(self, trade_idx, amount_sold, sell_price, bucket="sell_bag"):
        """Record a partial or full sell."""
        if trade_idx >= len(self.trades):
            return
        
        trade = self.trades[trade_idx]
        
        if "sells" not in trade:
            trade["sells"] = []
        
        sell_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bucket": bucket,
            "amount_sold": amount_sold,
            "sell_price": sell_price,
            "sol_received": amount_sold * sell_price,
        }
        
        if bucket == "sell_bag":
            sell_record["shares"] = amount_sold * 0.50  # sell bag is 50% of position
            sell_record["moon_shares"] = amount_sold * 0.50  # moon bag is 50%
        else:
            sell_record["shares"] = amount_sold  # moon bag is separate
        
        trade["sells"].append(sell_record)
        
        # Update remaining
        if "remaining" not in trade:
            trade["remaining"] = {"sell_bag": 1.0, "moon_bag": 1.0}
        
        if bucket == "sell_bag":
            trade["remaining"]["sell_bag"] *= (1 - amount_sold)
        else:
            trade["remaining"]["moon_bag"] *= (1 - amount_sold)
        
        self.save()
        return sell_record
    
    def get_trade(self, idx):
        if idx < len(self.trades):
            return self.trades[idx]
        return None
    
    def get_unrealized_pnl(self, current_prices):
        """Calculate unrealized P&L for open positions."""
        unrealized = []
        for i, trade in enumerate(self.trades):
            if trade.get("closed"):
                continue
            mint = trade.get("mint", "")
            entry_price = trade.get("entry_price_sol", 0)
            if not entry_price:
                continue
            
            current = current_prices.get(mint, 0)
            if current == 0:
                continue
            
            # Calculate remaining shares
            remaining_sells = trade.get("remaining", {}).get("sell_bag", 1.0)
            remaining_moons = trade.get("remaining", {}).get("moon_bag", 1.0)
            
            # Sell bag value
            sell_value = remaining_sells * entry_price * 2  # 2x entry
            moon_value = remaining_moons * entry_price * (current / entry_price)
            
            pnl_pct = ((current / entry_price) - 1) * 100
            unrealized.append({
                "idx": i,
                "symbol": trade.get("symbol", "?"),
                "mint": mint,
                "entry": entry_price,
                "current": current,
                "pnl_pct": round(pnl_pct, 1),
                "sell_bag_remaining": remaining_sells,
                "moon_bag_remaining": remaining_moons,
                "status": trade.get("status", "open"),
            })
        
        return unrealized
    
    def get_realized_pnl(self):
        """Calculate realized P&L from completed sells."""
        total_realized = 0
        for trade in self.trades:
            for sell in trade.get("sells", []):
                total_realized += sell.get("sol_received", 0)
        return total_realized
    
    def export_summary(self):
        """Export full ledger summary for Telegram."""
        lines = ["*Profit Ledger*\n"]
        
        total_realized = self.get_realized_pnl()
        lines.append(f"*Realized P&L:* ${total_realized:.2f} SOL received from sells")
        lines.append(f"*Total trades:* {len(self.trades)}")
        lines.append("")
        
        # Open positions
        open_positions = [t for t in self.trades if not t.get("closed")]
        if open_positions:
            lines.append(f"*Open positions ({len(open_positions)}):*\n")
            for trade in open_positions:
                lines.append(f"  `{trade.get('symbol', '?')}` ({trade.get('mint', '')[:15]}...)\n")
                lines.append(f"  Entry: {trade.get('entry_sol', 0):.3f} SOL\n")
                lines.append(f"  Sells: {len(trade.get('sells', []))}\n")
                lines.append(f"  Status: {trade.get('status', 'open')}\n")
        else:
            lines.append("*No open positions*")
        
        lines.append("")
        lines.append("*Recent sells:*\n")
        # Last 5 sells
        recent_sells = []
        for trade in reversed(self.trades[-10:]):
            for sell in trade.get("sells", []):
                recent_sells.append((sell, trade.get("symbol", "?")))
        
        for sell, sym in recent_sells[-5:]:
            bucket = sell.get("bucket", "?")
            price = sell.get("sol_received", 0)
            lines.append(f"  {sym}: {price:.3f} SOL ({bucket})")
        
        return "\n".join(lines)


def calculate_sell_schedule(entry_price, current_price, initial_sol=0.1):
    """Calculate what to sell at current price based on profit strategy.
    
    Returns dict with sell recommendations per bucket.
    """
    multiplier = current_price / entry_price if entry_price > 0 else 0
    
    result = {
        "entry_price": entry_price,
        "current_price": current_price,
        "multiplier": round(multiplier, 2),
        "recommendations": [],
    }
    
    # Sell Bag logic
    if multiplier >= SELL_BAG_TRIGGER:
        # Sell 50% of sell bag at 2x — recovers principal
        sol_back = initial_sol * SELL_BAG_PCT
        result["recommendations"].append({
            "bucket": "sell_bag",
            "action": "SELL",
            "reason": f"2x reached — recover {sol_back:.3f} SOL principal",
            "percent_of_position": SELL_BAG_PCT,
            "sol_to_receive": sol_back,
        })
    
    # Moon Bag schedule
    for target_mult, sell_pct in MOON_BAG_SCHEDULE:
        if multiplier >= target_mult:
            sol_to_receive = initial_sol * MOON_BAG_PCT * sell_pct
            result["recommendations"].append({
                "bucket": "moon_bag",
                "action": "SELL",
                "reason": f"{target_mult}x reached — take {sell_pct*100:.0f}% of moon bag",
                "percent_of_moon_bag": sell_pct,
                "sol_to_receive": sol_to_receive,
            })
    
    return result


def format_sell_schedule(trade_idx, ledger):
    """Format sell schedule for a trade by index."""
    trade = ledger.trades[trade_idx] if 0 <= trade_idx < len(ledger.trades) else None
    if not trade or trade.get("closed"):
        return f"No open position at index {trade_idx}"
    
    entry_price = trade.get("entry_price_sol", 0)
    if not entry_price:
        return f"No entry price recorded for {trade.get('symbol', '?')}"
    
    # Recalculate schedule from trade data
    from trading import check_token_price
    current_price = check_token_price(trade.get("mint", ""))
    if not current_price:
        return f"Could not get price for {trade.get('symbol', '?')}"
    
    return format_sell_schedule_for_trade(trade, entry_price, current_price)


def format_sell_schedule_for_trade(trade, entry_price, current_price):
    """Format sell schedule for a trade dict."""
    multiplier = current_price / entry_price if entry_price > 0 else 0
    
    lines = [f"*🎯 Exit Strategy — {multiplier:.1f}x multiplier*"]
    lines.append(f"Entry: {entry_price:.6f} SOL → Current: {current_price:.6f} SOL")
    
    # Sell bag analysis
    if multiplier >= 2.0:
        entry_sol = trade.get("entry_sol", 0)
        lines.append(f"  💰 SELL BAG: 2x reached — sell 50% to recover {entry_sol:.3f} SOL principal")
    else:
        lines.append(f"  💰 SELL BAG: needs {2.0/multiplier:.1f}x more to recover principal")
    
    # Moon bag analysis
    if multiplier >= 5.0:
        lines.append(f"  🌙 MOON BAG: 5x reached — sell 25% of remaining")
    elif multiplier >= 2.0:
        lines.append(f"  🌙 MOON BAG: {5.0/multiplier:.1f}x more to 5x trigger")
    
    if multiplier >= 10.0:
        lines.append(f"  🌙 MOON BAG: 10x reached — sell 25% more")
    elif multiplier >= 5.0:
        lines.append(f"  🌙 MOON BAG: {10.0/multiplier:.1f}x more to 10x trigger")
    
    if multiplier >= 1.5:
        lines.append(f"  📊 Trailing stop at {multiplier * 0.85:.1f}x (15% drop from peak)")
    
    lines.append("")
    
    # Remaining position
    remaining = trade.get("remaining", {"sell_bag": 1.0, "moon_bag": 1.0})
    lines.append(f"Remaining: sell_bag={remaining.get('sell_bag', 1.0):.0%} moon_bag={remaining.get('moon_bag', 1.0):.0%}")
    sells = trade.get("sells", [])
    if sells:
        lines.append(f"Sells done: {len(sells)}")
    
    return "\n".join(lines)


def record_entry_to_ledger(trade_data, ledger):
    """Record a new trade entry in the ledger."""
    trade = {
        "symbol": trade_data.get("symbol", "unknown"),
        "mint": trade_data.get("mint", ""),
        "chain": trade_data.get("chain", "solana"),
        "entry_sol": trade_data.get("entry_sol", 0),
        "entry_price_sol": trade_data.get("entry_price_sol", 0),
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "entry_signal": trade_data.get("signal", ""),
        "sells": [],
        "remaining": {"sell_bag": 1.0, "moon_bag": 1.0},
        "status": "open",
        "closed": False,
    }
    ledger.add_trade(trade)
    return len(ledger.trades) - 1  # trade index


if __name__ == "__main__":
    # Demo
    ledger = ProfitLedger()
    
    # Simulate a trade
    trade_data = {
        "symbol": "TEST",
        "mint": "test123",
        "entry_sol": 0.1,
        "entry_price_sol": 0.001,
        "signal": "BUY_PRESSURE_EARLY",
    }
    idx = record_entry_to_ledger(trade_data, ledger)
    
    # Simulate some sells
    ledger.record_sell(idx, 0.5, 0.002, "sell_bag")  # 2x sell
    ledger.record_sell(idx, 0.25, 0.005, "moon_bag")  # 5x sell
    
    print(ledger.export_summary())
