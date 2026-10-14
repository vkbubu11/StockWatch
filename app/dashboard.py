"""StockWatch dashboard (Streamlit).

    streamlit run app/dashboard.py

Negy fül:
  Attekintes   - mind a 10 papir egy tablazatban
  Reszletek    - chart a formaciogeometriaval es a szintekkel
  Jelzesek     - a signals.jsonl elozmenyei
  Backtest     - a kalibracios tabla, talalati aranyokkal
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stockwatch import charting, feed, housekeeping, indicators  # noqa: E402
from stockwatch.config import load_settings, load_watchlist, save_watchlist  # noqa: E402
from stockwatch.forecast import Calibration, analyse  # noqa: E402

st.set_page_config(page_title="StockWatch", page_icon="📈", layout="wide")

DIRECTION_ICON = {"bullish": "▲", "bearish": "▼", "neutral": "●"}


@st.cache_data(ttl=600, show_spinner=False)
def load_data(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Cache-elt betoltes: egy ujratoltes ne inditson 10 halozati kerest."""
    settings = load_settings()
    raw = feed.load_or_update(list(symbols), settings)
    return {s: indicators.enrich(df, settings.structure.atr_period) for s, df in raw.items()}


def analyse_all(data: dict[str, pd.DataFrame]):
    settings = load_settings()
    calibration = Calibration.load(settings.calibration_path)
    return {s: analyse(s, df, settings, calibration) for s, df in data.items()}


# ---------------------------------------------------------------------------
# Oldalsav
# ---------------------------------------------------------------------------
def sidebar() -> tuple[list[str], bool, int]:
    st.sidebar.title("📈 StockWatch")
    st.sidebar.caption("NASDAQ chart formacio figyelo")

    watchlist = load_watchlist().symbols
    text = st.sidebar.text_area(
        "Figyelt papirok (soronkent egy)",
        value="\n".join(watchlist),
        height=210,
    )
    entered = [s.strip().upper() for s in text.splitlines() if s.strip()]

    if entered != watchlist and st.sidebar.button("Watchlist mentese", width="stretch"):
        save_watchlist(entered)
        st.cache_data.clear()
        st.sidebar.success("Mentve.")
        st.rerun()

    st.sidebar.divider()
    dark = st.sidebar.toggle("Sotet chart", value=False)
    bars = st.sidebar.slider("Megjelenitett gyertyak", 60, 500, 180, step=20)

    if st.sidebar.button("🔄 Frissites most", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.divider()
    settings = load_settings()
    used = housekeeping.total_usage_mb(settings)
    limit = settings.storage.max_data_mb
    st.sidebar.caption("Lemezhasznalat")
    st.sidebar.progress(min(used / limit, 1.0), text=f"{used:.1f} / {limit:.0f} MB")
    if used > limit:
        st.sidebar.warning("Tullepted a kuszobot - futtasd a housekeeping parancsot.")

    st.sidebar.divider()
    st.sidebar.caption(
        "⚠️ Ez az eszkoz technikai elemzest vegez, **nem befektetesi tanacs**. "
        "A chart formaciok nem garantalnak semmit."
    )
    return entered, dark, bars


# ---------------------------------------------------------------------------
# Fülek
# ---------------------------------------------------------------------------
def tab_overview(forecasts: dict) -> None:
    st.subheader("Attekintes")

    rows = []
    for symbol, forecast in forecasts.items():
        pattern = forecast.pattern
        rows.append(
            {
                "Ticker": symbol,
                "Ar": round(forecast.price, 2),
                "Jelzes": f"{DIRECTION_ICON[forecast.direction]} {forecast.label}",
                "Pontszam": round(forecast.score, 2),
                "Formacio": pattern.name if pattern else "-",
                "Allapot": pattern.status if pattern else "-",
                "Minoseg": round(pattern.quality, 2) if pattern else None,
                "Valoszinuseg": forecast.probability_text,
                "R:R": round(pattern.risk_reward, 2) if pattern else None,
                "Support": round(forecast.support.price, 2) if forecast.support else None,
                "Sup. tav%": round(forecast.support.distance_pct, 1) if forecast.support else None,
                "Rezisztencia": round(forecast.resistance.price, 2) if forecast.resistance else None,
                "Rez. tav%": round(forecast.resistance.distance_pct, 1) if forecast.resistance else None,
            }
        )

    frame = pd.DataFrame(rows).sort_values("Pontszam", key=abs, ascending=False)
    st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        column_config={
            "Pontszam": st.column_config.NumberColumn(
                "Pontszam", help="-1 (eros csokkeno) .. +1 (eros emelkedo)", format="%+.2f"
            ),
            "Minoseg": st.column_config.ProgressColumn(
                "Minoseg", min_value=0.0, max_value=1.0, format="%.2f"
            ),
        },
    )

    active = [f for f in forecasts.values() if f.pattern]
    triggered = [f for f in active if f.pattern.status == "triggered"]
    columns = st.columns(4)
    columns[0].metric("Figyelt papir", len(forecasts))
    columns[1].metric("Aktiv formacio", len(active))
    columns[2].metric("Kitort", len(triggered))
    columns[3].metric(
        "Bullish / Bearish",
        f"{sum(f.direction == 'bullish' for f in forecasts.values())} / "
        f"{sum(f.direction == 'bearish' for f in forecasts.values())}",
    )

    warnings = [(s, w) for s, f in forecasts.items() for w in f.warnings]
    if warnings:
        with st.expander(f"Figyelmeztetesek ({len(warnings)})"):
            for symbol, warning in warnings:
                st.write(f"**{symbol}**: {warning}")


