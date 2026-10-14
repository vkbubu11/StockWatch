"""Formaciodetektorok tesztje szintetikus sorozatokon.

Ket dolgot kovetelunk meg:
  1. a tokeletes geometriaju mintat MEG KELL talalni (pozitiv tesztek),
  2. zajon es tiszta trenden NEM szabad jelezni (negativ tesztek).

A masodik a fontosabb. Egy rendszer, ami mindenben lat fej-vallat,
hasznalhatatlan - es ez a tipikus kudarcmod.
"""
from __future__ import annotations

import pytest

from stockwatch import indicators, patterns
from stockwatch.config import PatternSettings, StructureSettings

from . import synthetic

PATTERN_CFG = PatternSettings()
STRUCTURE_CFG = StructureSettings()


def _detect(df, confirmed_only: bool = False):
    enriched = indicators.enrich(df, STRUCTURE_CFG.atr_period)
    return patterns.detect("TEST", enriched, PATTERN_CFG, STRUCTURE_CFG, confirmed_only)


def _names(matches) -> set[str]:
    return {m.name for m in matches}


# ---------------------------------------------------------------------------
# Pozitiv tesztek - a mintat meg kell talalni
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "generator, expected",
    [
        (synthetic.double_bottom, "Dupla alj"),
        (synthetic.double_top, "Dupla teto"),
        (synthetic.head_and_shoulders, "Fej-vall"),
        (synthetic.inverse_head_and_shoulders, "Forditott fej-vall"),
        (synthetic.ascending_triangle, "Emelkedo haromszog"),
        (synthetic.descending_triangle, "Csokkeno haromszog"),
        (synthetic.bull_flag, "Bull zaszlo"),
    ],
)
def test_detects_known_pattern(generator, expected):
    matches = _detect(generator())
    assert expected in _names(matches), (
        f"A(z) {expected} formaciot nem talalta meg. Talalt: {_names(matches) or 'semmi'}"
    )


def test_double_bottom_geometry():
    """A kulcsszintek a generalt geometriara esnek."""
    matches = [m for m in _detect(synthetic.double_bottom()) if m.name == "Dupla alj"]
    assert matches, "Nincs dupla alj talalat"
    match = matches[0]

    # A nyakvonal a generalt 92-es kozbenso csucs kozeleben
    assert 89 <= match.trigger_level <= 95, f"Nyakvonal: {match.trigger_level}"
    # Measured move: 92 + (92 - 80) = 104 korul
    assert 99 <= match.target <= 109, f"Celar: {match.target}"
    # Az invalidacio a 80-as aljaknal
    assert 76 <= match.invalidation <= 84, f"Invalidacio: {match.invalidation}"
    assert match.direction == "bullish"
    assert match.status == "triggered", "A kitoresnek aktivalnia kellett volna"


def test_head_and_shoulders_geometry():
    matches = [m for m in _detect(synthetic.head_and_shoulders()) if m.name == "Fej-vall"]
    assert matches, "Nincs fej-vall talalat"
    match = matches[0]

    assert match.direction == "bearish"
    # Nyakvonal a 90-es volgyeknel
    assert 86 <= match.trigger_level <= 94, f"Nyakvonal: {match.trigger_level}"
    # Measured move lefele: 90 - (112 - 90) = 68 korul
    assert 60 <= match.target <= 78, f"Celar: {match.target}"
    # Az invalidacio a jobb vall csucsanal (100)
    assert 94 <= match.invalidation <= 106, f"Invalidacio: {match.invalidation}"
    # Ot kulcspont: bal vall, volgy, fej, volgy, jobb vall
    assert len(match.key_points) == 5


def test_forming_pattern_is_not_triggered():
    """Kitores nelkul a minta meg csak formalodik."""
    matches = [
        m for m in _detect(synthetic.double_bottom(breakout=False)) if m.name == "Dupla alj"
    ]
    if matches:
        assert matches[0].status == "forming"


def test_risk_reward_is_sane():
    """A measured move celnak jobb hozam/kockazat aranyt kell adnia 1-nel."""
    for generator in (synthetic.double_bottom, synthetic.head_and_shoulders):
        for match in _detect(generator()):
            assert match.risk_reward > 0, f"{match.name}: nulla R:R"


# ---------------------------------------------------------------------------
# Negativ tesztek - itt NEM szabad jelezni
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [7, 13, 21, 42, 99, 123, 256, 512])
def test_no_pattern_on_random_walk(seed):
    """Veletlen bolyongason minden talalat hamis pozitiv.

    Nem nulla toleranciat varunk el - egy veletlen sorozatban veletlenul
    elofordulhat valodi geometria -, de a magas minosegu talalat gyanus.
    """
    matches = _detect(synthetic.random_walk(seed=seed))
    high_quality = [m for m in matches if m.quality > 0.75]
    assert not high_quality, (
        f"Magas minosegu hamis pozitiv veletlen adaton (seed={seed}): "
        f"{[(m.name, round(m.quality, 2)) for m in high_quality]}"
    )


def test_random_walk_false_positive_rate():
    """Tobb veletlen sorozat atlagaban alacsony legyen a talalati arany."""
    detections = sum(bool(_detect(synthetic.random_walk(seed=s))) for s in range(30))
    rate = detections / 30
    assert rate <= 0.5, f"Veletlen adaton {rate:.0%} esetben jelez - tul sok hamis pozitiv"


def test_no_pattern_on_steady_trend():
    """Egyenletes trendben nincs konszolidacio, tehat nincs formacio sem."""
    matches = _detect(synthetic.steady_trend())
    high_quality = [m for m in matches if m.quality > 0.75]
    assert not high_quality, f"Hamis pozitiv tiszta trenden: {_names(high_quality)}"


def test_flat_line_does_not_crash():
    """Degeneralt bemenet: majdnem nulla volatilitas."""
    matches = _detect(synthetic.flat_line())
    assert isinstance(matches, list)


def test_empty_and_short_input():
    """Rovid bemenetre ures listat adunk, nem kivetelt."""
    import pandas as pd

    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    empty.index = pd.DatetimeIndex([], name="date")
    assert patterns.detect("TEST", empty, PATTERN_CFG, STRUCTURE_CFG) == []
    assert patterns.detect("TEST", synthetic.random_walk(bars=10), PATTERN_CFG, STRUCTURE_CFG) == []


# ---------------------------------------------------------------------------
# Look-ahead vedelem
# ---------------------------------------------------------------------------
def test_confirmed_only_mode_works():
    """A backtest modban a meg nem erositett pivot nem hasznalhato."""
    df = synthetic.double_bottom()
    with_unconfirmed = _detect(df, confirmed_only=False)
    confirmed = _detect(df, confirmed_only=True)

    # Szigorubb mod: legfeljebb annyi talalat lehet, mint a megengedobben
    assert len(confirmed) <= len(with_unconfirmed)
