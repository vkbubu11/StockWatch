"""Plotly chartok: gyertyak, formaciogeometria, support/rezisztencia savok.

SZINEK ES AKADALYMENTESSEG
--------------------------
A paletta a validalt referencia-keszletbol valasztott harom szin:

    aqua   #1baf7a / #199e70   emelkedes, support
    piros  #e34948 / #e66767   eses, rezisztencia
    violet #4a3aa7 / #9085e9   formaciogeometria

A validator szerint a zold-piros par a 6-8-as CVD savba esik (deuteranopia
eseten dE 6.9), ami KIZAROLAG masodlagos kodolassal megengedett. Ket helyen
epitettuk be ezt a masodlagos kodolast - ha barmelyiket eltavolitod, a chart
szinvaksag eseten ertelmezhetetlenne valik:

  1. URES vs TOMOR gyertyatest: az emelkedo gyertya uregers, az eso tomor.
     Ez a hagyomanyos japan konvencio, es szintol fuggetlenul olvashato.
  2. SZOVEGES CIMKE minden szinten ("S 486.70" / "R 495.19"), ami egyben a
     vilagos modban jelzett kontraszt-figyelmeztetes eloirt kompenzacioja is.

Tovabbi szabalyok, amiket betartunk:
  * nincs ketto y-tengely; a volumen, RSI es MACD kulon panelen van
  * a szoveg mindig semleges tintaszinu, sosem a sorozat szine
"""
from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .forecast import Forecast
from .levels import Level
from .patterns.base import PatternMatch

log = logging.getLogger(__name__)


class Palette:
    """Vilagos es sotet valtozat; a sotet nem automatikus invertalas,
    hanem ugyanazokbol a rampakbol a sotet felulethez lepteteve."""

    def __init__(self, dark: bool = False) -> None:
        self.dark = dark
        if dark:
            self.up, self.down, self.accent = "#199e70", "#e66767", "#9085e9"
            self.surface, self.grid = "#1a1a19", "#333330"
            self.text, self.muted = "#ffffff", "#c3c2b7"
        else:
            self.up, self.down, self.accent = "#1baf7a", "#e34948", "#4a3aa7"
            self.surface, self.grid = "#fcfcfb", "#e6e5e0"
            self.text, self.muted = "#0b0b0b", "#52514e"

    @property
    def ema_colors(self) -> tuple[str, str, str]:
        """A mozgoatlagok szandekosan visszafogottak: kontextus, nem foszereplo."""
        if self.dark:
            return "#6b6a64", "#8f8e85", "#c3c2b7"
        return "#9b9a93", "#77766f", "#52514e"


def build_chart(
    forecast: Forecast,
    df: pd.DataFrame,
    bars: int = 180,
    dark: bool = False,
    show_indicators: bool = True,
) -> go.Figure:
    """Teljes elemzo chart egy papirhoz."""
    palette = Palette(dark)
    window = df.tail(bars)

    rows, heights = (4, [0.55, 0.15, 0.15, 0.15]) if show_indicators else (1, [1.0])
    figure = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=heights,
        subplot_titles=(["", "Volumen", "RSI (14)", "MACD"] if show_indicators else [""]),
    )

    _add_candles(figure, window, palette)
    _add_emas(figure, window, palette)
    _add_levels(figure, forecast.all_levels, window, palette)

    if forecast.pattern is not None:
        _add_pattern(figure, forecast.pattern, window, palette)

    if show_indicators:
        _add_volume(figure, window, palette)
        _add_rsi(figure, window, palette)
        _add_macd(figure, window, palette)

    _style(figure, forecast, palette, rows)
    return figure


# ---------------------------------------------------------------------------
# Retegek
# ---------------------------------------------------------------------------
def _add_candles(figure: go.Figure, df: pd.DataFrame, palette: Palette) -> None:
    """Gyertyak URES emelkedo / TOMOR eso testtel.

    Az uregeses a szintol fuggetlen masodlagos kodolas - lasd a modul
    fejlecet. Ne cserelj tomor kitoltesre!
    """
    figure.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="Ar",
            increasing=dict(line=dict(color=palette.up, width=1.2), fillcolor="rgba(0,0,0,0)"),
            decreasing=dict(line=dict(color=palette.down, width=1.2), fillcolor=palette.down),
            hoverlabel=dict(namelength=-1),
        ),
        row=1, col=1,
    )


