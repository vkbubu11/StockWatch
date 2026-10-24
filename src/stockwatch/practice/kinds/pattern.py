"""„Melyik alakzat ez?” (#51): szintetikus helyzet, amelybe a generator (#38) ismert alakzatot epitett - a jo valasz
biztosan ismert. Negy valasz: a beepitett alakzat es harom azonos iranyu (hasonlo felepitesu) masik; a sorrend a
seedbol, a jo valasz helye egyenletes eloszlasu. A dontes pontja az utolso sablon-pivot utan AFTER gyertyaval.

Felfedes: a trigger (az alakzat savjanak szele - nyakvonal, teto vagy alj) es a mert cel (az alakzat magassaga),
az alakzat ismervei, es mi tortent utana (a generator szandekosan vegyes kimenetet ad).

shortcut: csak szintetikus adaton (a nyilvanos keszlet szintetikus); valodi adaton a felismero kuszob feletti
talalata kellene, ha a keszlet forrasa "real".
"""
from __future__ import annotations

from ...config import Settings
from .. import sampler, scoring, synthetic
from .common import line, norm

NAME = "pattern"
AFTER = 2
LABELS = {"Dupla alj": "Dupla alj", "Tripla alj": "Tripla alj", "Forditott fej-vall": "Fordított fej-váll",
          "Bull zaszlo": "Bull zászló", "Emelkedo haromszog": "Emelkedő háromszög", "Csesze-ful": "Csésze-fül",
          "Dupla teto": "Dupla tető", "Tripla teto": "Tripla tető", "Fej-vall": "Fej-váll", "Bear zaszlo": "Bear zászló",
          "Csokkeno haromszog": "Csökkenő háromszög"}
INFO = {"Dupla alj": "pi_bottom2", "Tripla alj": "pi_bottom3", "Forditott fej-vall": "pi_ihs", "Bull zaszlo": "pi_flag_bull",
        "Emelkedo haromszog": "pi_asc", "Csesze-ful": "pi_cup", "Dupla teto": "pi_top2", "Tripla teto": "pi_top3",
        "Fej-vall": "pi_hs", "Bear zaszlo": "pi_flag_bear", "Csokkeno haromszog": "pi_desc"}


def make(pick: dict, rng, settings: Settings) -> dict:
    names = synthetic.symbols(settings.practice.synthetic_symbols)
    for _ in range(500):
        symbol = names[int(rng.integers(len(names)))]
        df, embedded = synthetic.generate(symbol)
        ok = [e for e in embedded if e[3] + AFTER >= sampler.WARMUP_BARS and e[3] + AFTER + sampler.FUTURE_BARS < len(df)]
        if not ok:
            continue
        name, direction, start, pivot, success = ok[int(rng.integers(len(ok)))]
        i = pivot + AFTER
        same = sorted(n for n in synthetic.TEMPLATES if synthetic.DIRECTION[n] == direction and n != name)
        options = [name] + [same[j] for j in rng.permutation(len(same))[:3]]
        options = [options[j] for j in rng.permutation(len(options))]
        return {"symbol": symbol, "date": f"{df.index[i]:%Y-%m-%d}", "category": "pattern", "source": "quiz",
                "breakout": None,
                "params": {"prompt": {"options": [{"key": n, "label": LABELS[n]} for n in options]},
                           "secret": {"answer": name, "start": int(start), "pivot": int(pivot), "success": bool(success)}}}
    raise ValueError("Nincs alkalmas szintetikus alakzat.")


def validate(body: dict, prompt: dict) -> dict:
    choice = body.get("choice")
    if choice not in {o["key"] for o in prompt["options"]}:
        raise ValueError("Válassz egy alakzatot.")
    return {"choice": choice}


def evaluate(task: dict, df, i: int, answer: dict, settings: Settings) -> dict:
    scale, _ = norm(df, i)
    secret = task["params"]["secret"]
    name, choice = secret["answer"], answer["choice"]
    right = choice == name
    span = df.iloc[secret["start"]: secret["pivot"] + 1]
    hi, lo = float(span["high"].max()) * scale, float(span["low"].min()) * scale
    bullish = synthetic.DIRECTION[name] == "bullish"
    trigger = round(hi if bullish else lo, 2)
    target = round(trigger + (hi - lo) if bullish else trigger - (hi - lo), 2)
    lines = [line("p_right", LABELS[name]) if right else line("p_wrong", LABELS[name], LABELS[choice]),
             line(INFO[name]), line("p_levels", trigger, target),
             line("p_after_ok" if secret["success"] else "p_after_fail"),
             line("q_quality", 100 if right else 0)]
    marks = [{"kind": "p_start", "k": secret["start"] - i, "v": round(float(df["close"].iloc[secret["start"]]) * scale, 2)},
             {"kind": "p_end", "k": secret["pivot"] - i, "v": round(float(df["close"].iloc[secret["pivot"]]) * scale, 2)}]
    return {"quality": 1.0 if right else 0.0, "lines": lines, "answer": name, "answer_label": LABELS[name],
            "levels": [{"kind": "trigger", "v": trigger}, {"kind": "p_target", "v": target}], "marks": marks,
            "future": scoring.future_bars(df, i, scale), "success": secret["success"]}
