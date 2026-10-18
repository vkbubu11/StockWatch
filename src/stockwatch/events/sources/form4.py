"""SEC Form 4 - bennfentes (insider) tranzakciok.

Ket forras, ugyanabba a tablaba:
  * MULT: a SEC negyedeves "Insider Transactions Data Sets" csomagjai (egy ZIP / negyedev, benne
    SUBMISSION.tsv, REPORTINGOWNER.tsv, NONDERIV_TRANS.tsv). Itt csak a beadas DATUMA ismert, ezert az
    esemeny a KOVETKEZO kereskedesi nap nyitasatol szamit ismertnek (konzervativ, nincs jovobe latas).
  * ELO: a papir EDGAR-beadvanylistaja (submissions JSON) + a Form 4 XML. Itt az elfogadas idopontja is
    megvan (edgar.accepted_utc, a ket ertelmezes kozul a kesobbi).

Csak a nem szarmazekos (non-derivative) tablazat sorai kellenek: a tranzakciokod P (nyilt piaci vetel) es
S (nyilt piaci eladas) a lenyeges; a tobbi kod (opcio-lehivas, ajandek, adovisszatartas...) is tarolodik,
de az esemenyek nem hasznaljak. A 10b5-1 terv szerinti (elore utemezett) kotes jelolve van.

A SEC kerese: azonosito User-Agent (SEC_USER_AGENT a .env-ben) es legfeljebb 10 keres / mp - a kozos
korlatot a sentiment.sources.edgar.throttle tartja (minden folyamatbol egyutt <= 8 / mp).

Tarolas: data/events/form4/<ev>.csv.gz (csak a kivonat), osszesen legfeljebb LIMIT_MB; a legregebbi ev
torlodik eloszor.
"""
from __future__ import annotations

import gzip
import io
import logging
import zipfile
from datetime import date, datetime, timezone
from xml.etree import ElementTree

import pandas as pd

from ...config import DATA_DIR

log = logging.getLogger(__name__)

BULK_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{year}q{quarter}_form345.zip"
FORM4_DIR = DATA_DIR / "events" / "form4"
LIMIT_MB = 50
COLUMNS = ["accession", "symbol", "issuer_cik", "form", "filed", "accepted", "owner_cik", "owner", "director",
           "officer", "ten_pct", "title", "code", "acq_disp", "shares", "price", "value", "plan_10b5_1", "source"]
FORMS = ("4", "4/A")


def _float(value) -> float | None:
    try:
        out = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def _flag(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "y", "yes")


# ---------------------------------------------------------------------------
# Elo: Form 4 XML
# ---------------------------------------------------------------------------
def _text(node, path: str) -> str:
    """Az XML-ben az ertek gyakran <x><value>...</value></x> alakban van; mindkettot kezeli."""
    if node is None:
        return ""
    found = node.find(path + "/value")
    if found is None:
        found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_xml(text: str | bytes, accession: str, filed: str, accepted: str | None = None) -> list[dict]:
    """Egy Form 4 XML (ownershipDocument) nem szarmazekos tranzakcioi."""
    root = ElementTree.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    if root.tag != "ownershipDocument":
        raise ValueError(f"nem Form 4 XML ({root.tag})")
    form = _text(root, "documentType") or "4"
    symbol = _text(root, "issuer/issuerTradingSymbol").upper()
    cik = _text(root, "issuer/issuerCik")
    owners = root.findall("reportingOwner")
    first = owners[0] if owners else None
    rel = first.find("reportingOwnerRelationship") if first is not None else None
    roles = {k: _flag(_text(rel, k)) for k in ("isDirector", "isOfficer", "isTenPercentOwner")} if rel is not None else {}
    plan = _text(root, "aff10b5One")
    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = _float(_text(tx, "transactionAmounts/transactionShares"))
        price = _float(_text(tx, "transactionAmounts/transactionPricePerShare"))
        out.append({
            "accession": accession, "symbol": symbol, "issuer_cik": cik, "form": form, "filed": filed,
            "accepted": accepted or "", "owner_cik": _text(first, "reportingOwnerId/rptOwnerCik"),
            "owner": _text(first, "reportingOwnerId/rptOwnerName"), "director": roles.get("isDirector", False),
            "officer": roles.get("isOfficer", False), "ten_pct": roles.get("isTenPercentOwner", False),
            "title": _text(rel, "officerTitle") if rel is not None else "",
            "code": _text(tx, "transactionCoding/transactionCode").upper(),
            "acq_disp": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode").upper(),
            "shares": shares, "price": price,
            "value": round(shares * price, 2) if shares is not None and price is not None else None,
            "plan_10b5_1": _flag(plan) if plan else None, "source": "xml",
        })
    return out


