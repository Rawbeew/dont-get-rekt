"""
Signal engine — VWAP, SFP, CVD, engulfing, volume spike.
Operates on OHLCV DataFrames (from CEX feeds) and DEX signal dicts.
"""
import pandas as pd
import numpy as np
from config import (
    VWAP_SD_BANDS, SFP_LOOKBACK, ENGULFING_MIN_BODY_RATIO,
    VOLUME_SPIKE_MULT, CVD_DIVERGENCE_BARS,
)
from feeds.dex_feeds import detect_volume_surge


def compute_vwap(df):
    """Add VWAP + SD bands to an OHLCV DataFrame."""
    df = df.copy()
    df["TP"] = (df["High"] + df["Low"] + df["Close"]) / 3
    grp = df.groupby(df.index.date)
    cum_vol = grp["Volume"].cumsum()
    cum_v_tp = (df["Volume"] * df["TP"]).groupby(df.index.date).cumsum()
    df["VWAP"] = np.where(cum_vol > 0, cum_v_tp / cum_vol, df["TP"])
    price_diff_sq = ((df["TP"] - df["VWAP"]) ** 2) * df["Volume"]
    cum_p_diff = price_diff_sq.groupby(df.index.date).cumsum()
    variance = np.maximum(cum_p_diff / cum_vol, 0)
    df["SD"] = np.where(cum_vol > 0, np.sqrt(variance), 0)
    df["Upper_Band"] = df["VWAP"] + VWAP_SD_BANDS * df["SD"]
    df["Lower_Band"] = df["VWAP"] - VWAP_SD_BANDS * df["SD"]
    return df


def detect_sfp(df, lookback=None):
    """Detect Swing Failure Pattern on the most recent closed bar.
    Returns dict with bull_sfp, bear_sfp, or None.
    SFP = price sweeps prior swing high/low then closes back inside.
    """
    lb = lookback or SFP_LOOKBACK
    if len(df) < lb + 2:
        return None

    sw_h = df["High"].rolling(lb).max().shift(1)
    sw_l = df["Low"].rolling(lb).min().shift(1)

    # Most recent CLOSED bar (not the forming one)
    last = df.iloc[-2]
    prev_sw_h = sw_h.iloc[-2]
    prev_sw_l = sw_l.iloc[-2]

    bull_sfp = bool(
        (last["Low"] < prev_sw_l)  # swept below swing low
        and (last["Close"] > prev_sw_l)  # but closed back above
    )
    bear_sfp = bool(
        (last["High"] > prev_sw_h)  # swept above swing high
        and (last["Close"] < prev_sw_h)  # but closed back below
    )

    if bull_sfp or bear_sfp:
        return {
            "symbol": last.name,
            "close": float(last["Close"]),
            "bull_sfp": bull_sfp,
            "bear_sfp": bear_sfp,
            "swing_high": float(prev_sw_h),
            "swing_low": float(prev_sw_l),
            "signal": "BULL_SFP" if bull_sfp else "BEAR_SFP",
        }
    return None


def detect_engulfing(df):
    """Detect bullish or bearish engulfing on most recent closed bar."""
    if len(df) < 3:
        return None
    last = df.iloc[-2]
    prev = df.iloc[-3]

    last_body = abs(last["Close"] - last["Open"])
    last_range = last["High"] - last["Low"]
    prev_body = abs(prev["Close"] - prev["Open"])

    if last_range == 0:
        return None

    body_ratio = last_body / last_range
    if body_ratio < ENGULFING_MIN_BODY_RATIO:
        return None

    bull_engulf = (
        prev["Close"] < prev["Open"]  # prev was red
        and last["Close"] > last["Open"]  # last is green
        and last["Close"] >= prev["Open"]  # engulfs prev open
        and last["Open"] <= prev["Close"]  # engulfs prev close
    )
    bear_engulf = (
        prev["Close"] > prev["Open"]  # prev was green
        and last["Close"] < last["Open"]  # last is red
        and last["Open"] >= prev["Close"]
        and last["Close"] <= prev["Open"]
    )

    if bull_engulf or bear_engulf:
        return {
            "signal": "BULL_ENGULFING" if bull_engulf else "BEAR_ENGULFING",
            "close": float(last["Close"]),
            "body_ratio": float(body_ratio),
        }
    return None


def compute_cvd(df):
    """Compute Cumulative Volume Delta. Returns CVD series."""
    delta = np.where(df["Close"] > df["Open"], df["Volume"],
                     np.where(df["Close"] < df["Open"], -df["Volume"], 0))
    return pd.Series(delta, index=df.index).cumsum()