def _add_emas(figure: go.Figure, df: pd.DataFrame, palette: Palette) -> None:
    colors = palette.ema_colors
    for column, label, color in zip(("ema20", "ema50", "ema200"), ("EMA20", "EMA50", "EMA200"), colors):
        if column not in df.columns or df[column].isna().all():
            continue
        figure.add_trace(
            go.Scatter(
                x=df.index, y=df[column], name=label, mode="lines",
                line=dict(color=color, width=1.2),
                hovertemplate=f"{label}: %{{y:.2f}}<extra></extra>",
            ),
            row=1, col=1,
        )


def _add_levels(figure: go.Figure, levels: list[Level], df: pd.DataFrame, palette: Palette) -> None:
    """Support/rezisztencia savok, cimkevel.

    A cimke nem dekoracio: ez a szinvaksag es a vilagos modban jelzett
    kontraszt-figyelmeztetes eloirt kompenzacioja.
    """
    if not levels:
        return

    start, end = df.index[0], df.index[-1]

    # Csak az OT legrelevansabb szint kerul fel. Nyolc szeles sav gyakorlatilag
    # beteriti a panelt, es a gyertyak - vagyis a tenyleges adat - elvesznek a
    # szines mosasban. A tobbi szint a dashboard tablazataban ott van.
    for level in levels[:5]:
        color = palette.up if level.kind == "support" else palette.down
        # Az atlatszosag a szint erossegevel aranyos, de vegig visszafogott:
        # a sav hatter, nem foszereplo
        opacity = 0.05 + 0.10 * (level.score / 100.0)

        figure.add_shape(
            type="rect", xref="x", yref="y",
            x0=start, x1=end, y0=level.zone_low, y1=level.zone_high,
            fillcolor=color, opacity=opacity, line=dict(width=0),
            layer="below", row=1, col=1,
        )
        figure.add_shape(
            type="line", xref="x", yref="y",
            x0=start, x1=end, y0=level.price, y1=level.price,
            line=dict(color=color, width=1, dash="dot"),
            layer="below", row=1, col=1,
        )

        prefix = "S" if level.kind == "support" else "R"
        flip = " ⇄" if level.flipped else ""
        figure.add_annotation(
            x=end, y=level.price, xref="x", yref="y",
            text=f"{prefix} {level.price:.2f}{flip}",
            showarrow=False, xanchor="left", xshift=6,
            font=dict(size=9, color=palette.muted),
            bgcolor=palette.surface, opacity=0.9,
            row=1, col=1,
        )


def _add_pattern(figure: go.Figure, match: PatternMatch, df: pd.DataFrame, palette: Palette) -> None:
    """A formacio geometriaja: kulcspontok, nyakvonal vagy trendvonalak."""
    visible = [(d, p) for d, p in match.key_points if d >= df.index[0]]
    if visible:
        figure.add_trace(
            go.Scatter(
                x=[d for d, _ in visible], y=[p for _, p in visible],
                mode="lines+markers", name=match.name,
                line=dict(color=palette.accent, width=2, dash="dash"),
                marker=dict(size=9, color=palette.accent, symbol="circle",
                            line=dict(width=1.5, color=palette.surface)),
                hovertemplate=f"{match.name}<br>%{{y:.2f}}<extra></extra>",
            ),
            row=1, col=1,
        )

    kind = match.extras.get("kind")
    start, end = df.index[0], df.index[-1]

    if kind in ("double", "triple") and "neckline" in match.extras:
        _horizontal(figure, start, end, match.extras["neckline"], palette.accent, "Nyakvonal")
    elif kind == "hs" and "neckline_now" in match.extras:
        _horizontal(figure, start, end, match.extras["neckline_now"], palette.accent, "Nyakvonal")
    elif kind == "triangle":
        _trendlines(figure, match, df, palette)
    elif kind == "flag":
        for key, label in (("cons_high", "Zaszlo teteje"), ("cons_low", "Zaszlo alja")):
            if key in match.extras:
                _horizontal(figure, start, end, match.extras[key], palette.accent, label)

    # Celar es stop - a ket legfontosabb szam a kepen
    _horizontal(figure, start, end, match.target, palette.up, f"Cel {match.target:.2f}", dash="dashdot")
    _horizontal(figure, start, end, match.invalidation, palette.down,
                f"Stop {match.invalidation:.2f}", dash="dashdot")


