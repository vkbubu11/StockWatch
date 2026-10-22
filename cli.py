#!/usr/bin/env python
"""StockWatch oktatasi kiadas - parancssori felulet.

    python cli.py fetch-universe                 a gyakorlo univerzum arfolyamainak letoltese (config/universe.yaml)
    python cli.py practice-pool [--force]        a Gyakorlas feladatkeszletenek felepitese
    python cli.py web [--port 8765]              webes felulet a bongeszoben
    python cli.py db migrate                     az adatbazis semajanak letrehozasa / frissitese (DATABASE_URL)
    python cli.py data-usage                     lemezhasznalat kimutatas
    python cli.py housekeeping                   takaritas
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from stockwatch import feed, housekeeping  # noqa: E402
from stockwatch.config import ensure_dirs, load_settings  # noqa: E402

DISCLAIMER = "\nOktatasi celu gyakorlas multbeli adaton - NEM befektetesi tanacs.\n"


def cmd_web(args) -> int:
    from stockwatch import webapp

    settings = load_settings()
    housekeeping.setup_logging(settings)
    ensure_dirs()
    webapp.serve(host=args.host, port=args.port, open_browser=not args.no_browser,
                 behind_proxy=args.behind_proxy)
    return 0


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
    print("Kovetkezo lepes: python cli.py practice-pool")
    return 0


def cmd_practice_pool(args) -> int:
    """Gyakorlo mod: a feladatkeszlet (rejtett multbeli helyzetek) felepitese az univerzum-cache-bol."""
    from stockwatch.practice import sampler

    settings = load_settings()
    housekeeping.setup_logging(settings, verbose=args.verbose)
    pool = sampler.build(settings, progress=lambda msg: print(f"  {msg}", flush=True), force=args.force,
                         max_seconds=args.max_minutes * 60 if args.max_minutes else None)
    if pool.get("unchanged"):
        print("Valtozatlan: a keszlet mar erre az adatra keszult (--force: ujraepites).")
    counts = pool["counts"]
    print(f"Feladatkeszlet: {pool['symbols']} papir, strategia-jelzes {counts['strategy']}, formalodo alakzat "
          f"{counts['pattern']}, semmi {counts['nothing']}" + (" (RESZLEGES: elfogyott az idokeret)" if pool.get("partial") else ""))
    return 0


def cmd_db(args) -> int:
    """Adatbazis-migraciok (migrations/*.sql) - a kapcsolat a DATABASE_URL titokbol."""
    from stockwatch import db

    housekeeping.setup_logging(load_settings())
    try:
        applied = db.migrate()
    except db.DatabaseUnavailable as exc:
        print(exc)
        return 1
    finally:
        db.close()
    print(f"Lefutott: {', '.join(applied)}" if applied else "Nincs uj migracio - a sema naprakesz.")
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockWatch - oktatasi kiadas (Gyakorlas)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=DISCLAIMER,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("fetch-universe", help="a gyakorlo univerzum letoltese (config/universe.yaml)")
    p.set_defaults(func=cmd_fetch_universe)

    p = subparsers.add_parser("practice-pool", help="gyakorlo mod: a feladatkeszlet felepitese")
    p.add_argument("--force", action="store_true", help="akkor is ujraepiti, ha az adat nem valtozott")
    p.add_argument("--max-minutes", type=float, help="idokeret; utana reszleges keszlet")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_practice_pool)

    p = subparsers.add_parser("web", help="webes felulet (http://127.0.0.1:8765)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="ne nyissa meg automatikusan a bongeszot")
    p.add_argument("--host", default="127.0.0.1",
                   help="figyelt cim (alap: 127.0.0.1)")
    p.add_argument("--behind-proxy", action="store_true",
                   help="HTTPS reverse proxy mogott fut (szerver): X-Forwarded-* fejlecek")
    p.set_defaults(func=cmd_web)

    p = subparsers.add_parser("db", help="adatbazis: migraciok futtatasa")
    p.add_argument("action", choices=["migrate"])
    p.set_defaults(func=cmd_db)

    p = subparsers.add_parser("data-usage", help="lemezhasznalat kimutatas")
    p.set_defaults(func=cmd_data_usage)

    p = subparsers.add_parser("housekeeping", help="takaritas futtatasa")
    p.set_defaults(func=cmd_housekeeping)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
