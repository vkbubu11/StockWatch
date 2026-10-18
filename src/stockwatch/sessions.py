"""Kereskedesi napok: melyik napi gyertya LEZART.

A yfinance nyitvatartas alatt a mai, meg alakulo gyertyat is visszaadja (reszleges
ar, toredek volumen). Ha egy vegleges dontes (belepesi jelzes) ezzel szamol, a
jelzes napkozben valtozik: egy tegnapi "megerositett kitores" eltunhet, a
volumen 0.0x-nek latszik. Ezert a vegleges elemzes csak lezart gyertyat hasznal.
"""
from __future__ import annotations

import pandas as pd

MARKET_TZ = "America/New_York"
CLOSE_GRACE = (16, 5)       # 16:05 NY utan a mai gyertya lezartnak szamit


def now_ny() -> pd.Timestamp:
    return pd.Timestamp.now(tz=MARKET_TZ)


def session_closed(day: pd.Timestamp, now: pd.Timestamp | None = None) -> bool:
    """Lezart-e mar az adott nap gyertyaja (NY ido szerint)."""
    now = now or now_ny()
    today = now.tz_localize(None).normalize() if now.tzinfo else now.normalize()
    day = pd.Timestamp(day).normalize()
    if day < today:
        return True
    if day > today:
        return False
    return (now.hour, now.minute) >= CLOSE_GRACE


def completed_bars(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """A gyertyasor a meg alakulo mai gyertya nelkul."""
    if df is None or df.empty:
        return df
    if session_closed(df.index[-1], now):
        return df
    return df.iloc[:-1]


def trading_day(day: pd.Timestamp) -> bool | None:
    """Kereskedesi nap volt-e (unnepnap!). None, ha a naptar nem elerheto."""
    if day.weekday() >= 5:
        return False
    try:
        import pandas_market_calendars as mcal

        return not mcal.get_calendar("NASDAQ").schedule(start_date=day.date(), end_date=day.date()).empty
    except Exception:
        return None