# ---------------------------------------------------------------------------
# Mult: negyedeves SEC-csomag (TSV-k egy ZIP-ben)
# ---------------------------------------------------------------------------
def _tsv(archive: zipfile.ZipFile, name: str) -> pd.DataFrame:
    member = next((m for m in archive.namelist() if m.upper().endswith(name.upper())), None)
    if member is None:
        raise ValueError(f"hianyzo fajl a csomagban: {name}")
    with archive.open(member) as fh:
        df = pd.read_csv(fh, sep="\t", dtype=str, keep_default_na=False, quoting=3, on_bad_lines="skip")
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([""] * len(df), index=df.index)


def _filed(value: str) -> str:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_bulk(data: bytes) -> list[dict]:
    """Egy negyedeves csomag Form 4 sorai (a tobbi urlap - 3, 5 - kimarad)."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    sub = _tsv(archive, "SUBMISSION.tsv")
    own = _tsv(archive, "REPORTINGOWNER.tsv")
    trans = _tsv(archive, "NONDERIV_TRANS.tsv")
    sub = sub[_col(sub, "DOCUMENT_TYPE").isin(FORMS)]
    subs = {r["ACCESSION_NUMBER"]: r for r in sub.to_dict("records")}
    owners: dict[str, dict] = {}
    for r in own.to_dict("records"):
        owners.setdefault(r["ACCESSION_NUMBER"], r)                # tobb bejelento: az elso neve, a szerepek unioja
        rel = str(r.get("RPTOWNER_RELATIONSHIP", "")).lower()
        o = owners[r["ACCESSION_NUMBER"]]
        o.setdefault("_roles", set()).update(x.strip() for x in rel.split(","))
    out = []
    for r in trans.to_dict("records"):
        s = subs.get(r.get("ACCESSION_NUMBER"))
        if s is None:
            continue
        o = owners.get(r["ACCESSION_NUMBER"], {})
        roles = o.get("_roles", set())
        shares, price = _float(r.get("TRANS_SHARES")), _float(r.get("TRANS_PRICEPERSHARE"))
        plan = s.get("AFF10B5ONE", "")
        out.append({
            "accession": r["ACCESSION_NUMBER"], "symbol": str(s.get("ISSUERTRADINGSYMBOL", "")).strip().upper(),
            "issuer_cik": s.get("ISSUERCIK", ""), "form": s.get("DOCUMENT_TYPE", "4"),
            "filed": _filed(s.get("FILING_DATE", "")), "accepted": "",
            "owner_cik": o.get("RPTOWNERCIK", ""), "owner": o.get("RPTOWNERNAME", ""),
            "director": "director" in roles, "officer": "officer" in roles,
            "ten_pct": any("10" in x or "tenpercent" in x.replace(" ", "") for x in roles), "title": o.get("RPTOWNER_TITLE", ""),
            "code": str(r.get("TRANS_CODE", "")).strip().upper(),
            "acq_disp": str(r.get("TRANS_ACQUIRED_DISP_CD", "")).strip().upper(),
            "shares": shares, "price": price,
            "value": round(shares * price, 2) if shares is not None and price is not None else None,
            "plan_10b5_1": _flag(plan) if plan else None, "source": "bulk",
        })
    return [r for r in out if r["symbol"] and r["filed"]]


def quarters(since: date, until: date | None = None) -> list[tuple[int, int]]:
    until = until or date.today()
    out, y, q = [], since.year, (since.month - 1) // 3 + 1
    while (y, q) <= (until.year, (until.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch_quarter(year: int, quarter: int, session=None) -> list[dict]:
    from ...sentiment.sources import edgar

    return parse_bulk(edgar.get_bytes(BULK_URL.format(year=year, quarter=quarter), session, timeout=120))


# ---------------------------------------------------------------------------
# Elo frissites: EDGAR beadvanylista + XML
# ---------------------------------------------------------------------------
def refresh_live(symbols: list[str], since: pd.Timestamp, session=None, max_filings: int = 200) -> int:
    """A papirok uj Form 4 beadasai (elfogadasi idoponttal). Papironkent 1 + uj beadasonkent 1 keres."""
    from ...sentiment.sources import edgar

    known = set(load(symbols=symbols)["accession"]) if symbols else set()
    tickers = edgar.ticker_map(session)
    rows = []
    for symbol in symbols:
        info = tickers.get(symbol.upper())
        if not info:
            continue
        try:
            recent = edgar._get(edgar.SUBMISSIONS_URL.format(name=f"CIK{info['cik']:010d}.json"), session)
        except Exception as exc:
            log.warning("Form 4: %s beadvanylistaja nem toltheto le: %s", symbol, exc)
            continue
        new = [r for r in edgar._rows(recent["filings"]["recent"]) if r["form"] in FORMS
               and pd.Timestamp(r["filingDate"]) >= since and r["accessionNumber"] not in known][:max_filings]
        for r in new:
            doc = (r.get("primaryDocument") or "").split("/")[-1]       # az xsl-mappa nelkul: a nyers XML
            url = f"https://www.sec.gov/Archives/edgar/data/{info['cik']}/{r['accessionNumber'].replace('-', '')}/{doc}"
            try:
                accepted = edgar.accepted_utc(r["acceptanceDateTime"], r["filingDate"]).isoformat()
                parsed = parse_xml(edgar.get_bytes(url, session), r["accessionNumber"], r["filingDate"], accepted)
                rows += [x | {"symbol": x["symbol"] or symbol.upper()} for x in parsed]
            except Exception as exc:
                log.warning("Form 4: %s %s nem dolgozhato fel: %s", symbol, r["accessionNumber"], exc)
    save(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Tarolas (evenkent, tomoritve, plafonnal)
# ---------------------------------------------------------------------------
def _path(year: int):
    return FORM4_DIR / f"{year}.csv.gz"


def _read(path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype={"accession": str, "issuer_cik": str, "owner_cik": str, "filed": str,
                                        "accepted": str, "symbol": str, "code": str, "form": str},
                           keep_default_na=False, na_values=[""])
    except (OSError, ValueError, EOFError):
        log.warning("Serult Form 4 fajl: %s", path)
        return pd.DataFrame(columns=COLUMNS)


def save(rows: list[dict]) -> int:
    """Uj sorok hozzaadasa (azonos beadas + sor: az elo XML felulirja a csomagbelit, mert ott az idopont is
    megvan). Visszaadja a tarolt sorok szamat."""
    if not rows:
        return 0
    FORM4_DIR.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(rows, columns=COLUMNS)
    total = 0
    for year, part in new.groupby(new["filed"].str[:4]):
        path = _path(int(year))
        old = _read(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
        fresh = set(zip(part["accession"], part["source"]))
        old = old[[(a, s) not in fresh for a, s in zip(old["accession"], old["source"])]]   # az uj adag lecsereli
        both = pd.concat([old, part], ignore_index=True)
        with_xml = set(both.loc[both["source"] == "xml", "accession"])       # az elo XML-nek ido is van
        both = both[(both["source"] == "xml") | ~both["accession"].isin(with_xml)]
        both = both.sort_values(["filed", "symbol", "accession"])
        buffer = io.StringIO()
        both[COLUMNS].to_csv(buffer, index=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(gzip.compress(buffer.getvalue().encode("utf-8")))
        tmp.replace(path)
        total += len(both)
    prune()
    return total


def load(symbols: list[str] | None = None, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    if not FORM4_DIR.exists():
        return pd.DataFrame(columns=COLUMNS)
    frames = []
    for path in sorted(FORM4_DIR.glob("*.csv.gz")):
        year = path.name[:4]
        if (start and year < start[:4]) or (end and year > end[:4]):
            continue
        df = _read(path)
        if symbols is not None:
            df = df[df["symbol"].isin(set(symbols))]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    for col in ("director", "officer", "ten_pct"):
        out[col] = out[col].astype(str).str.lower().isin(("true", "1"))
    return out


def usage_bytes() -> int:
    return sum(p.stat().st_size for p in FORM4_DIR.glob("*.csv.gz")) if FORM4_DIR.exists() else 0


def prune() -> list[str]:
    files = sorted(FORM4_DIR.glob("*.csv.gz")) if FORM4_DIR.exists() else []
    removed = []
    while files and sum(p.stat().st_size for p in files) > LIMIT_MB * 1024 * 1024 and len(files) > 1:
        victim = files.pop(0)
        victim.unlink(missing_ok=True)
        removed.append(victim.name)
    return removed


def coverage() -> dict:
    """Mely evekre van adat (a felulet es a tanulmany figyelmeztetesehez)."""
    years = sorted(p.name[:4] for p in FORM4_DIR.glob("*.csv.gz")) if FORM4_DIR.exists() else []
    return {"years": years, "bytes": usage_bytes(), "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}