def _trendlines(figure: go.Figure, match: PatternMatch, df: pd.DataFrame, palette: Palette) -> None:
    upper, lower = match.extras.get("upper_line"), match.extras.get("lower_line")
    if not upper or not lower:
        return

    # A vonalakat a teljes sorozat indexeire illesztettuk, ezert a megjelenitett
    # ablak eltolasat vissza kell szamolni.
    offset = match.extras.get("line_start", 0)
    try:
        start_position = int(df.index.get_loc(match.start_date))
    except KeyError:
        start_position = 0

    positions = range(start_position, len(df))
    absolute = [offset + (p - start_position) for p in positions]
    dates = [df.index[p] for p in positions]

    for (slope, intercept), label in ((upper, "Felso vonal"), (lower, "Also vonal")):
        figure.add_trace(
            go.Scatter(
                x=dates, y=[slope * a + intercept for a in absolute],
                mode="lines", name=label,
                line=dict(color=palette.accent, width=1.8),
                hovertemplate=f"{label}: %{{y:.2f}}<extra></extra>",
            ),
            row=1, col=1,
        )


def _horizontal(figure, x0, x1, y, color, label, dash="dash") -> None:
    """Vizszintes vonal cimkevel.

    A cimke a BAL oldalon, hatterdobozzal ul: a jobb szelt a szint-cimkek
    foglaljak, es a ketto egymasra csuszna.
    """
    figure.add_shape(
        type="line", xref="x", yref="y", x0=x0, x1=x1, y0=y, y1=y,
        line=dict(color=color, width=1.5, dash=dash), row=1, col=1,
    )
    figure.add_annotation(
        x=x0, y=y, xref="x", yref="y", text=label, showarrow=False,
        xanchor="left", xshift=6, yshift=9,
        font=dict(size=9, color=color),
        bgcolor=palette_surface_of(figure), opacity=0.9, borderpad=2,
        row=1, col=1,
    )


def palette_surface_of(figure) -> str:
    """A cimkedoboz hattere mindig a chart felulete legyen."""
    return figure.layout.paper_bgcolor or "#fcfcfb"


def _add_volume(figure: go.Figure, df: pd.DataFrame, palette: Palette) -> None:
    colors = [
        palette.up if close >= open_ else palette.down
        for close, open_ in zip(df["close"], df["open"])
    ]
    figure.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="Volumen",
               marker=dict(color=colors, line=dict(width=0)), opacity=0.55,
               hovertemplate="Volumen: %{y:,.0f}<extra></extra>"),
        row=2, col=1,
    )
    if "vol_sma20" in df.columns:
        figure.add_trace(
            go.Scatter(x=df.index, y=df["vol_sma20"], name="Volumen atlag",
                       mode="lines", line=dict(color=palette.muted, width=1.2),
                       hovertemplate="Atlag: %{y:,.0f}<extra></extra>"),
            row=2, col=1,
        )


def _add_rsi(figure: go.Figure, df: pd.DataFrame, palette: Palette) -> None:
    if "rsi" not in df.columns:
        return
    figure.add_trace(
        go.Scatter(x=df.index, y=df["rsi"], name="RSI", mode="lines",
                   line=dict(color=palette.accent, width=1.6),
                   hovertemplate="RSI: %{y:.1f}<extra></extra>"),
        row=3, col=1,
    )
    for value, label in ((70, "tulvett"), (30, "tuladott")):
        figure.add_hline(y=value, line=dict(color=palette.muted, width=1, dash="dot"),
                         row=3, col=1, annotation_text=label,
                         annotation_font=dict(size=8, color=palette.muted))