def tab_detail(forecasts: dict, data: dict, dark: bool, bars: int) -> None:
    symbol = st.selectbox("Papir", list(forecasts.keys()))
    if not symbol:
        return

    forecast, df = forecasts[symbol], data[symbol]

    columns = st.columns(5)
    columns[0].metric("Ar", f"{forecast.price:.2f}")
    columns[1].metric("Jelzes", forecast.label, delta=f"{forecast.score:+.2f}")
    columns[2].metric(
        "Support",
        f"{forecast.support.price:.2f}" if forecast.support else "-",
        delta=f"{forecast.support.distance_pct:+.1f}%" if forecast.support else None,
    )
    columns[3].metric(
        "Rezisztencia",
        f"{forecast.resistance.price:.2f}" if forecast.resistance else "nincs",
        delta=f"{forecast.resistance.distance_pct:+.1f}%" if forecast.resistance else None,
    )
    columns[4].metric("Talalati arany", forecast.probability_text)

    st.plotly_chart(
        charting.build_chart(forecast, df, bars=bars, dark=dark),
        width="stretch",
        config={"displayModeBar": True, "scrollZoom": True},
    )

    left, right = st.columns(2)

    with left:
        st.markdown("#### Miert ez a jelzes?")
        st.caption("A kompozit pontszam komponensei es a sulyuk.")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Komponens": c.name,
                        "Pontszam": round(c.score, 2),
                        "Suly": f"{c.weight:.0%}",
                        "Hozzajarulas": round(c.contribution, 3),
                        "Indoklas": c.reason,
                    }
                    for c in forecast.components
                ]
            ),
            width="stretch", hide_index=True,
        )

        if forecast.pattern:
            match = forecast.pattern
            st.markdown("#### Formacio")
            st.write(
                f"**{match.name}** ({match.direction}) - {match.status}, "
                f"minoseg {match.quality:.0%}"
            )
            st.write(match.notes)
            st.write(
                f"Trigger **{match.trigger_level:.2f}** · "
                f"Cel **{match.target:.2f}** ({match.target_pct:+.1f}%) · "
                f"Stop **{match.invalidation:.2f}** · R:R **{match.risk_reward:.2f}**"
            )
            if forecast.low_quality:
                st.warning("Gyenge hozam/kockazat arany.")

    with right:
        st.markdown("#### Support / rezisztencia szintek")
        st.caption(
            "A **pont** a szint strukturalis erosseget meri, a **relevancia** "
            "ezt sulyozza az aktualis artol valo tavolsaggal."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Ar": round(level.price, 2),
                        "Tipus": "Support" if level.kind == "support" else "Rezisztencia",
                        "Pont": round(level.score, 1),
                        "Relev.": round(level.relevance, 1),
                        "Erintes": level.touches,
                        "Tav%": round(level.distance_pct, 1),
                        "Flip": "⇄" if level.flipped else "",
                        "Forrasok": ", ".join(level.sources[:3]),
                    }
                    for level in forecast.all_levels
                ]
            ),
            width="stretch", hide_index=True,
        )


