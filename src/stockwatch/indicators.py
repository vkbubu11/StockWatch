"""Technikai indikatorok tiszta pandas implementacioban.

Szandekosan nincs `pandas-ta` vagy `TA-Lib` fuggoseg:
  * a pandas-ta 0.3.14b eltorik numpy>=2 alatt (`from numpy import NaN`),
    ezen a gepen pedig numpy 2.4.6 van;
  * a TA-Lib C fordítást igenyelne Windowson.

Az itt kellő nyolc indikator osszesen par sor, cserebe nulla fuggosegi
kockazat es pontosan ismert szemantika.  A simitas mindenutt Wilder-fele
(alpha = 1/n), ahol a klasszikus definicio azt hasznalja.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder-fele simitas: exponencialis atlag alpha = 1/period ertekkel."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    # Nulla atlagos veszteseg eseten az RSI definicio szerint 100.
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    return result.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())


def streak(close: pd.Series) -> pd.Series:
    """Egymast koveto emelkedo (+n) / eso (-n) zarok szama; valtozatlan zaro: 0."""
    sign = np.sign(close.diff().fillna(0.0)).to_numpy()
    out = np.zeros(len(sign))
    for i in range(1, len(sign)):
        s = sign[i]
        if s > 0:
            out[i] = out[i - 1] + 1 if out[i - 1] > 0 else 1
        elif s < 0:
            out[i] = out[i - 1] - 1 if out[i - 1] < 0 else -1
    return pd.Series(out, index=close.index)


def percent_rank(series: pd.Series, period: int) -> pd.Series:
    """A mai ertek hany szazaleka nagyobb az ELOZO `period` ertek kozul (0-100). A mai nap nem
    szamit bele a viszonyitasba, es jovobeli ertek sem - nincs elorelatas."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) > period:
        windows = np.lib.stride_tricks.sliding_window_view(values, period + 1)
        past, today = windows[:, :-1], windows[:, -1:]
        ranks = (past < today).sum(axis=1) / period * 100.0
        valid = ~np.isnan(windows).any(axis=1)
        out[period:] = np.where(valid, ranks, np.nan)
    return pd.Series(out, index=series.index)


def connors_rsi(close: pd.Series, rsi_period: int = 3, streak_period: int = 2, rank_period: int = 100) -> pd.Series:
    """ConnorsRSI(3,2,100) = (RSI(3) + az emelkedo/eso sorozat RSI(2)-je + az 1 napos hozam
    szazalekos rangja az elozo 100 napban) / 3. 0-100; alacsony = tuladott."""
    return (rsi(close, rsi_period) + rsi(streak(close), streak_period)
            + percent_rank(close.pct_change(), rank_period)) / 3.0


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "hist": macd_line - signal_line,
        }
    )


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder-fele ADX, +DI es -DI.  Trenderossegre hasznaljuk (>25 = trendelo)."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )

    atr_ = _wilder(true_range(df), period)
    safe_atr = atr_.replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / safe_atr
    minus_di = 100.0 * _wilder(minus_dm, period) / safe_atr

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return pd.DataFrame({"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di})


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    middle = sma(series, period)
    # ddof=0: a klasszikus Bollinger populacios szorast hasznal
    deviation = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std_mult * deviation
    lower = middle - std_mult * deviation
    width = (upper - lower) / middle.replace(0.0, np.nan)
    return pd.DataFrame({"bb_upper": upper, "bb_mid": middle, "bb_lower": lower, "bb_width": width})


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume: a volumen elojeles kumulalasa a zaro iranya szerint."""
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def slope_pct(series: pd.Series, period: int = 20) -> pd.Series:
    """A sorozat meredeksege szazalekban, `period` gyertyara visszatekintve.

    Trendiranyhoz hasznaljuk (pl. EMA50 emelkedik-e), ezert relativ ertek,
    hogy kulonbozo arszintu papirok osszehasonlithatok legyenek.
    """
    past = series.shift(period)
    return (series - past) / past.abs().replace(0.0, np.nan) * 100.0


def enrich(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """Minden indikator racsatolasa egy OHLCV tablara, egy hivassal.

    Az elemzo retegek ezt a bovitett tablat kapjak, igy nem szamol mindenki
    ujra ugyanazt.
    """
    out = df.copy()
    close = out["close"]

    out["ema20"] = ema(close, 20)
    out["ema50"] = ema(close, 50)
    out["ema200"] = ema(close, 200)
    out["sma20"] = sma(close, 20)
    out["atr"] = atr(out, atr_period)
    out["atr_pct"] = out["atr"] / close.replace(0.0, np.nan) * 100.0
    out["rsi"] = rsi(close, 14)
    out = out.join(macd(close))
    out = out.join(adx(out))
    out = out.join(bollinger(close))
    out["obv"] = obv(out)
    out["obv_slope"] = slope_pct(out["obv"], 20)
    out["ema50_slope"] = slope_pct(out["ema50"], 20)
    out["vol_sma20"] = sma(out["volume"], 20)
    out["vol_ratio"] = out["volume"] / out["vol_sma20"].replace(0.0, np.nan)
    return out