def detect_cvd_divergence(df, bars=None):
    """Detect CVD divergence: price making new highs but CVD falling (or inverse).
    Returns dict or None."""
    lookback = bars or CVD_DIVERGENCE_BARS
    if len(df) < lookback + 2:
        return None

    cvd = compute_cvd(df)
    recent = df.iloc[-(lookback + 2):-2]  # leave the forming bar out

    prices = recent["Close"]
    cvd_recent = cvd.iloc[-(lookback):]

    price_higher = prices.iloc[-1] > prices.iloc[0]
    cvd_lower = cvd_recent.iloc[-1] < cvd_recent.iloc[0]
    price_lower = prices.iloc[-1] < prices.iloc[0]
    cvd_higher = cvd_recent.iloc[-1] > cvd_recent.iloc[0]

    # Bearish divergence: price up, CVD down
    if price_higher and cvd_lower:
        return {"signal": "BEAR_CVD_DIVERGENCE", "close": float(prices.iloc[-1])}
    # Bullish divergence: price down, CVD up
    if price_lower and cvd_higher:
        return {"signal": "BULL_CVD_DIVERGENCE", "close": float(prices.iloc[-1])}
    return None


def detect_volume_spike(df, mult=None):
    """Detect volume spike on most recent closed bar."""
    m = mult or VOLUME_SPIKE_MULT
    if len(df) < 22:
        return None

    last_vol = df["Volume"].iloc[-2]
    avg_vol = df["Volume"].iloc[-22:-2].mean()

    if avg_vol > 0 and last_vol > avg_vol * m:
        return {
            "signal": "VOLUME_SPIKE",
            "volume": float(last_vol),
            "avg_volume": float(avg_vol),
            "multiple": float(last_vol / avg_vol),
        }
    return None


def scan_cex_symbol(symbol, df):
    """Run all CEX signals on one symbol's OHLCV. Returns list of signal dicts."""
    signals = []
    df = compute_vwap(df)

    # SFP
    sfp = detect_sfp(df)
    if sfp:
        sfp["source"] = "CEX"
        sfp["symbol"] = symbol
        signals.append(sfp)

    # Engulfing
    eng = detect_engulfing(df)
    if eng:
        eng["source"] = "CEX"
        eng["symbol"] = symbol
        signals.append(eng)

    # Volume spike
    vs = detect_volume_spike(df)
    if vs:
        vs["source"] = "CEX"
        vs["symbol"] = symbol
        signals.append(vs)

    # CVD divergence
    cvd = detect_cvd_divergence(df)
    if cvd:
        cvd["source"] = "CEX"
        cvd["symbol"] = symbol
        signals.append(cvd)

    # VWAP band touch
    last = df.iloc[-2]
    if last["Close"] >= last["Upper_Band"]:
        signals.append({
            "source": "CEX",
            "symbol": symbol,
            "signal": "VWAP_UPPER_TOUCH",
            "close": float(last["Close"]),
            "upper_band": float(last["Upper_Band"]),
        })
    elif last["Close"] <= last["Lower_Band"]:
        signals.append({
            "source": "CEX",
            "symbol": symbol,
            "signal": "VWAP_LOWER_TOUCH",
            "close": float(last["Close"]),
            "lower_band": float(last["Lower_Band"]),
        })

    return signals


def scan_dex_signals(dex_signals):
    """Run DEX-specific signals on Dexscreener data. Returns list of dicts."""
    alerts = []
    for token in dex_signals:
        pair_address = token.get("pair_address", "")
        # Volume surge
        if detect_volume_surge(token):
            alerts.append({
                "source": "DEX",
                "symbol": token["symbol"],
                "chain": token["chain"],
                "pair_address": pair_address,
                "signal": "DEX_VOLUME_SURGE",
                "price_usd": token["price_usd"],
                "vol_24h": token["vol_24h"],
                "vol_6h": token["vol_6h"],
                "url": token["url"],
            })

        # Buy/sell imbalance (24h)
        buys = token.get("buys_24h", 0)
        sells = token.get("sells_24h", 0)
        total = buys + sells
        if total > 0:
            buy_ratio = buys / total
            if buy_ratio > 0.70:
                alerts.append({
                    "source": "DEX",
                    "symbol": token["symbol"],
                    "chain": token["chain"],
                    "pair_address": pair_address,
                    "signal": "DEX_BUY_PRESSURE",
                    "buy_ratio": round(buy_ratio, 2),
                    "buys_24h": buys,
                    "sells_24h": sells,
                    "price_usd": token["price_usd"],
                    "url": token["url"],
                })
            elif buy_ratio < 0.30:
                alerts.append({
                    "source": "DEX",
                    "symbol": token["symbol"],
                    "chain": token["chain"],
                    "pair_address": pair_address,
                    "signal": "DEX_SELL_PRESSURE",
                    "buy_ratio": round(buy_ratio, 2),
                    "buys_24h": buys,
                    "sells_24h": sells,
                    "price_usd": token["price_usd"],
                    "url": token["url"],
                })

        # Price change alerts
        change_1h = token.get("price_change_1h", 0)
        if change_1h > 25:
            alerts.append({
                "source": "DEX",
                "symbol": token["symbol"],
                "chain": token["chain"],
                "pair_address": pair_address,
                "signal": "DEX_PUMP_1H",
                "change_1h": change_1h,
                "price_usd": token["price_usd"],
                "url": token["url"],
            })
        elif change_1h < -25:
            alerts.append({
                "source": "DEX",
                "symbol": token["symbol"],
                "chain": token["chain"],
                "pair_address": pair_address,
                "signal": "DEX_DUMP_1H",
                "change_1h": change_1h,
                "price_usd": token["price_usd"],
                "url": token["url"],
            })

    return alerts