def tab_signals() -> None:
    st.subheader("Jelzes-elozmenyek")
    settings = load_settings()
    records = housekeeping.read_jsonl(settings.signals_path, limit=300)

    if not records:
        st.info(
            "Meg nincs jelzes. Inditsd el a figyelot: `python cli.py monitor` "
            "vagy futtass egy ciklust: `python cli.py monitor --once`"
        )
        return

    frame = pd.DataFrame(
        [
            {
                "Idopont": r.get("timestamp"),
                "Esemeny": r.get("event"),
                "Ticker": r.get("symbol"),
                "Formacio": (r.get("pattern") or {}).get("name", "-"),
                "Irany": r.get("direction"),
                "Ar": r.get("price"),
                "Cel": r.get("target"),
                "Stop": r.get("invalidation"),
                "R:R": r.get("risk_reward"),
                "Jelzes": r.get("label"),
            }
            for r in records
        ]
    )
    st.dataframe(frame, width="stretch", hide_index=True)
    st.caption(f"{len(records)} jelzes (a fajl meretalapon rotal, a regebbiek archivumba kerulnek).")


def tab_backtest() -> None:
    st.subheader("Backtest kalibracio")
    settings = load_settings()
    calibration = Calibration.load(settings.calibration_path)

    if not calibration.data:
        st.info(
            "Meg nem futott backtest. Futtasd: `python cli.py backtest --symbols all`\n\n"
            "Addig a rendszer nem jelenit meg valoszinuseget - inkabb bevallja, "
            "hogy nem tudja, mint hogy kitalaljon egy szamot."
        )
        return

    st.caption(
        f"Generalva: {calibration.data.get('generated_at', '?')} · "
        f"{calibration.data.get('total_trades', 0)} triggerelt formacio"
    )

    for warning in calibration.data.get("warnings", []):
        st.error(warning)

    rows = [
        {
            "Formacio": entry["name"],
            "Irany": entry["direction"],
            "Minta (n)": entry["samples"],
            "Talalati arany": entry["hit_rate"],
            "Timeout": entry["timeout_rate"],
            "Varhato ertek (R)": entry["expectancy_r"],
            "Medián nap": entry["median_bars"],
        }
        for entry in calibration.data.get("patterns", {}).values()
    ]
    frame = pd.DataFrame(rows).sort_values("Minta (n)", ascending=False)

    st.dataframe(
        frame,
        width="stretch", hide_index=True,
        column_config={
            "Talalati arany": st.column_config.ProgressColumn(
                "Talalati arany", min_value=0.0, max_value=1.0, format="%.0f%%"
            ),
            "Timeout": st.column_config.NumberColumn("Timeout", format="%.0f%%"),
        },
    )

    minimum = settings.forecast.min_samples_for_probability
    st.caption(
        f"Csak a legalabb **{minimum}** mintaval rendelkezo formaciokhoz jelenitunk meg "
        "valoszinuseget. A klasszikus chart formaciok valos talalati aranya jellemzoen "
        "**40-65%** kozott van; a 85% feletti ertek nem siker, hanem look-ahead szivargas "
        "gyanuja - a backtest ezt kulon jelzi."
    )

    if "walk_forward" in calibration.data:
        st.markdown("#### Walk-forward stabilitas")
        st.caption("A regebbi es az ujabb idoszak talalati aranya. Nagy elteres = a szamok nem altalanosithatok.")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Formacio": k, "Regebbi fel": v["train_hit_rate"],
                     "Ujabb fel": v["test_hit_rate"], "Elteres": v["drift"],
                     "Stabil": "igen" if v["stable"] else "NEM"}
                    for k, v in calibration.data["walk_forward"].items()
                    if isinstance(v, dict)
                ]
            ),
            width="stretch", hide_index=True,
        )


# ---------------------------------------------------------------------------
def main() -> None:
    symbols, dark, bars = sidebar()
    if not symbols:
        st.warning("Adj meg legalabb egy tickert az oldalsavban.")
        return

    with st.spinner("Adatok betoltese es elemzes..."):
        data = load_data(tuple(symbols))
        if not data:
            st.error("Nem sikerult adatot betolteni. Ellenorizd az internetkapcsolatot.")
            return
        forecasts = analyse_all(data)

    overview, detail, signals, backtest_tab = st.tabs(
        ["Attekintes", "Reszletek", "Jelzesek", "Backtest"]
    )
    with overview:
        tab_overview(forecasts)
    with detail:
        tab_detail(forecasts, data, dark, bars)
    with signals:
        tab_signals()
    with backtest_tab:
        tab_backtest()


if __name__ == "__main__":
    main()