def _add_macd(figure: go.Figure, df: pd.DataFrame, palette: Palette) -> None:
    if "macd" not in df.columns:
        return
    colors = [palette.up if v >= 0 else palette.down for v in df["hist"].fillna(0)]
    figure.add_trace(
        go.Bar(x=df.index, y=df["hist"], name="MACD hiszt.",
               marker=dict(color=colors, line=dict(width=0)), opacity=0.6,
               hovertemplate="Hiszt.: %{y:.3f}<extra></extra>"),
        row=4, col=1,
    )
    figure.add_trace(
        go.Scatter(x=df.index, y=df["macd"], name="MACD", mode="lines",
                   line=dict(color=palette.accent, width=1.5),
                   hovertemplate="MACD: %{y:.3f}<extra></extra>"),
        row=4, col=1,
    )
    figure.add_trace(
        go.Scatter(x=df.index, y=df["signal"], name="Szignal", mode="lines",
                   line=dict(color=palette.muted, width=1.2, dash="dot"),
                   hovertemplate="Szignal: %{y:.3f}<extra></extra>"),
        row=4, col=1,
    )


def _style(figure: go.Figure, forecast: Forecast, palette: Palette, rows: int) -> None:
    pattern_text = f" · {forecast.pattern.name}" if forecast.pattern else ""
    title = (
        f"{forecast.symbol} · {forecast.price:.2f}{pattern_text}"
        f"<br><span style='font-size:11px;color:{palette.muted}'>"
        f"{forecast.label} ({forecast.score:+.2f}) · talalati arany: {forecast.probability_text}"
        f"</span>"
    )

    figure.update_layout(
        # A cim felfele, a jelmagyarazat ala kerul. Korabban mindketto ugyanabban
        # a savban ult, es a ketsoros cim atlogott a legenden.
        title=dict(
            text=title, font=dict(size=16, color=palette.text),
            x=0.01, xanchor="left", y=0.975, yanchor="top",
        ),
        paper_bgcolor=palette.surface,
        plot_bgcolor=palette.surface,
        font=dict(color=palette.text, size=11),
        xaxis_rangeslider_visible=False,
        # Egyetlen, kozos hover: kereszt a datumon, minden panelen
        hovermode="x unified",
        hoverlabel=dict(bgcolor=palette.surface, font=dict(color=palette.text, size=11)),
        legend=dict(orientation="h", yanchor="bottom", y=1.005, xanchor="left", x=0,
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=55, r=95, t=115, b=40),
        height=760 if rows > 1 else 460,
        bargap=0.15,
    )

    # Visszafogott racs es tengely - a rajzolt adat a foszereplo
    figure.update_xaxes(
        showgrid=True, gridcolor=palette.grid, gridwidth=0.5,
        zeroline=False, showline=False,
        rangebreaks=[dict(bounds=["sat", "mon"])],   # hetvegi resek elrejtese
    )
    figure.update_yaxes(
        showgrid=True, gridcolor=palette.grid, gridwidth=0.5,
        zeroline=False, showline=False,
    )
    for annotation in figure.layout.annotations[:rows]:
        if annotation.text in ("Volumen", "RSI (14)", "MACD"):
            annotation.font = dict(size=10, color=palette.muted)


def to_png(figure: go.Figure, width: int = 1200, height: int = 800) -> bytes | None:
    """PNG bajtok a Telegram uzenethez - ideiglenes fajl NELKUL.

    Igy hiaba fut a monitor honapokig, nem hagy maga utan kepeket a lemezen.
    """
    try:
        return figure.to_image(format="png", width=width, height=height, scale=1)
    except Exception as exc:
        log.warning("A chart PNG-re rendereles nem sikerult (kaleido hianyzik?): %s", exc)
        return None
