"""„Tedd le a stopot” (#50): adott a helyzet es a belepes iranya (a kovetkezo nyitason); a felhasznalo a stopot
teszi le. Ertekeles a minoseg szerint (a kimenettol fuggetlenul):

  * tavolsag ATR-ben: TIGHT alatt szoros, WIDE felett tag (a ketto kozott teljes pont)
  * a legkozelebbi szerkezeti szint: a legutobbi MEGEROSITETT swing melypont (long) / csucs (short) a dontes
    napjaig ismert adatbol (structure.find_zigzag_pivots a dontes napjaig vagott adaton); a stop mogotte jo, elotte
    gyenge, nagyon messze mogotte kozepes
  * mi tortent volna: a kotes a te stopoddal es a referencia-stoppal (a szint mogott REF_BEHIND ATR-rel; szint
    nelkul REF_DIST ATR) - ugyanazzal a motorral, mint a dontesi feladat; R-ben mindketto
"""
from __future__ import annotations

from pydantic import ValidationError

from ... import meanrev, structure
from ...config import Settings
from .. import scoring
from ..templates import TEMPLATES
from .common import line, norm

NAME = "stop"
DAYS = 10
TIGHT, WIDE = 1.0, 4.0                 # ATR: a jo stop-tavolsag sava
REF_BEHIND = 0.5                       # a referencia-stop a szerkezeti szint mogott ennyi ATR-rel
REF_DIST = 2.0                         # szerkezeti szint nelkul a referencia ennyi ATR
MAX_STRUCT_ATR = 6.0                   # ennel messzebbi swing pont mar nem "kozeli" szint
FAR_ATR = 1.5                          # a szint mogott ennel messzebb: tobb kockazat, mint szukseges
WINDOW = 400                           # a pivot-kereses ablaka (gyertya) a dontes napjaig


def make(pick: dict, rng, settings: Settings) -> dict:
    side = "short" if pick["category"] == "short" else "long"
    return pick | {"params": {"prompt": {"side": side, "days": DAYS}, "secret": {}}}


def validate(body: dict, prompt: dict) -> dict:
    from ..session import Decision                                      # a dontesi feladat szabalyai es szovegei

    try:
        d = Decision(action=prompt["side"], entry="open", stop=body.get("stop"), days=DAYS)
    except ValidationError as exc:
        raise ValueError(str(exc.errors()[0]["msg"]).removeprefix("Value error, ")) from None
    return {"stop": round(float(d.stop), 2)}


def structural_level(df, i: int, side: str, scale: float, atr: float) -> dict | None:
    """A legutobbi megerositett swing melypont (long, a belepo alatt) / csucs (short, felette) - csak a dontes
    napjaig ismert adatbol. {"price" (normalizalt), "k" (gyertya a dontes napjahoz kepest)} vagy None."""
    start = max(0, i + 1 - WINDOW)
    pivots = structure.confirmed_only(structure.find_zigzag_pivots(df.iloc[start: i + 1]))
    kind = "L" if side == "long" else "H"
    for p in reversed(pivots):
        price = p.price * scale
        if p.kind != kind or (price >= 100 if side == "long" else price <= 100):
            continue
        if abs(100 - price) / atr > MAX_STRUCT_ATR:
            return None
        return {"price": round(price, 2), "k": start + p.index - i}
    return None


def _distance_score(dist: float) -> float:
    if dist < TIGHT:
        return max(dist, 0.0) / TIGHT
    if dist > WIDE:
        return max(0.0, 1 - (dist - WIDE) / WIDE)
    return 1.0


def _outcome(label_key: str, tv: dict) -> dict:
    label = TEMPLATES[label_key]
    if tv["status"] != "closed" or tv["r"] is None:
        return line("s_out_open", label)
    if tv["reason"] in ("stop", "gap_stop"):
        return line("s_out_stop", label, int(tv["exit_k"]), float(tv["r"]))
    return line("s_out_time", label, DAYS, float(tv["r"]))


def evaluate(task: dict, df, i: int, answer: dict, settings: Settings) -> dict:
    scale, atr = norm(df, i)
    side = task["params"]["prompt"]["side"]
    sign = 1.0 if side == "long" else -1.0
    stop = answer["stop"]
    dist = sign * (100 - stop) / atr
    level = structural_level(df, i, side, scale, atr)
    ref = round(level["price"] - sign * REF_BEHIND * atr if level else 100 - sign * REF_DIST * atr, 2)

    d_score = _distance_score(dist)
    lines = [line(f"s_{side}"), line("s_dist", stop, 100.0, dist)]
    if dist < TIGHT:
        lines.append(line("s_tight", TIGHT))
    elif dist > WIDE:
        lines.append(line("s_wide", WIDE))
    else:
        lines.append(line("s_ok", TIGHT, WIDE))
    if level:
        lines.append(line("s_struct_low" if side == "long" else "s_struct_high", -int(level["k"]), level["price"]))
        beyond = sign * (level["price"] - stop) / atr                  # pozitiv: a stop a szint mogott van
        if beyond < 0:
            s_score = 0.3
            lines.append(line("s_front"))
        elif beyond > FAR_ATR:
            s_score = 0.7
            lines.append(line("s_far", beyond))
        else:
            s_score = 1.0
            lines.append(line("s_behind"))
        lines.append(line("s_ref_struct", ref, sign * (100 - ref) / atr))
    else:
        s_score = 1.0
        lines += [line("s_struct_none"), line("s_ref_dist", ref, sign * (100 - ref) / atr)]
    quality = round(0.5 * d_score + 0.5 * s_score, 2)

    feat = meanrev.features(df, settings.strategies.pullback.params)
    mirrored = scoring.mirror(feat)
    cost, borrow = scoring.cost_bps(settings), scoring.borrow_bps(settings)
    trade = {"action": side, "entry": "open", "limit": None, "target": None, "days": DAYS}
    mine, _ = scoring.user_trade(feat, i, trade | {"stop": stop}, scale, cost, mirrored, borrow, atr)
    reference, _ = scoring.user_trade(feat, i, trade | {"stop": ref}, scale, cost, mirrored, borrow, atr)
    lines += [_outcome("s_mine", mine), _outcome("s_ref", reference), line("s_size"),
              line("s_parts", int(round(d_score * 100)), int(round(s_score * 100))),
              line("q_quality", int(round(quality * 100)))]
    marks = [{"kind": "entry", "k": mine["entry_k"], "v": mine["entry"]}] if mine["entry_k"] is not None else []
    if mine["exit_k"] is not None:
        marks.append({"kind": "exit", "k": mine["exit_k"], "v": mine["exit"], "r": mine["r"]})
    levels = [{"kind": "stop", "v": stop}, {"kind": "ref_stop", "v": ref}]
    if level:
        levels.append({"kind": "struct", "v": level["price"]})
    return {"quality": quality, "lines": lines, "levels": levels, "marks": marks,
            "future": scoring.future_bars(df, i, scale), "user": mine, "reference_trade": reference,
            "structure": level, "stop_atr": round(dist, 3), "atr": round(atr, 3),
            "parts": {"distance": round(d_score, 2), "structure": round(s_score, 2)}}
