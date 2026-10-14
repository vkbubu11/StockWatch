"""Indikatorok, piaci szerkezet es a support/rezisztencia motor tesztje."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import indicators, levels, structure
from stockwatch.config import LevelSettings

from . import synthetic


# ---------------------------------------------------------------------------
# Indikatorok
# ---------------------------------------------------------------------------
def test_ema_matches_manual_calculation():
    """Az EMA rekurziv definicioja: EMA_t = a*ar_t + (1-a)*EMA_(t-1)."""
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    result = indicators.ema(series, 3)

    alpha = 2 / (3 + 1)
    expected = series.iloc[0]
    for value in series.iloc[1:]:
        expected = alpha * value + (1 - alpha) * expected

    assert result.iloc[-1] == pytest.approx(expected, rel=1e-9)
    # Az elso ket ertek meg nem definialt (min_periods=3)
    assert result.iloc[:2].isna().all()


def test_rsi_extremes():
    """Csak emelkedo sorozaton az RSI 100, csak csokkenon 0 fele tart."""
    rising = pd.Series(np.arange(1, 60, dtype="float64"))
    assert indicators.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)

    falling = pd.Series(np.arange(60, 1, -1, dtype="float64"))
    assert indicators.rsi(falling, 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_stays_in_range():
    df = synthetic.random_walk(bars=300)
    values = indicators.rsi(df["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_atr_is_positive_and_scales():
    """Az ATR pozitiv, es aranyos a tenyleges napi ingadozassal."""
    df = synthetic.random_walk(bars=200)
    atr = indicators.atr(df, 14).dropna()

    assert (atr > 0).all()
    average_range = (df["high"] - df["low"]).mean()
    assert atr.mean() == pytest.approx(average_range, rel=0.5)


def test_true_range_handles_gaps():
    """Reses nyitasnal a TR az elozo zarohoz kepest szamol, nem a napi savbol."""
    df = pd.DataFrame(
        {
            "open": [100.0, 120.0],
            "high": [101.0, 121.0],
            "low": [99.0, 119.0],
            "close": [100.0, 120.0],
            "volume": [1e6, 1e6],
        }
    )
    tr = indicators.true_range(df)
    # Masodik nap: |121 - 100| = 21, nem pedig 121-119 = 2
    assert tr.iloc[1] == pytest.approx(21.0)


def test_macd_histogram_is_difference():
    df = synthetic.random_walk(bars=200)
    macd = indicators.macd(df["close"])
    computed = (macd["macd"] - macd["signal"]).dropna()
    pd.testing.assert_series_equal(
        macd["hist"].dropna(), computed, check_names=False
    )


def test_adx_range_and_trend_detection():
    """Az ADX 0-100 kozott van, es tiszta trendben magasabb, mint zajban."""
    trend = indicators.adx(indicators.enrich(synthetic.steady_trend(bars=250), 14))
    noise = indicators.adx(indicators.enrich(synthetic.random_walk(bars=250), 14))

    assert trend["adx"].dropna().between(0, 100).all()
    assert trend["adx"].tail(50).mean() > noise["adx"].tail(50).mean()


def test_enrich_produces_all_columns():
    df = indicators.enrich(synthetic.random_walk(bars=300), 14)
    expected = {
        "ema20", "ema50", "ema200", "atr", "atr_pct", "rsi", "macd", "signal",
        "hist", "adx", "plus_di", "minus_di", "bb_upper", "bb_mid", "bb_lower",
        "obv", "obv_slope", "ema50_slope", "vol_sma20", "vol_ratio",
    }
    assert expected.issubset(df.columns)
    # Az utolso soron mar minden indikatornak ki kell szamolodnia
    assert not df[list(expected)].iloc[-1].isna().any()


# ---------------------------------------------------------------------------
# Piaci szerkezet
# ---------------------------------------------------------------------------
def test_zigzag_finds_known_swings():
    """Ismert geometrian a pivotoknak a generalt fordulopontokra kell esniuk."""
    df = indicators.enrich(synthetic.double_bottom(), 14)
    pivots = structure.find_zigzag_pivots(df, 1.5, 14)

    lows = [p.price for p in pivots if not p.is_high]
    # A generalt aljak 80-nal vannak
    assert any(78 <= price <= 83 for price in lows), f"Nincs 80 koruli alj: {lows}"


def test_last_pivot_is_unconfirmed():
    """LOOK-AHEAD VEDELEM: az utolso pivot nem lehet megerositett.

    Ez a rendszer legfontosabb egyetlen invariansa. Ha elromlik, a backtest
    irrealisan jo eredmenyt ad, es a felhasznalo hamis biztonsagban erzi magat.
    """
    df = indicators.enrich(synthetic.random_walk(bars=300), 14)
    pivots = structure.find_zigzag_pivots(df, 1.5, 14)

    assert pivots, "Nem talaltunk pivotot"
    assert not pivots[-1].confirmed, "Az utolso pivot megerositettnek latszik!"
    assert all(p.confirmed for p in pivots[:-1]), "Kozbenso pivot maradt megerositetlen"


def test_confirmed_only_filter():
    df = indicators.enrich(synthetic.random_walk(bars=300), 14)
    pivots = structure.find_zigzag_pivots(df, 1.5, 14)
    confirmed = structure.confirmed_only(pivots)

    assert len(confirmed) == len(pivots) - 1
    assert all(p.confirmed for p in confirmed)


def test_alternating_produces_clean_sequence():
    df = indicators.enrich(synthetic.random_walk(bars=400), 14)
    pivots = structure.alternating(structure.find_fractal_pivots(df, 3, 3))

    for previous, current in zip(pivots, pivots[1:]):
        assert previous.kind != current.kind, "Ket azonos tipusu pivot egymas utan"


def test_zigzag_threshold_controls_sensitivity():
    """Nagyobb ATR-szorzo kevesebb, de jelentosebb pivotot ad."""
    df = indicators.enrich(synthetic.random_walk(bars=400), 14)
    sensitive = structure.find_zigzag_pivots(df, 0.8, 14)
    coarse = structure.find_zigzag_pivots(df, 3.0, 14)

    assert len(sensitive) > len(coarse)


def test_trendline_fit_on_perfect_line():
    """Tokeletesen egy egyenesre eso pontoknal a hiba nulla."""
    pivots = [
        structure.Pivot(i, pd.Timestamp("2024-01-01"), 100.0 + 2.0 * i, "H", True)
        for i in range(5)
    ]
    line = structure.fit_trendline(pivots)

    assert line.slope == pytest.approx(2.0)
    assert line.rmse == pytest.approx(0.0, abs=1e-9)
    assert line.r2 == pytest.approx(1.0)


def test_trendline_rmse_detects_flat_line_fit():
    """A vizszintes vonal helyes merese - ez a fo ok, amiert rmse kell.

    Harom szinte azonos magassagu csucsra az R^2 nullahoz tart (a szorast nem
    a meredekseg magyarazza), az rmse viszont helyesen kicsi.
    """
    pivots = [
        structure.Pivot(0, pd.Timestamp("2024-01-01"), 100.2, "H", True),
        structure.Pivot(20, pd.Timestamp("2024-02-01"), 99.8, "H", True),
        structure.Pivot(40, pd.Timestamp("2024-03-01"), 100.1, "H", True),
    ]
    line = structure.fit_trendline(pivots)

    assert line.rmse < 0.3, "Az rmse-nek kicsinek kell lennie egy lapos vonalon"
    assert line.r2 < 0.5, "Ez a teszt pont azt mutatja, hogy az R^2 itt felrevezeto"
    assert abs(line.slope_pct_per_bar) < 0.05, "A vonal gyakorlatilag vizszintes"


# ---------------------------------------------------------------------------
# Support / rezisztencia
# ---------------------------------------------------------------------------
def _triple_bounce_series() -> pd.DataFrame:
    """Az ar haromszor pattan ugyanarrol a 80-as szintrol.

    A sorozat a harmadik pattanas utan rovidesen veget er. Ez szandekos: egy
    hosszu, lassu lecsorgas a vegen tobb szaz erintest gyartana valahol
    kozepen, es az venne at a vezetest a valodi 80-as szinttol.
    """
    points = [(0, 100.0), (25, 100.0)]
    for index in range(3):
        base = 25 + index * 40
        points += [(base + 20, 80.0), (base + 40, 100.0)]
    points += [(158, 93.0)]   # semleges zaras: se a 80, se a 100 kozeleben
    return synthetic.build(points)


def test_finds_support_at_repeated_bounce_level():
    """Ahol az ar haromszor pattant, ott a LEGEROSEBB supportnak kell lennie.

    Szandekosan relativ az elvaras, nem egy abszolut pontszam-kuszob: a
    pontozas telitesi hatarai valos adathoz vannak hangolva (ahol egy szint
    10-20 erintest gyujt ossze), igy egy haromszor tesztelt szintetikus szint
    abszolut erteke alacsonyabb. Ami szamit - es amit a felhasznalo lat -, az
    a RANGSOR: ennek a szintnek kell az elso helyen allnia.
    """
    df = indicators.enrich(_triple_bounce_series(), 14)
    found = levels.find_levels(df, LevelSettings(), 14)

    assert found, "Nem talaltunk egyetlen szintet sem"

    price = float(df["close"].iloc[-1])
    atr = float(df["atr"].iloc[-1])

    # Az AKTUALIS ar koruli zonat kihagyjuk az osszehasonlitasbol. Az ott levo
    # szint mindig sok friss erintest gyujt ossze, es a frissessegi sulyozas
    # jogosan emeli ki - ez a pontozas helyes mukodese, nem hiba. A kerdes az,
    # hogy a TAVOLI szintek kozul a haromszor tesztelt 80-as nyer-e.
    distant = [
        l for l in found
        if l.kind == "support" and l.price < price - 2 * atr
    ]
    assert distant, "Nem talaltunk tavoli supportot"

    strongest = max(distant, key=lambda l: l.score)
    assert 77 <= strongest.price <= 83, (
        f"A legerosebb tavoli support {strongest.price:.1f}, de a 80-as szintnek "
        f"kellene lennie. Osszes: {[(round(l.price, 1), round(l.score, 1)) for l in distant]}"
    )
    assert strongest.touches >= 3, f"Csak {strongest.touches} erintest szamolt"
    assert "52 hetes melypont" in strongest.sources or "swing" in strongest.sources


def test_levels_classified_by_position():
    df = indicators.enrich(synthetic.random_walk(bars=400), 14)
    price = float(df["close"].iloc[-1])
    found = levels.find_levels(df, LevelSettings(), 14)

    for level in found:
        if level.kind == "support":
            assert level.price < price
        else:
            assert level.price >= price


def test_nearest_support_and_resistance():
    df = indicators.enrich(synthetic.random_walk(bars=400), 14)
    found = levels.find_levels(df, LevelSettings(), 14)
    price = float(df["close"].iloc[-1])

    support = levels.nearest_support(found)
    resistance = levels.nearest_resistance(found)

    if support:
        supports = [l.price for l in found if l.kind == "support"]
        assert support.price == max(supports), "Nem a legkozelebbi supportot adta"
        assert support.price < price
    if resistance:
        resistances = [l.price for l in found if l.kind == "resistance"]
        assert resistance.price == min(resistances)
        assert resistance.price >= price


def test_scores_discriminate():
    """A pontszamoknak szorodniuk kell - ha mind 90+, nem mernek semmit."""
    df = indicators.enrich(synthetic.random_walk(bars=500), 14)
    found = levels.find_levels(df, LevelSettings(), 14)

    if len(found) >= 5:
        scores = [l.score for l in found]
        assert max(scores) - min(scores) > 15, (
            f"A pontszamok tul osszetomorultek: {[round(s, 1) for s in scores]}"
        )


def test_volume_profile_conserves_volume():
    """A profil a teljes volument szetteriti, nem veszit es nem gyart."""
    df = synthetic.random_walk(bars=200)
    profile = levels.volume_profile(df, 50)

    assert profile["volume"].sum() == pytest.approx(df["volume"].sum(), rel=1e-6)
    assert (profile["volume"] >= 0).all()


def test_levels_on_degenerate_input():
    """Majdnem mozdulatlan aron sem szabad osszeomlani."""
    df = indicators.enrich(synthetic.flat_line(bars=200), 14)
    assert isinstance(levels.find_levels(df, LevelSettings(), 14), list)

    short = indicators.enrich(synthetic.random_walk(bars=15), 14)
    assert levels.find_levels(short, LevelSettings(), 14) == []
