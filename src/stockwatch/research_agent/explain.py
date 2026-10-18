"""Sablonos indoklas es lepesnaplo-szovegek.

Minden sor: sablonkulcs + argumentumok (a szamok a szamitasbol, formazva) + a kesz magyar szoveg. A
forditas a megjeleniteskor tortenik (i18n katalogus). LLM nincs: a szoveg determinisztikus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .space import FIELD_LABELS
from .templates import TEMPLATES, VIOLATION_LABELS, WORDS

_SLOT = re.compile(r"\{(\d+)(?::n)?\}")


@dataclass
class Line:
    key: str
    args: list[str] = field(default_factory=list)
    numeric: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _SLOT.sub(lambda m: self.args[int(m.group(1))], TEMPLATES[self.key])

    @property
    def strings(self) -> list[str]:
        return [a for i, a in enumerate(self.args) if i not in self.numeric]

    @property
    def numbers(self) -> list[str]:
        return [a for i, a in enumerate(self.args) if i in self.numeric]

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.args, "numeric": self.numeric, "text": self.text}


def value_text(v) -> str:
    """Parameter-ertek szovegkent (bool / None szoval, szam felesleges tizedesek nelkul)."""
    if isinstance(v, bool) or v is None:
        return WORDS[v]
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def signed(v: float, digits: int = 1) -> str:
    return f"{v:+.{digits}f}"


def pct(v: float | None, digits: int = 1) -> str:
    return f"{(v or 0) * 100:.{digits}f}"


def summary_lines(report: dict) -> list[Line]:
    tried = report["tried"]
    out = [Line("summary", [str(tried["total"]), str(tried["rejected"]), report["periods"]["split"]], [0, 1])]
    checked = sum(1 for f in report["finalists"] if f.get("checked"))
    if checked:
        out.append(Line("validation_once", [report["periods"]["split"], report["periods"]["visible_end"],
                                            str(checked)], [2]))
    out.append(Line("holdout", [report["periods"]["holdout_start"]]))
    stopped = report.get("stopped")
    if stopped == "variants":
        out.append(Line("stopped_variants", [str(report["brief"]["search"]["max_variants"])], [0]))
    elif stopped == "cpu":
        out.append(Line("stopped_cpu", [value_text(report["brief"]["search"]["max_cpu_minutes"])], [0]))
    elif stopped == "deadline":
        out.append(Line("stopped_deadline"))
    if report.get("skipped"):
        out.append(Line("skipped", [", ".join(report["skipped"])]))
    if not report["finalists"]:
        out.append(Line("no_result"))
    elif all(f["assess"]["violations"] for f in report["finalists"]):
        out.append(Line("no_feasible"))
    return out


def warning_lines(report: dict) -> list[Line]:
    out = []
    if report["tried"]["total"] >= 50:
        out.append(Line("warn_many"))
    if report["universe"] in ("indexes", "nasdaq100", "sp500"):
        out.append(Line("warn_survivor"))
    if report.get("validation_looks", 0) > 1:
        out.append(Line("warn_looks", [str(report["validation_looks"])], [0]))
    return out


def finalist_lines(f: dict, unit: str) -> list[Line]:
    out = []
    if not f["changes"]:
        out.append(Line("preset_same"))
    ablation = {a["field"]: a for a in f.get("ablation") or []}
    neutral = []
    for name, (old, new) in f["changes"].items():
        label = FIELD_LABELS.get(name, name)
        a = ablation.get(name)
        if a and a.get("d_win_pp") is not None and a.get("d_ev") is not None:
            if abs(a["d_win_pp"]) < 0.05 and abs(a["d_ev"]) < 0.005:
                neutral.append(f"{label} {value_text(new)}")      # a valtoztatas a tanito eredmenyen semmit nem mozdit
                continue
            out.append(Line("change_pct" if unit == "%" else "change_r",
                            [label, value_text(new), value_text(old), signed(a["d_win_pp"]),
                             signed(a["d_ev"], 2)], [3, 4]))
        else:
            out.append(Line("change_plain", [label, value_text(new), value_text(old)]))
    if neutral:
        out.append(Line("change_none", [", ".join(neutral)]))
    n = f.get("neighbourhood")
    if n and n.get("n") and n.get("win_min") is not None:
        out.append(Line("neighbours", [str(n["n"]), str(n["feasible"]), pct(n["win_min"]), pct(n["win_max"])],
                        [0, 1, 2, 3]))
    elif n is not None:
        out.append(Line("neighbours_none"))
    c = (f.get("cost_2x") or {}).get("visible")
    if c and c.get("win") is not None:
        out.append(Line("cost2x_pct" if unit == "%" else "cost2x_r", [pct(c["win"]), f"{c['ev']:.2f}"], [0, 1]))
    years = f.get("years") or {}
    if years:
        positive = sum(1 for v in years.values() if (v.get("ev") or 0) > 0)
        out.append(Line("years", [str(len(years)), str(positive)], [0, 1]))
    gate = f.get("gate")
    if gate:
        ok = sum(1 for _, passed in gate["checks"] if passed)
        out.append(Line("gate", [str(ok), str(len(gate["checks"]))], [0, 1]))
    if f["assess"]["violations"]:
        out.append(Line("violations", [", ".join(VIOLATION_LABELS.get(v, v) for v in f["assess"]["violations"])]))
    return out


def _score(v) -> str:
    return "–" if v is None else f"{v:.3f}"


def log_line(rec: dict) -> Line | None:
    """Egy naplobejegyzes idovonal-sora (a reszletes fokozati sorok nem kapnak kulon sort)."""
    phase = rec.get("phase")
    if phase == "rung_summary":
        return Line("log_rung", [str(rec["rung"]), str(rec["evaluated"]), f"{rec['fraction'] * 100:.0f}",
                                 str(rec["symbols"]), str(rec["kept"])], [0, 1, 2, 3, 4])
    if phase == "refine":
        name, old, new = rec["move"]
        return Line("log_better" if rec["verdict"] == "better" else "log_worse",
                    [FIELD_LABELS.get(name, name), value_text(old), value_text(new), _score(rec.get("score_before")),
                     _score(rec.get("score"))])
    if phase == "step":
        changes = rec.get("changes") or {}
        text = ", ".join(f"{FIELD_LABELS.get(k, k)} {value_text(v[1])}" for k, v in changes.items()) or "–"
        return Line("log_step", [text])
    if rec.get("verdict") == "rejected":
        changes = rec.get("changes") or {}
        text = ", ".join(f"{FIELD_LABELS.get(k, k)} {value_text(v[1])}" for k, v in changes.items()) or "–"
        return Line("log_rejected", [text, rec.get("reason") or "-"])
    if phase == "final":
        return Line("log_validated", [str(rec["rank"])], [0])
    return None
