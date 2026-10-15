#!/usr/bin/env python
"""StockWatch parancssori felulet.

    python cli.py fetch [--symbols AAPL,MSFT] [--full]
    python cli.py scan [--symbols ...] [--plot]
    python cli.py entries [--symbols ...] [--all] [--details]   swing long belepesi pontok
    python cli.py entry-backtest [--universe] [--csv out.csv]
    python cli.py positions                      a jelzett trade-ek kovetese
    python cli.py holdings [add|close|set-stop|remove]  sajat poziciok es kiertekelesuk
    python cli.py broker [status|sync]           Alpaca paper szamla
    python cli.py ledger [--decision alerted]    jelzesnaplo (minden belepesi dontes)
    python cli.py trace <jel-id>                 egy jelzes eletutja
    python cli.py revalidate                     elozo esti jelzesek ujraellenorzese (nyitas elott)
    python cli.py replay <jel-id>                naplozott dontes visszajatszasa
    python cli.py index-scan [--nasdaq100] [--sp500] [--alerts]   indexek atnezese
    python cli.py web [--port 8765]              helyi webes felulet a bongeszoben
    python cli.py fetch-universe                 a bovitett kutatasi minta letoltese
    python cli.py entry-research [--universe]    belepesi szabalyvaltozatok osszehasonlitasa
    python cli.py levels --symbol AAPL
    python cli.py backtest [--symbols all] [--walk-forward] [--purge-cache]
    python cli.py backtest-symbol SPY --start 2000-01-01 --strategy deep [--set stop_atr=4]
    python cli.py monitor [--once] [--dry-run]
    python cli.py data-usage
    python cli.py test-alert
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from stockwatch import (  # noqa: E402
    backtest, entry, feed, housekeeping, indicators, levels, market, monitor, store,
)
from stockwatch.alerts import Alert, Dispatcher, format_body, format_title  # noqa: E402
from stockwatch.config import ensure_dirs, load_settings, load_watchlist  # noqa: E402
from stockwatch.forecast import Calibration, analyse  # noqa: E402

DISCLAIMER = (
    "\nFIGYELEM: Ez az eszkoz technikai elemzest vegez, NEM befektetesi tanacsot ad.\n"
    "A chart formaciok nem garantalnak semmit; a tozsden veszithetsz penzt.\n"
)


def _symbols(args) -> list[str]:
    if getattr(args, "symbols", None):
        if args.symbols.lower() == "all":
            return load_watchlist().symbols
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    return load_watchlist().symbols


# ---------------------------------------------------------------------------
def cmd_fetch(args) -> int:
    settings = load_settings()
    symbols = _symbols(args)
    data = feed.update(symbols, settings, force_full=args.full)

    if settings.entry.enabled and settings.entry.regime_filter:
        bench = feed.update([settings.entry.benchmark], settings, force_full=args.full, universe=True)
        frame = bench.get(settings.entry.benchmark.upper())
        state = f"{len(frame)} gyertya" if frame is not None and not frame.empty else "NEM SIKERULT"
        print(f"Benchmark ({settings.entry.benchmark}, piaci rezsimhez): {state}")

    print(f"\n{'ticker':<8} {'gyertya':>8} {'elso':>12} {'utolso':>12}")
    print("-" * 44)
    for symbol in symbols:
        df = data.get(symbol)
        if df is None or df.empty:
            print(f"{symbol:<8} {'nincs adat':>8}")
            continue
        print(f"{symbol:<8} {len(df):>8} {str(df.index.min().date()):>12} {str(df.index.max().date()):>12}")
    return 0


def cmd_levels(args) -> int:
    settings = load_settings()
    df = store.load(args.symbol, settings)
    if df.empty:
        print(f"Nincs cache-elt adat: {args.symbol}. Futtasd elobb: python cli.py fetch")
        return 1

    enriched = indicators.enrich(df, settings.structure.atr_period)
    price = float(enriched["close"].iloc[-1])
    found = levels.find_levels(enriched, settings.levels, settings.structure.atr_period)

    print(f"\n{args.symbol} - aktualis ar: {price:.2f}\n")
    print(f"{'ar':>10} {'tipus':<13} {'pont':>6} {'relev':>6} {'erint':>6} {'tav%':>8}  {'flip':<5} forrasok")
    print("-" * 92)
    for level in found:
        print(
            f"{level.price:10.2f} {level.kind:<13} {level.score:6.1f} {level.relevance:6.1f} "
            f"{level.touches:6d} {level.distance_pct:+8.2f}  {'IGEN' if level.flipped else '-':<5} "
            f"{', '.join(level.sources[:4])}"
        )

    support = levels.nearest_support(found)
    resistance = levels.nearest_resistance(found)
    print()
    print(f"  Legkozelebbi SUPPORT:      {support.price:.2f} ({support.distance_pct:+.2f}%)" if support
          else "  Legkozelebbi SUPPORT:      nincs (az ar a melyponton van)")
    print(f"  Legkozelebbi REZISZTENCIA: {resistance.price:.2f} ({resistance.distance_pct:+.2f}%)" if resistance
          else "  Legkozelebbi REZISZTENCIA: nincs (az ar csucson van)")
    return 0


def cmd_scan(args) -> int:
    settings = load_settings()
    symbols = _symbols(args)
    calibration = Calibration.load(settings.calibration_path)
    data = feed.load_or_update(symbols, settings)

    print(f"\n{'ticker':<7} {'ar':>9} {'jelzes':<15} {'pont':>6} {'formacio':<24} {'allapot':<10} {'valoszinuseg':<22} {'support':>9}")
    print("-" * 118)

    forecasts = []
    for symbol in symbols:
        df = data.get(symbol)
        if df is None or df.empty:
            continue
        enriched = indicators.enrich(df, settings.structure.atr_period)
        forecast = analyse(symbol, enriched, settings, calibration)
        forecasts.append((forecast, enriched))

        pattern = forecast.pattern.name if forecast.pattern else "-"
        status = forecast.pattern.status if forecast.pattern else "-"
        support = f"{forecast.support.price:.2f}" if forecast.support else "-"
        print(
            f"{symbol:<7} {forecast.price:9.2f} {forecast.label:<15} {forecast.score:+6.2f} "
            f"{pattern:<24} {status:<10} {forecast.probability_text:<22} {support:>9}"
        )

    if args.plot:
        from stockwatch import charting
        ensure_dirs()
        output = Path("charts")
        output.mkdir(exist_ok=True)
        for forecast, enriched in forecasts:
            figure = charting.build_chart(forecast, enriched)
            path = output / f"{forecast.symbol}.html"
            figure.write_html(path, include_plotlyjs="cdn")
            print(f"  chart mentve: {path}")

    print(DISCLAIMER)
    return 0


def cmd_entries(args) -> int:
    """A mai swing long belepesi lehetosegek rangsorolva, indoklassal."""
    settings = load_settings()
    housekeeping.setup_logging(settings, verbose=False)
    symbols = _symbols(args)
    cfg = settings.entry

    signals: list[entry.EntrySignal] = []
    monitor.scan(
        settings,
        symbols=symbols,
        send_alerts=False,
        entry_sink=signals,
        refresh=not args.offline,
    )

    if cfg.regime_filter:
        ctx = market.load_context(settings, refresh=False)
        print("\nPiaci rezsim (a papirok a sajat indexuket nezik; indexen kivul a sajat trendjuket):")
        for symbol in market.REGIME_INDEXES:
            bench = ctx.benches.get(symbol)
            if bench is None or bench.empty:
                print(f"  {market.INDEX_LABELS[symbol]}: nincs adat - futtasd: python cli.py fetch")
                continue
            regime = market.market_regime(bench, bench.index[-1])
            print(f"  {market.INDEX_LABELS[symbol]}: {'BIKAS' if regime.ok else 'NEM BIKAS'} - {regime.text}")

    shown = signals if args.all else [s for s in signals if s.qualifies(cfg)]
    if not shown:
        print(f"\nMa nincs a feltetelnek megfelelo swing long belepesi pont ({len(signals)} jelolt vizsgalva).")
        if signals and not args.all:
            print("Az elbukott jeloltek okai: python cli.py entries --all")
        print(DISCLAIMER)
        return 0

    print(f"\n{'ticker':<7} {'helyzet':<10} {'formacio':<22} {'pont':>5} {'zaro':>9} {'belepo':>9} "
          f"{'stop':>9} {'cel':>9} {'R/R':>5} {'db':>5}  eredmeny")
    print("-" * 122)
    for s in sorted(shown, key=lambda x: (x.qualifies(cfg), x.score), reverse=True):
        verdict = "JELZES" if s.qualifies(cfg) else (
            "bukott: " + ", ".join(c.name for c in s.failed_checks) if s.failed_checks
            else f"pont < {cfg.min_score:.0f}"
        )
        p = s.plan
        print(f"{s.symbol:<7} {s.kind:<10} {s.pattern.name[:22]:<22} {s.score:5.0f} {s.price:9.2f} "
              f"{p.entry:9.2f} {p.stop:9.2f} {p.target:9.2f} {p.risk_reward:5.2f} {p.shares:5d}  {verdict}")

    if args.details:
        for s in shown:
            print("\n" + "=" * 70)
            print(entry.format_title(s))
            print("-" * 70)
            print(entry.format_body(s, final_bar=not monitor.is_market_open()))

    print("\nHelyzetek: breakout = mai zaro kitores | retest = visszateszt utan fordulas | "
          "setup = meg nem tort ki, figyelendo")
    print(DISCLAIMER)
    return 0


def cmd_entry_backtest(args) -> int:
    """A JELENLEGI beallitas historikus eredmenye, a nyers kitoresekhez kepest."""
    import os
    from stockwatch import entry_research as er

    settings = load_settings()
    housekeeping.setup_logging(settings)
    symbols = _universe_symbols() if args.universe else _symbols(args)
    workers = args.workers or max((os.cpu_count() or 2) - 1, 1)
    cfg = settings.entry

    raw = er.Variant("Minden bikas kitores (szures nelkul)", "breakout", 1.5, "target", overrides={
        "require_uptrend": False, "rsi_min": 0, "rsi_max": 100, "max_extension_atr": 1e9,
        "min_risk_reward": 0, "max_stop_pct": 100, "min_score": 0, "min_breakout_volume": 0,
    })
    current = er.Variant.from_settings(cfg)
    variants = [raw, current]
    if current.earnings:
        from dataclasses import replace as dc_replace
        variants.append(dc_replace(current, name="jelenlegi, earnings-tilalom NELKUL", earnings=False))
    print(f"Belepesi backteszt {len(symbols)} papiron. Jelenlegi beallitas: {current.entry_mode}, "
          f"stop {current.stop_atr} ATR, kilepes: {current.exit_mode}\n")
    out = er.run(symbols, settings, universe=bool(args.universe), workers=workers, grid=variants,
                 holdout=args.holdout)
    if out.get("earnings_coverage"):
        cov = out["earnings_coverage"]
        print(f"Historikus earnings-datumok: {cov['symbols']}/{cov['of']} papirhoz "
              f"(ahol nincs, ott a tilalom nem blokkol).")
    _holdout_warning(out)
    print(RESEARCH_HEADER)
    print("-" * len(RESEARCH_HEADER))
    for row in out["rows"]:
        print(_research_line(row, holdout=args.holdout))
    if cfg.regime_filter and not out["benchmark"]:
        print("\nFIGYELEM: nincs QQQ adat - a piaci rezsim szurovel a jelenlegi beallitas nem ad trade-et. "
              "Futtasd: python cli.py fetch (vagy fetch-universe).")

    if args.csv:
        import pandas as pd
        rows = [{**r.__dict__, "variant": row["variant"].name} for row in out["rows"] for r in row["results"]]
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"\nUgyletlista mentve: {args.csv}")

    print("\nEV(R) = atlagos eredmeny R-ben | 95% = konfidencia-intervallum (nyeres: Wilson, EV: bootstrap) | "
          "+ev = pozitiv evek / osszes ev.\nA 2025-04-01 utani idoszak zarolt vegso teszt (--holdout). "
          "Az earnings-tilalom a historikus jelentesi datumokkal tesztelve (a 'NELKUL' sor a hatasat mutatja).")
    print(DISCLAIMER)
    return 0


RESEARCH_HEADER = (f"{'valtozat':<38} {'n':>4} {'nyer':>5} {'nyer 95%':>9} {'EV(R)':>6} {'EV 95%':>13} "
                   f"{'PF':>5} {'DD':>5} {'+ev':>5} | {'tanito <2022':^17} | {'validacio 2022-25/03':^20}")


def _research_line(row: dict, holdout: bool = False) -> str:
    def fmt(s: dict, width: int = 17) -> str:
        if not s["n"]:
            return f"{0:>4} {'-':>5} {'-':>6}".center(width)
        return f"{s['n']:>4} {s['win']:>5.0%} {s['ev']:>+6.2f}".center(width)

    st = row["stats"]
    a = st["all"]
    pf = a["pf"]
    pf_text = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf is not None else "-")
    if a["n"]:
        win_ci = f"{a['win_lo']:.0%}-{a['win_hi']:.0%}" if a.get("win_lo") is not None else "-"
        ev_ci = f"{a['ev_lo']:+.2f}..{a['ev_hi']:+.2f}" if a.get("ev_lo") is not None else "-"
        main = f"{a['n']:>4} {a['win']:>5.0%} {win_ci:>9} {a['ev']:>+6.2f} {ev_ci:>13}"
    else:
        main = f"{0:>4} {'-':>5} {'-':>9} {'-':>6} {'-':>13}"
    years = st.get("years") or {}
    pos = f"{years.get('positive', 0)}/{years.get('years', 0)}"
    line = (f"{row['variant'].name[:38]:<38} {main} {pf_text:>5} {a['dd']:>5.1f} {pos:>5} | "
            f"{fmt(st['train'])} | {fmt(st['test'], 20)}")
    if holdout and "holdout" in st:
        line += f" | HOLDOUT {fmt(st['holdout'])}"
    return line


def _holdout_warning(out: dict) -> None:
    if out.get("holdout"):
        print(f"\n*** HOLDOUT MEGNYITVA ({out['holdout_openings']}. alkalommal) - a 2025-04-01 utani zarolt "
              "idoszak eredmenye lathato.\n    Ha ennek alapjan valtoztatsz, a holdout mar nem fuggetlen teszt. ***\n")


def cmd_web(args) -> int:
    from stockwatch import webapp

    settings = load_settings()
    housekeeping.setup_logging(settings)
    ensure_dirs()
    webapp.serve(host=args.host, port=args.port, open_browser=not args.no_browser,
                 behind_proxy=args.behind_proxy)
    return 0


def cmd_web_password(args) -> int:
    """A webes felulet jelszava: csak a scrypt hash kerul a titokfajlba."""
    import getpass

    from stockwatch import config, webauth

    if args.disable:
        config.save_env({webauth.HASH_KEY: ""})
        print(f"Jelszo torolve ({config.ENV_PATH}). Proxy mogott a felulet igy nem enged be senkit.")
        return 0
    password = getpass.getpass(f"Uj jelszo (min. {webauth.MIN_PASSWORD} karakter): ")
    if password != getpass.getpass("Meg egyszer: "):
        print("A ket jelszo nem egyezik.")
        return 1
    try:
        hashed = webauth.hash_password(password)
    except ValueError as exc:
        print(exc)
        return 1
    config.save_env({webauth.HASH_KEY: hashed})
    print(f"Jelszo mentve ({config.ENV_PATH}). A futo felulet azonnal ezt hasznalja, "
          "a korabbi bejelentkezesek kileptek.")
    return 0


def cmd_positions(args) -> int:
    """A jelzett trade-ek elo kovetese es eddigi eredmenye."""
    from stockwatch import positions as pos_mod

    settings = load_settings()
    tracker = pos_mod.PositionTracker(settings, read_only=True)     # betolteskor az Alpaca szerint
    items = sorted(tracker.positions.values(), key=lambda p: p.signal_date, reverse=True)
    if not items:
        print("\nMeg nincs kovetett trade. Az elso belepesi jelzes utan jelennek meg itt.")
        return 0

    print(f"\n{'ticker':<7} {'jelzes':<11} {'formacio':<20} {'allapot':<8} {'belepo':>9} {'stop':>9} "
          f"{'cel':>9} {'eredmeny':>9}  megjegyzes")
    print("-" * 110)
    for p in items:
        state = p.position_state()
        stop = state.stop if state else p.stop
        entry_price = f"{p.entry_price:.2f}" if p.entry_price else "-"
        result = f"{p.result_r:+.2f}R" if p.result_r is not None else "-"
        note = p.reason or ("fele eladva, stop a belepon" if state and state.partial_done else "")
        print(f"{p.symbol:<7} {p.signal_date:<11} {p.pattern[:20]:<20} {p.status:<8} {entry_price:>9} "
              f"{stop:>9.2f} {p.target:>9.2f} {result:>9}  {note}")

    stats = tracker.closed_stats()
    if stats["n"]:
        print(f"\nLezart trade-ek: {stats['n']} | nyero: {stats['win']:.0%} | "
              f"atlag: {stats['ev']:+.2f}R | osszesen: {stats['total']:+.1f}R")
    print("\nAhol paper trade van, az Alpaca allapota latszik; a tobbi a jelzes szerinti belepes virtualis kovetese.")
    return 0


def cmd_holdings(args) -> int:
    """Sajat, kezzel felvett poziciok: felvetel, lezaras, torles, kiertekeles."""
    from stockwatch import holdings as hold_mod

    settings = load_settings()
    own = hold_mod.HoldingStore(settings)
    action = args.action

    if action == "add":
        if not (args.symbol and args.price and args.shares):
            print("Kell: --symbol, --price es --shares")
            return 1
        symbol = args.symbol.upper()
        if store.load(symbol, settings).empty and feed.update([symbol], settings).get(symbol) is None:
            print(f"Nem talalhato arfolyam-adat: {symbol}")
            return 1
        try:
            h = own.add(symbol, args.price, args.shares, args.date, args.stop, args.target, args.note or "")
        except ValueError as exc:
            print(f"Hiba: {exc}")
            return 1
        own.save()
        print(f"Felveve: {h.id}")
    elif action in ("close", "remove", "set-stop"):
        if not args.id:
            print("Kell: --id (lasd: python cli.py holdings)")
            return 1
        try:
            if action == "close":
                if not args.price:
                    print("Kell: --price (az eladasi ar)")
                    return 1
                own.close(args.id, args.price, args.date)
            elif action == "set-stop":
                own.update(args.id, stop=args.stop)
            else:
                own.remove(args.id)
        except ValueError as exc:
            print(f"Hiba: {exc}")
            return 1
        own.save()
        print("Kesz.")
        if action == "remove":
            return 0

    results = hold_mod.evaluate_open(settings, own, allow_network=not args.offline)
    if not results:
        print("\nNincs nyitott sajat pozicio.  Felvetel:  python cli.py holdings add --symbol AAPL "
              "--price 180 --shares 10 [--date 2026-03-02] [--stop 170] [--target 210]")
        return 0

    print(f"\n{'id':<26} {'ticker':<7} {'db':>7} {'belepo':>9} {'zaro':>9} {'P/L $':>10} {'P/L %':>7} "
          f"{'stop':>9} {'minosites':<11}")
    print("-" * 104)
    evaluations = []
    for r in results:
        if isinstance(r, hold_mod.Holding):
            print(f"{r.id:<26} {r.symbol:<7} {r.shares:>7g} {r.entry_price:>9.2f}  nincs arfolyam-adat")
            continue
        h = r.holding
        stop = f"{h.stop:.2f}" if h.stop is not None else "nincs"
        print(f"{h.id:<26} {h.symbol:<7} {h.shares:>7g} {h.entry_price:>9.2f} {r.price:>9.2f} "
              f"{r.pnl:>+10,.0f} {r.pnl_pct:>+6.1f}% {stop:>9} {r.verdict_text.upper():<11}")
        evaluations.append(r)

    for r in evaluations:
        h = r.holding
        print(f"\n{h.symbol} ({h.id}) - {r.verdict_text.upper()}")
        for check in r.checks:
            mark = {"ok": "OK ", "warn": " ! ", "bad": "NEM", "info": " i "}[check.level]
            print(f"  [{mark}] {check.name}: {check.detail}")
        print(f"  Stop javaslat: {r.suggested_stop:.2f} $ ({r.suggested_stop_text})")
        if r.target is not None:
            print(f"  Cel: {r.target:.2f} $ ({r.target_source})")
    print(DISCLAIMER)
    return 0


def cmd_broker(args) -> int:
    """Alpaca paper szamla: allapot es szinkronizalas."""
    from stockwatch import broker as broker_mod

    settings = load_settings()
    client = broker_mod.AlpacaClient.from_env()
    if client is None:
        print("Nincs Alpaca kulcs. Add meg a webes felulet Beallitasok fulen (python cli.py web).")
        return 1
    book = broker_mod.BrokerBook(settings, read_only=True)
    try:
        if args.action == "sync":
            final = not bool(client.clock().get("is_open"))
            with broker_mod.book_lock(settings):
                book = broker_mod.BrokerBook(settings)
                events = broker_mod.sync(settings, client, book, final_bar=final)
                book.save()
            for trade, event in events:
                print(f"  {trade.symbol}: {broker_mod.EVENT_LABELS.get(event, event)}")
            print(f"Szinkronizalva, {len(events)} esemeny.")
        account = client.account()
    except broker_mod.BrokerError as exc:
        print(f"Hiba: {exc}")
        return 1

    print(f"\nAlpaca PAPER szamla: {account.get('status')} | equity {float(account['equity']):,.2f} $ | "
          f"vasarloero {float(account['buying_power']):,.2f} $")
    print(f"Automata bekuldes: {'BE' if settings.broker.auto_trade else 'KI'} | "
          f"max. nyitott: {settings.broker.max_open_trades}")
    trades = sorted(book.trades.values(), key=lambda t: t.submitted_at, reverse=True)
    if not trades:
        print("\nMeg nincs paper trade.")
        return 0
    print(f"\n{'ticker':<7} {'jelzes':<11} {'allapot':<10} {'db':>5} {'limit':>9} {'belepo':>9} {'stop':>9} {'cel':>9} {'eredmeny':>10}")
    print("-" * 88)
    for t in trades:
        entry_price = f"{t.entry_price:.2f}" if t.entry_price else "-"
        pnl = f"{t.realised_pnl:+.2f}" if t.realised_pnl is not None else "-"
        print(f"{t.symbol:<7} {t.signal_date:<11} {t.status:<10} {t.shares:>5} {t.entry_limit:>9.2f} "
              f"{entry_price:>9} {t.stop:>9.2f} {t.target:>9.2f} {pnl:>10}")
    stats = book.stats()
    if stats["n"]:
        print(f"\nLezart: {stats['n']} | nyero: {stats['win']:.0%} | osszesen: {stats['pnl']:+,.2f} $")
    for strategy, r in broker_mod.readiness(book, settings).items():
        print(f"\nEles kapu - {strategy}: {'ATMENT, elesitheto' if r['ready'] else 'meg nem ment at'}")
        for text, ok in r["checks"]:
            print(f"  [{'OK ' if ok else '   '}] {text}")
        print(f"  nyeresi arany 95%-os intervalluma: {r['win_lo']:.0%} - {r['win_hi']:.0%}")
    return 0


def cmd_index_scan(args) -> int:
    """A Nasdaq-100 / S&P 500 atnezese (a beallitasokban bekapcsolt indexek)."""
    from stockwatch import indexes

    settings = load_settings()
    if args.nasdaq100 or args.sp500:
        settings = settings.model_copy(update={"scan": settings.scan.model_copy(
            update={"nasdaq100": args.nasdaq100, "sp500": args.sp500})})
    if not indexes.IndexMembers(settings).enabled():
        print("\nEgy index sincs bekapcsolva. Beallitasok fulon, vagy: python cli.py index-scan --sp500")
        return 1

    housekeeping.setup_logging(settings)
    dispatcher = Dispatcher(settings) if args.alerts else None
    result = monitor.index_scan(settings, dispatcher=dispatcher, send_alerts=args.alerts)
    print()
    for item in indexes.IndexMembers(settings).status():
        flag = "BE" if item["enabled"] else "ki"
        print(f"  {item['label']:<18} {flag}  {item['count']:>4} papir  (lista: {item['fetched'] or '-'})")
    print(f"\n{result['analysed']} / {result['symbols']} papir elemezve (utolso gyertya: {result['last_bar']}).")

    rows = [s for s in result["signals"] if args.all or (s["qualifies"] and not s["muted"])]
    if not rows:
        print("Nincs belepesi jelzes az indexpapirokon." + ("" if args.all else "  (--all: az elbukottak is)"))
        return 0
    print(f"\n{'ticker':<7} {'helyzet':<9} {'formacio':<24} {'pont':>5} {'zaro':>9} {'limit':>9} {'stop':>9} {'cel':>9}  eredmeny")
    print("-" * 104)
    for s in rows:
        p = s["plan"]
        verdict = "JELZES" if s["qualifies"] and not s["muted"] else (
            "elnemitva (elokeszulet)" if s["muted"] else "bukott: " + ", ".join(s["failed"]))
        print(f"{s['symbol']:<7} {s['kind']:<9} {s['pattern'][:24]:<24} {s['score']:>5.0f} {s['price']:>9.2f} "
              f"{p['entry_limit']:>9.2f} {p['stop']:>9.2f} {p['target']:>9.2f}  {verdict}")
    print(DISCLAIMER)
    return 0


def cmd_ledger(args) -> int:
    """A jelzesnaplo: minden lezart gyertyas belepesi dontes, okkal."""
    from stockwatch import ledger

    rows = []
    for rec in ledger.iter_records():
        if args.decision and rec.get("decision") != args.decision:
            continue
        if args.symbol and rec.get("symbol") != args.symbol.upper():
            continue
        rows.append(rec)
        if len(rows) >= args.limit:
            break
    if not rows:
        print("A jelzesnaplo meg ures. A monitor zaras utani futasa tolti (python cli.py monitor).")
        return 0
    print(f"\n{'jel-id':<17} {'nap':<11} {'ticker':<7} {'helyzet':<9} {'formacio':<22} {'pont':>5}  {'dontes':<11} ok")
    print("-" * 120)
    for r in rows:
        print(f"{r['signal_id']:<17} {r['as_of']:<11} {r['symbol']:<7} {r['kind']:<9} "
              f"{(r.get('pattern') or {}).get('name', '')[:22]:<22} {r.get('score', 0):>5.0f}  "
              f"{ledger.DECISION_LABELS.get(r['decision'], r['decision']):<11} {r.get('reason', '')}")
    print(f"\nKodverzio most: {ledger.code_version()}   |   eletut: python cli.py trace <jel-id>   "
          f"|   visszajatszas: python cli.py replay <jel-id>")
    return 0


def cmd_revalidate(args) -> int:
    from stockwatch import revalidate

    settings = load_settings()
    housekeeping.setup_logging(settings)
    results = revalidate.revalidate(settings, dispatcher=Dispatcher(settings) if args.alerts else None)
    if not results:
        print("Nincs ujraellenorizendo jelzes (az elmult napok kikuldott jelzesei mar ellenorizve, vagy nem volt ilyen).")
        return 0
    for r in results:
        print(f"  {r['symbol']:<7} {r['signal_id']}  {r['decision']:<10} {r['reason']}"
              + (f"  [{', '.join(r['actions'])}]" if r["actions"] else ""))
    return 0


def cmd_trace(args) -> int:
    from stockwatch import ledger, replay

    t = replay.trace(args.signal_id)
    if not t["records"] and not t["alerts"]:
        print(f"Nincs ilyen jelzes: {args.signal_id}")
        return 1
    print(f"\nJelzes {args.signal_id}")
    for r in t["records"]:
        v = r.get("versions") or {}
        print(f"  [naplo] {r['recorded_at']}  {r['symbol']} {r['as_of']} {r['kind']} - "
              f"{ledger.DECISION_LABELS.get(r['decision'], r['decision'])}: {r.get('reason', '')}")
        print(f"          pont {r.get('score')}, beallitas {v.get('config')}, kod {v.get('code')}, "
              f"kalibracio {v.get('calibration')}, pillanatkep: {'van' if r.get('snapshot') else 'nincs'}")
    for a in t["alerts"]:
        print(f"  [ertesites] {a['timestamp']}  {a['event']}: {a['title']}")
    if t["position"]:
        p = t["position"]
        print(f"  [virtualis pozicio] {p['status']}  belepo {p.get('entry_price')}  eredmeny {p.get('result_r')}R  {p.get('reason', '')}")
    if t["trade"]:
        tr = t["trade"]
        print(f"  [paper trade] {tr['status']}  {tr['shares']} db  belepo {tr.get('entry_price')}  eredmeny {tr.get('realised_pnl')} $")
    if t["review"]:
        rv = t["review"]
        print(f"  [utolagos ertekeles] {rv['grade']} - {rv['outcome']}  (valtozas {rv.get('change_pct') or 0:+.1f}%)")
    return 0


def cmd_replay(args) -> int:
    from stockwatch import replay

    try:
        res = replay.replay(args.signal_id)
    except ValueError as exc:
        print(f"Hiba: {exc}")
        return 1
    print(f"\nVisszajatszas: {res['signal_id']}  ({res['symbol']}, {res['as_of']})")
    print(f"  adatforras: {res['source']}  |  azonos adatablak: {'igen' if res['same_data'] else 'nem'}")
    print(f"  kodverzio akkor: {res['code_then']}  most: {res['code_now']}")
    for note in res["notes"]:
        print(f"  megjegyzes: {note}")
    for d in res["diffs"]:
        print(f"  ELTERES  {d['field']}: akkor {d['then']}  ->  most {d['now']}")
    print(f"\n  {res['verdict']}")
    return 0 if res["found"] and not res["diffs"] else 2


def _universe_symbols() -> list[str]:
    import yaml
    path = Path(__file__).parent / "config" / "universe.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    from stockwatch.config import yaml_tickers

    return yaml_tickers(raw.get("symbols", []))


def cmd_fetch_universe(args) -> int:
    settings = load_settings()
    housekeeping.setup_logging(settings)
    symbols = _universe_symbols() + [settings.entry.benchmark.upper()]
    print(f"{len(symbols)} papir letoltese a kutatasi univerzumba (data/universe)...")
    data = {}
    for start in range(0, len(symbols), 25):          # kotegekben, kimeletesen
        data.update(feed.update(symbols[start:start + 25], settings, universe=True))
    ok = [s for s in symbols if s in data and not data[s].empty]
    missing = sorted(set(symbols) - set(ok))
    print(f"Kesz: {len(ok)} papir.")
    if missing:
        print(f"Nem sikerult: {', '.join(missing)}")
    print("Kovetkezo lepes: python cli.py entry-research --universe")
    return 0


def cmd_backtest_symbol(args) -> int:
    """Backteszt oldal parancssorbol: egy papir teljes tortenete, egy strategia."""
    import json

    from stockwatch.backtest_page import data as bt_data, runner as bt_runner, runs as bt_runs

    settings = load_settings()
    housekeeping.setup_logging(settings)
    symbol = args.symbol.upper()
    if args.fetch or bt_data.load(symbol).empty:
        m = bt_data.collect(symbol, args.start, settings, progress=print)
        print(f"Adat: {m['first']} -> {m['last']} ({m['bars']} gyertya)"
              + (f"  FIGYELEM: {'; '.join(m['quality']['issues'])}" if m["quality"]["issues"] else ""))
    overrides = {}
    for item in args.set or []:
        key, sep, value = item.partition("=")
        if not sep:
            print(f"Hibas --set: {item} (alak: kulcs=ertek)")
            return 2
        overrides[key.strip()] = value.strip()
    result = bt_runner.run(symbol, args.strategy, settings, start=args.start, end=args.end, overrides=overrides,
                           cost_bps=args.cost_bps, include_holdout=args.include_holdout, alloc_pct=args.alloc)
    run_id = bt_runs.save(result)
    print()
    print(bt_runner.summary_line(result))
    for name, label in (("train", "Tanito (<2022)"), ("test", "Validacio (2022-2025.03)"), ("holdout", "HOLDOUT")):
        s = result["periods"].get(name)
        if s and s.get("n"):
            print(f"  {label:<26} {s['n']:>4} kotes  nyeresi {s['win'] * 100:5.1f}%  atlag {s['ev']:+.2f}%  "
                  f"PF {s['pf'] if s['pf'] is not None else float('inf'):.2f}")
    for w in result["warnings"]:
        print(f"  ! {w}")
    if args.json:
        Path(args.json).write_text(json.dumps(bt_runs.load(run_id), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Mentve: {run_id} (a felulet Backteszt fulen is megnyithato)")
    return 0


def cmd_meanrev_research(args) -> int:
    """A Visszahuzas (RSI2) strategia validalasa: tanito / validacio / (holdout), evenkent, elfogadasi feltetellel."""
    from stockwatch import meanrev_research as mr

    settings = load_settings()
    housekeeping.setup_logging(settings)
    if args.universe:
        symbols = _universe_symbols()
    elif args.symbols:
        symbols = _symbols(args)
    else:
        from stockwatch import indexes as indexes_mod

        members = indexes_mod.IndexMembers(settings)
        symbols = list(dict.fromkeys(members.members("nasdaq100") + members.members("sp500")))
    params = settings.strategies.sandbox.params if args.sandbox else settings.strategies.pullback.params
    out_dir, study, name = mr.OUT_DIR, "meanrev", "Visszahuzas (RSI2)"
    if args.deep:
        params, out_dir, study, name = settings.strategies.deep.params, mr.DEEP_DIR, "deep", "Mely visszahuzas"
    if args.etf:
        from stockwatch import feed

        symbols, params = list(settings.strategies.etf.symbols), settings.strategies.etf.params
        out_dir, study, name = mr.ETF_DIR, "etf", "ETF visszahuzas"
        print(f"ETF adatok frissitese ({len(symbols)} ETF)...")
        feed.update(symbols, settings, universe=True)
    print(f"{name} validalas {len(symbols)} papiron"
          f"{' (HOLDOUT megnyitva - naplozva!)' if args.holdout else ''}...")
    report = mr.run(symbols, settings, params, holdout=args.holdout, neighbourhood_check=args.neighbourhood,
                    survivorship_symbols=_universe_symbols() if args.survivorship_check and not args.etf else None,
                    out_dir=out_dir, study=study)
    print()
    print(mr.report_markdown(__import__("json").loads(
        __import__("json").dumps(report, default=mr._default).replace("Infinity", "null"))))
    print(f"Mentve: {out_dir}")
    return 0


def cmd_entry_research(args) -> int:
    import os
    from stockwatch import entry_research as er

    settings = load_settings()
    housekeeping.setup_logging(settings)
    if args.indexes:
        from stockwatch import indexes as indexes_mod

        members = indexes_mod.IndexMembers(settings)
        symbols = list(dict.fromkeys(members.members("nasdaq100") + members.members("sp500")))
    else:
        symbols = _universe_symbols() if args.universe else _symbols(args)
    universe = bool(args.universe or args.indexes)
    workers = args.workers or max((os.cpu_count() or 2) - 1, 1)

    print(f"Kutatas {len(symbols)} papiron, {workers} szalon. Az elso futas lassu "
          f"(papironkent ~1 perc / szal), utana cache-bol masodpercek.\n")
    grid = None
    if args.study:
        grid = [er.Variant.from_settings(settings.entry, name="ELO beallitas")] + er.study_grid(settings.entry)
        if args.only:
            wanted = {name.strip() for name in args.only.split(";")}
            grid = [grid[0]] + [v for v in grid[1:] if v.name in wanted]
    out = er.run(symbols, settings, universe=universe, workers=workers, holdout=args.holdout, grid=grid)
    rows = out["rows"]
    baseline = rows[0]["stats"]
    _holdout_warning(out)

    if args.study:
        print(f"{out['symbols']} papir, {out['events']} bikas kitores. Elore rogzitett vizsgalat "
              f"({len(rows) - 1} valtozat) - MINDEN sor latszik, nem csak a nyertesek:\n")
        print(RESEARCH_HEADER)
        print("-" * len(RESEARCH_HEADER))
        print(_research_line(rows[0], holdout=args.holdout) + "   <- ELO")
        winners = {id(r) for r in er.robust_winners(rows[1:], baseline)}
        for row in rows[1:]:
            print(_research_line(row, holdout=args.holdout) + ("   <- robusztus" if id(row) in winners else ""))
        print(DISCLAIMER)
        return 0

    def line(row):
        return _research_line(row, holdout=args.holdout)

    header = RESEARCH_HEADER
    print(f"{out['symbols']} papir, {out['events']} bikas kitores. "
          f"Benchmark: {'van' if out['benchmark'] else 'NINCS (a QQQ-valtozatok kimaradnak)'}")
    print(f"Tanito idoszakban pozitiv alakzatok: {', '.join(out['whitelist']) or '-'}\n")
    print(header)
    print("-" * len(header))
    print(line(rows[0]) + "   <- az ELO beallitas (osszehasonlitasi alap)")
    print()
    winners = er.robust_winners(rows[1:], baseline)
    print(f"Robusztus valtozatok ({len(winners)} db): a rosszabbik idoszak EV-je ES az EV 95%-os also "
          f"hatara is legalabb a jelenlegi beallitasee. Nyeresi arany szerint, elso {args.top}:")
    print("-" * len(header))
    for row in winners[: args.top]:
        print(line(row))

    if args.csv:
        import pandas as pd
        records = []
        for row in rows:
            for period, st in row["stats"].items():
                if period == "years":
                    continue
                records.append({"variant": row["variant"].name, "period": period, **st})
        pd.DataFrame(records).to_csv(args.csv, index=False)
        print(f"\nTeljes tabla: {args.csv}")

    print("\nn = trade-ek | nyer = nyero arany | 95% = konfidencia-intervallum | EV = atlagos eredmeny R-ben | "
          "PF = profit faktor | DD = legnagyobb visszaeses R-ben | +ev = pozitiv evek\n"
          "A 2025-04-01 utani idoszak zarolt vegso teszt: csak --holdout kapcsoloval latszik (naplozva).")
    print(DISCLAIMER)
    return 0


def cmd_backtest(args) -> int:
    settings = load_settings()
    symbols = _symbols(args)

    print(f"Backtest {len(symbols)} papiron - ez tobb percig tarthat...\n")
    result = backtest.run(symbols, settings, walk_forward=args.walk_forward)

    stats = result.get("patterns", {})
    if not stats:
        print("Nem keletkezett statisztika.")
        return 1

    print(f"\n{'formacio':<34} {'n':>5} {'talalat':>9} {'timeout':>8} {'EV (R)':>8} {'med.nap':>8}")
    print("-" * 78)
    for key, entry in sorted(stats.items()):
        print(
            f"{key:<34} {entry['samples']:>5} {entry['hit_rate']:>8.1%} "
            f"{entry['timeout_rate']:>7.0%} {entry['expectancy_r']:>+8.2f} "
            f"{str(entry['median_bars'] or '-'):>8}"
        )

    print(f"\nOsszesen {result['total_trades']} triggerelt formacio.")

    if result.get("warnings"):
        print("\n!!! JOZANSAGI FIGYELMEZTETESEK !!!")
        for warning in result["warnings"]:
            print(f"  {warning}")
    else:
        print("\nJozansagi ellenorzes: rendben (nincs gyanusan magas talalati arany).")

    if args.walk_forward and "walk_forward" in result:
        print("\nWalk-forward stabilitas (regebbi fel vs. ujabb fel):")
        for key, entry in result["walk_forward"].items():
            if isinstance(entry, dict):
                flag = "stabil" if entry["stable"] else "INSTABIL"
                print(f"  {key:<34} {entry['train_hit_rate']:.0%} -> {entry['test_hit_rate']:.0%}  ({flag})")

    if args.purge_cache:
        import shutil
        if settings.universe_dir.exists():
            shutil.rmtree(settings.universe_dir)
            settings.universe_dir.mkdir(parents=True, exist_ok=True)
            print("\nA backtest univerzum cache torolve.")

    print(DISCLAIMER)
    return 0


def cmd_monitor(args) -> int:
    settings = load_settings()
    housekeeping.setup_logging(settings, verbose=args.verbose)

    if args.once:
        dispatcher = Dispatcher(settings, dry_run=args.dry_run)
        state = monitor.SignalState(settings, read_only=args.dry_run)
        final_bar = not monitor.is_market_open()
        results = monitor.scan(settings, dispatcher=dispatcher, state=state, final_bar=final_bar)
        if final_bar and (settings.scan.nasdaq100 or settings.scan.sp500):
            res = monitor.index_scan(settings, dispatcher=dispatcher, send_alerts=True, state=state)
            print(f"Indexszkenneles: {res['analysed']} papir elemezve.")
        monitor.broker_cycle(dispatcher, final_bar=final_bar, read_only=args.dry_run)
        print(f"\nEgy ciklus lefutott, {len(results)} papir elemezve.")
        if not final_bar:
            print("A piac nyitva van: belepesi riasztas csak zaras utan megy ki.")
        return 0

    monitor.run_forever(settings, dry_run=args.dry_run)
    return 0


def cmd_data_usage(args) -> int:
    settings = load_settings()
    entries = housekeeping.disk_usage(settings)

    print(f"\n{'komponens':<38} {'meret':>12} {'limit':>12} {'kihasznalt':>11}")
    print("-" * 78)
    for entry in entries:
        limit = f"{entry.limit_bytes / housekeeping.MB:.0f} MB" if entry.limit_bytes else "-"
        usage = f"{entry.pct_of_limit:.0f}%" if entry.pct_of_limit is not None else "-"
        print(f"{entry.label:<38} {entry.mb:>9.2f} MB {limit:>12} {usage:>11}")

    total = housekeeping.total_usage_mb(settings)
    print("-" * 78)
    print(f"{'OSSZESEN':<38} {total:>9.2f} MB {settings.storage.max_data_mb:>9.0f} MB "
          f"{total / settings.storage.max_data_mb * 100:>10.0f}%")

    if total > settings.storage.max_data_mb:
        print("\nFIGYELEM: tullepted a beallitott kuszobot. Futtasd: python cli.py housekeeping")
    return 0


def cmd_housekeeping(args) -> int:
    settings = load_settings()
    housekeeping.setup_logging(settings)
    result = housekeeping.run(settings)
    print(f"\nTakaritas kesz: {result}")
    return 0


def cmd_test_alert(args) -> int:
    from stockwatch.alerts.discord import DiscordChannel
    from stockwatch.alerts.telegram import TelegramChannel

    settings = load_settings()
    housekeeping.setup_logging(settings)

    channels = []
    if settings.alerts.telegram:
        telegram = TelegramChannel(settings)
        if telegram.test():
            channels.append(telegram)
        else:
            print("\nA Telegram kapcsolat nem mukodik. Ellenorizd a titokfajlt:")
            print("  TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...")
    if settings.alerts.discord:
        discord = DiscordChannel(settings)
        name = discord.test()
        if name:
            print(f"Discord webhook rendben: {name}")
            channels.append(discord)
        else:
            print("\nA Discord nincs beallitva vagy nem elerheto. Beallitas: python cli.py discord")
    if not channels:
        return 1
    return _send_test_alert(args, settings, channels)


def cmd_discord(args) -> int:
    """Discord webhook beallitasa: rejtett bevitel, ellenorzes, mentes a titokfajlba, probauzenet."""
    import getpass

    from stockwatch import config
    from stockwatch.alerts.discord import DiscordChannel, is_discord_url

    settings = load_settings()
    housekeeping.setup_logging(settings)
    if args.disable:
        config.save_env({"DISCORD_WEBHOOK_URL": ""})
        print(f"Discord webhook torolve ({config.ENV_PATH}).")
        return 0

    print("Discord: Szerver beallitasok -> Integraciok -> Webhookok -> Uj webhook -> Webhook URL masolasa")
    url = getpass.getpass("Webhook URL (beillesztes, nem latszik): ").strip()
    if not is_discord_url(url):
        print("Ez nem Discord webhook cim (https://discord.com/api/webhooks/<szam>/<token>).")
        return 1
    channel = DiscordChannel(settings, url=url)
    name = channel.test()
    if not name:
        print("A Discord nem fogadta el a webhookot (torolve lett, vagy elirtad?).")
        return 1
    config.save_env({"DISCORD_WEBHOOK_URL": url})
    print(f"Mentve ({config.ENV_PATH}). Webhook: {name}")
    print("A futo monitor ujrainditas utan kuld ide (szerveren: sudo systemctl restart stockwatch-monitor).")
    return _send_test_alert(args, settings, [channel])


def cmd_lab(args) -> int:
    """Strategia-labor: folyamatos strategiatesztelas multbeli adatokon (lab.py)."""
    import os

    from stockwatch import lab

    settings = load_settings()
    housekeeping.setup_logging(settings, verbose=args.verbose)
    ensure_dirs()
    if args.action == "status":
        st = lab.read_status()
        data = lab.load_store()
        print(f"Labor: {'FUT' if st.get('running') else 'NEM FUT'} | {st.get('phase')} {st.get('detail', '')}")
        if st.get("progress"):
            print(f"  halad: {st['progress'][0]}/{st['progress'][1]}")
        if st.get("coverage"):
            c = st["coverage"]
            print(f"  lefedettseg: {c['with_events']}/{c['symbols']} papir kitores-cache-sel, {c['with_data']} adattal")
        print(f"\n{'kiserlet':<34} {'univerzum':<10} {'allapot':<8} {'n':>5} {'nyer':>5} {'EV(R)':>7} {'elo n':>6} {'elo EV':>7}")
        print("-" * 90)
        for exp in data["experiments"]:
            s = exp.get("summary") or {}
            a, f = s.get("all") or {}, s.get("forward") or {}
            win = f"{a['win']:.0%}" if a.get("win") is not None else "-"
            ev = f"{a['ev']:+.2f}" if a.get("ev") is not None else "-"
            fev = f"{f['ev']:+.2f}" if f.get("ev") is not None else "-"
            print(f"{exp['name'][:34]:<34} {exp['universe']:<10} {exp['status']:<8} {a.get('n', 0):>5} {win:>5} "
                  f"{ev:>7} {f.get('n', 0):>6} {fev:>7}")
        print(f"\nOsszesen valaha lefuttatott kiserlet: {data.get('created_total', 0)}")
        return 0
    workers = args.workers or 1
    # a numerikus konyvtarak ne inditsanak magonkent szalat (a gyerekfolyamatok ezt oroklik):
    # tobb folyamat mellett a szalak versengese sokszorosara lassit
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    if args.once:
        did = lab.run_once(settings, workers=workers)
        print("Kor kesz." if did else "Nem volt teendo.")
        return 0
    lab.run_forever(workers=workers)
    return 0


def cmd_heartbeat(args) -> int:
    """Eletjel most, kezzel: a tarolt adatbol (letoltes es elemzes nelkul)."""
    import pandas as pd

    from stockwatch import heartbeat, monitor, store

    settings = load_settings()
    housekeeping.setup_logging(settings)
    symbols = monitor.default_symbols(settings)
    bars = [df.index[-1] for df in (store.load(s, settings) for s in symbols) if not df.empty]
    report = heartbeat.DayReport(day=pd.Timestamp.now(tz=monitor.MARKET_TZ).normalize(),
                                 watchlist_total=len(symbols), watchlist_analysed=len(bars),
                                 last_bar=max(bars, default=None))
    alert = heartbeat.build(settings, report, sent=None, errors=None, started=None)
    alert.title = "[KEZI] " + alert.title
    if args.print:
        print(f"{alert.title}\n{alert.body}")
        return 0
    results = Dispatcher(settings).broadcast(alert)
    print(f"{alert.title}\n{alert.body}\n\nKikuldve: {results}")
    return 0 if any(results.values()) else 1


def _send_test_alert(args, settings, channels) -> int:
    """Probauzenet chart-keppel, KOZVETLENUL a megadott csatornakra - a jelzesnaploba
    (signals.jsonl) nem kerul, igy az Ertesitesek statisztikajat nem torzitja."""
    from stockwatch import charting

    symbols = _symbols(args)[:1]
    data = feed.load_or_update(symbols, settings)
    if not data:
        print("Nincs adat a teszt jelzeshez.")
        return 1

    symbol, df = next(iter(data.items()))
    enriched = indicators.enrich(df, settings.structure.atr_period)
    forecast = analyse(symbol, enriched, settings)
    chart_png = None
    if settings.alerts.send_chart:
        try:
            chart_png = charting.to_png(charting.build_chart(forecast, enriched))
        except Exception:
            print("(A chart-kep nem keszult el - a probauzenet kep nelkul megy.)")

    alert = Alert(
        symbol=symbol,
        title="[TESZT] " + format_title(forecast, "new"),
        body=format_body(forecast, "new"),
        forecast=forecast,
        event="new",
        chart_png=chart_png,
    )
    failed = [c.name for c in channels if not c.send(alert)]
    if failed:
        print(f"\nA probauzenet nem ment ki: {', '.join(failed)} (lasd a naplot).")
        return 1
    print(f"\nProbauzenet elkuldve ({', '.join(c.name for c in channels)}"
          f"{', chart-keppel' if chart_png else ''}).")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockWatch - NASDAQ chart formacio figyelo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=DISCLAIMER,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = dict(help="tickerek vesszovel, vagy 'all' a watchlisthez")

    p = subparsers.add_parser("fetch", help="arfolyamadat letoltese es cache-elese")
    p.add_argument("--symbols", **common)
    p.add_argument("--full", action="store_true", help="teljes historia ujraletoltese")
    p.set_defaults(func=cmd_fetch)

    p = subparsers.add_parser("scan", help="elemzes es formaciokereses")
    p.add_argument("--symbols", **common)
    p.add_argument("--plot", action="store_true", help="HTML chartok mentese a charts/ mappaba")
    p.set_defaults(func=cmd_scan)

    p = subparsers.add_parser("entries", help="swing long belepesi lehetosegek ma")
    p.add_argument("--symbols", **common)
    p.add_argument("--all", action="store_true", help="az elbukott jelolteket is mutassa, okokkal")
    p.add_argument("--details", action="store_true", help="teljes jelzesszoveg (terv, szurok, pontok)")
    p.add_argument("--offline", action="store_true", help="ne frissitsen, csak a cache-bol")
    p.set_defaults(func=cmd_entries)

    p = subparsers.add_parser("entry-backtest", help="a jelenlegi belepesi beallitas historikus tesztje")
    p.add_argument("--symbols", **common)
    p.add_argument("--universe", action="store_true", help="a config/universe.yaml papirjain")
    p.add_argument("--workers", type=int, help="parhuzamos szalak (alap: magok-1)")
    p.add_argument("--csv", help="ugyletlista mentese CSV-be")
    p.add_argument("--holdout", action="store_true", help="a ZAROLT vegso teszt-idoszak megnyitasa (naplozva)")
    p.set_defaults(func=cmd_entry_backtest)

    p = subparsers.add_parser("web", help="helyi webes felulet (http://127.0.0.1:8765)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="ne nyissa meg automatikusan a bongeszot")
    p.add_argument("--host", default="127.0.0.1",
                   help="figyelt cim (alap: 127.0.0.1; mas cimen jelszo kotelezo)")
    p.add_argument("--behind-proxy", action="store_true",
                   help="HTTPS reverse proxy mogott fut (szerver): jelszo kotelezo, X-Forwarded-* fejlecek")
    p.set_defaults(func=cmd_web)

    p = subparsers.add_parser("web-password", help="a webes felulet jelszavanak beallitasa")
    p.add_argument("--disable", action="store_true", help="jelszo torlese")
    p.set_defaults(func=cmd_web_password)

    p = subparsers.add_parser("positions", help="a jelzett trade-ek kovetese es eredmenye")
    p.set_defaults(func=cmd_positions)

    p = subparsers.add_parser("holdings", help="sajat, kezzel felvett poziciok es kiertekelesuk")
    p.add_argument("action", nargs="?", default="list", choices=["list", "add", "close", "set-stop", "remove"])
    p.add_argument("--id", help="a pozicio azonositoja (close / set-stop / remove)")
    p.add_argument("--symbol")
    p.add_argument("--price", type=float, help="belepo ar (add) vagy eladasi ar (close)")
    p.add_argument("--shares", type=float)
    p.add_argument("--date", help="EEEE-HH-NN (alap: ma)")
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--note")
    p.add_argument("--offline", action="store_true", help="ne kerdezze le az earnings datumokat")
    p.set_defaults(func=cmd_holdings)

    p = subparsers.add_parser("broker", help="Alpaca paper szamla: allapot es szinkronizalas")
    p.add_argument("action", nargs="?", default="status", choices=["status", "sync"])
    p.set_defaults(func=cmd_broker)

    p = subparsers.add_parser("index-scan", help="Nasdaq-100 / S&P 500 atnezese belepesi jelzesekert")
    p.add_argument("--nasdaq100", action="store_true", help="csak most: a Nasdaq-100 (a beallitastol fuggetlenul)")
    p.add_argument("--sp500", action="store_true", help="csak most: az S&P 500 (a beallitastol fuggetlenul)")
    p.add_argument("--all", action="store_true", help="az elbukott jelolteket is mutassa")
    p.add_argument("--alerts", action="store_true", help="kuldjon is ertesitest (alapbol csak kiirja)")
    p.set_defaults(func=cmd_index_scan)

    p = subparsers.add_parser("ledger", help="jelzesnaplo: minden belepesi dontes, okkal")
    p.add_argument("--decision", choices=["alerted", "manual", "skipped", "rejected", "confirmed", "revoked"])
    p.add_argument("--symbol")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_ledger)

    p = subparsers.add_parser("revalidate", help="az elozo esti jelzesek ujraellenorzese a veglegesitett adaton")
    p.add_argument("--alerts", action="store_true", help="visszavonasnal ertesites is menjen")
    p.set_defaults(func=cmd_revalidate)

    p = subparsers.add_parser("trace", help="egy jelzes teljes eletutja (naplo -> ertesites -> pozicio -> paper)")
    p.add_argument("signal_id")
    p.set_defaults(func=cmd_trace)

    p = subparsers.add_parser("replay", help="egy naplozott dontes visszajatszasa es osszevetese")
    p.add_argument("signal_id")
    p.set_defaults(func=cmd_replay)

    p = subparsers.add_parser("fetch-universe", help="bovitett kutatasi minta letoltese (config/universe.yaml)")
    p.set_defaults(func=cmd_fetch_universe)

    p = subparsers.add_parser("entry-research", help="belepesi szabalyvaltozatok osszehasonlitasa")
    p.add_argument("--symbols", **common)
    p.add_argument("--universe", action="store_true", help="a config/universe.yaml papirjain")
    p.add_argument("--indexes", action="store_true", help="a Nasdaq-100 + S&P 500 osszes papirjan")
    p.add_argument("--workers", type=int, help="parhuzamos szalak (alap: magok-1)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--csv", help="teljes tabla mentese")
    p.add_argument("--holdout", action="store_true", help="a ZAROLT vegso teszt-idoszak megnyitasa (naplozva)")
    p.add_argument("--study", action="store_true",
                   help="elore rogzitett, szakirodalmi alapu szurok vizsgalata az elo beallitashoz kepest")
    p.add_argument("--only", help="--study mellett: csak ezek a valtozatok (nevek ;-vel elvalasztva)")
    p.set_defaults(func=cmd_entry_research)

    p = subparsers.add_parser("meanrev-research", help="a Visszahuzas (RSI2) strategia validalasa")
    p.add_argument("--symbols", **common)
    p.add_argument("--universe", action="store_true", help="a config/universe.yaml papirjain (alap: az indexeken)")
    p.add_argument("--sandbox", action="store_true", help="a homokozo parametereivel")
    p.add_argument("--etf", action="store_true", help="az ETF visszahuzas strategia, a sajat ETF-listajan")
    p.add_argument("--deep", action="store_true", help="a Mely visszahuzas strategia (tag, 5 ATR-es stop)")
    p.add_argument("--neighbourhood", action="store_true", help="stabilitasvizsgalat a szomszedos beallitasokon")
    p.add_argument("--survivorship-check", action="store_true",
                   help="osszevetes a config/universe.yaml listaval (kiesett / csodbe ment papirokkal)")
    p.add_argument("--holdout", action="store_true", help="a ZAROLT vegso teszt-idoszak megnyitasa (naplozva)")
    p.set_defaults(func=cmd_meanrev_research)

    p = subparsers.add_parser("backtest-symbol", help="egy papir / ETF teljes tortenete, egy strategiaval")
    p.add_argument("symbol")
    p.add_argument("--start", default="2000-01-01", help="kezdodatum (alap: 2000-01-01)")
    p.add_argument("--end", help="vege (alap: a zarolt holdout elott)")
    p.add_argument("--strategy", default="pullback", help="pullback | deep | etf | sandbox")
    p.add_argument("--set", action="append", metavar="KULCS=ERTEK", help="parameter-feluliras (tobbszor is)")
    p.add_argument("--cost-bps", type=float, help="koltseg bps / oldal (alap: a strategiae)")
    p.add_argument("--alloc", type=float, default=100.0, help="a toke hany %%-a megy egy kotesbe (alap: 100)")
    p.add_argument("--include-holdout", action="store_true", help="a zarolt holdout is (naplozva)")
    p.add_argument("--fetch", action="store_true", help="adat frissitese futtatas elott")
    p.add_argument("--json", help="a teljes eredmeny mentese ide")
    p.set_defaults(func=cmd_backtest_symbol)

    p = subparsers.add_parser("levels", help="support/rezisztencia szintek egy papirra")
    p.add_argument("--symbol", required=True)
    p.set_defaults(func=cmd_levels)

    p = subparsers.add_parser("backtest", help="historikus kalibracio")
    p.add_argument("--symbols", **common)
    p.add_argument("--walk-forward", action="store_true", help="stabilitasvizsgalat")
    p.add_argument("--purge-cache", action="store_true", help="univerzum cache torlese a vegen")
    p.set_defaults(func=cmd_backtest)

    p = subparsers.add_parser("monitor", help="folyamatos figyeles")
    p.add_argument("--once", action="store_true", help="csak egy ciklus")
    p.add_argument("--dry-run", action="store_true", help="ne kuldjon ki riasztast")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_monitor)

    p = subparsers.add_parser("data-usage", help="lemezhasznalat kimutatas")
    p.set_defaults(func=cmd_data_usage)

    p = subparsers.add_parser("housekeeping", help="takaritas futtatasa")
    p.set_defaults(func=cmd_housekeeping)

    p = subparsers.add_parser("test-alert", help="Telegram / Discord kapcsolat es probauzenet")
    p.add_argument("--symbols", **common)
    p.set_defaults(func=cmd_test_alert)

    p = subparsers.add_parser("lab", help="strategia-labor: folyamatos strategiatesztelas multbeli adatokon")
    p.add_argument("action", nargs="?", choices=["run", "status"], default="run")
    p.add_argument("--once", action="store_true", help="egy teljes kor, utana kilep")
    p.add_argument("--workers", type=int, help="parhuzamos szalak a kitoresek gyujtesehez (alap: 1)")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_lab)

    p = subparsers.add_parser("heartbeat", help="eletjel / allapotjelentes most (Discord)")
    p.add_argument("--print", action="store_true", help="csak kiirja, nem kuldi el")
    p.set_defaults(func=cmd_heartbeat)

    p = subparsers.add_parser("discord", help="Discord ertesites beallitasa (webhook)")
    p.add_argument("--symbols", **common)
    p.add_argument("--disable", action="store_true", help="Discord webhook torlese")
    p.set_defaults(func=cmd_discord)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
