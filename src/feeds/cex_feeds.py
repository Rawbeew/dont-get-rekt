"""
CEX feeds via ccxt.
Pulls OHLCV from Binance (or configurable exchange) for major pairs.
"""
import ccxt
import pandas as pd
import numpy as np
from config import CEX_WATCHLIST, CEX_TIMEFRAME, CEX_LIMIT, CEX_EXCHANGE


def get_exchange():
    """Init ccxt exchange."""
    if CEX_EXCHANGE == "binance":
        return ccxt.binance({"enableRateLimit": True})
    elif CEX_EXCHANGE == "coinbase":
        return ccxt.coinbase({"enableRateLimit": True})
    else:
        return getattr(ccxt, CEX_EXCHANGE)({"enableRateLimit": True})


def fetch_ohlcv(symbol, timeframe=None, limit=None):
    """Fetch OHLCV for a single symbol. Returns DataFrame or None.
    Falls back to Coinbase if primary exchange fails."""
    tf = timeframe or CEX_TIMEFRAME
    lim = limit or CEX_LIMIT

    exchange_order = [CEX_EXCHANGE, "coinbase", "kraken"]
    seen = set()

    for ex_name in exchange_order:
        if ex_name in seen:
            continue
        seen.add(ex_name)
        try:
            ex = getattr(ccxt, ex_name)({"enableRateLimit": True})
            # Kraken uses /USD not /USDT for some pairs
            sym = symbol
            if ex_name == "kraken" and "/USDT" in sym:
                sym = sym.replace("/USDT", "/USD")
            ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=lim)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("datetime", inplace=True)
            return df
        except Exception as e:
            print(f"  [cex] {symbol} on {ex_name} failed: {str(e)[:80]}")
            continue
    return None


def fetch_all_cex():
    """Fetch OHLCV for all CEX watchlist symbols. Returns {symbol: DataFrame}."""
    results = {}
    for symbol in CEX_WATCHLIST:
        df = fetch_ohlcv(symbol)
        if df is not None and len(df) >= 20:
            results[symbol] = df
            print(f"  [cex] {symbol}: {len(df)} bars, last close {df['Close'].iloc[-1]:.4f}")
    return results
