"""A kutatougynok jelentese: JSON (a felulet es a tesztek) + Markdown (CLI, archivum).

A `runtime` kulcs alatt csak a futasonkent valtozo adat van (CPU-ido, idopontok); minden mas ugyanarra a
briefre, seedre, adatra es kodverziora bitre azonos (determinizmus-teszt).
"""
from __future__ import annotations

from .. import ledger
from ..entry_research import HOLDOUT_START, SPLIT_DATE
from . import explain
from .brief import ENGINE_LABELS, UNIVERSE_LABELS, Brief
from .search import TRAIN_END, VISIBLE_END
from .space import FIELD_LABELS, Space

METRIC_KEYS = ("n", "per_week", "win", "win_lo", "ev", "ev_lo", "pf", "max_dd", "held", "total")


def _clean_metrics(m: dict | None) -> dict | None:
    return {k: m.get(k) for k in METRIC_KEYS} if m else None


def adopt_payload(space: Space, assignment: dict) -> dict:
    """Ami a Homokozoba atveheto: Visszahuzasnal a teljes PullbackParams, Kitoresnel a lab.EDITABLE mezok."""
    if space.engine == "breakout":
        from .. import lab

        values = space.full(assignment)
        return {"engine": "breakout", "params": {k: values[k] for k in lab.EDITABLE if k in values}}
    params = space.params(assignment).model_dump()
    params.pop("commission_bps", None)                 # a kotesi dij a beallitasokbol jon
    return {"engine": "pullback", "params": params}


def build(brief: Brief, space: Space, engine, search, result: dict, *, symbols: int, looks: int,
          runtime: dict) -> dict:
    finalists = []
    for f in result["finalists"]:
        item = f["item"]
        visible = f.get("visible") or {}
        costly = f.get("cost_2x") or {}
        entry = {
            "rank": f["rank"], "key": f["key"], "changes": item["changes"],
            "assignment": item["assignment"], "adopt": adopt_payload(space, item["assignment"]),
            "assess": f["assess"], "checked": bool(f.get("checked")),
            "train_search": _clean_metrics(f.get("train_search") or f.get("train")),
            "train": _clean_metrics(visible.get("train")), "test": _clean_metrics(visible.get("test")),
            "visible": _clean_metrics(visible.get("visible")),
            "years": {str(k): v for k, v in (visible.get("years") or {}).items()},
            "equity": visible.get("equity") or [],
            "cost_2x": {"visible": _clean_metrics(costly.get("visible"))} if costly else None,
            "neighbourhood": f.get("neighbourhood"), "ablation": f.get("ablation"), "gate": f.get("gate"),
        }
        entry["explanation"] = [ln.to_dict() for ln in explain.finalist_lines(entry, engine.unit)]
        finalists.append(entry)
    report = {
        "version": 1,
        "brief": brief.model_dump(), "brief_id": brief.id, "brief_fingerprint": brief.fingerprint(),
        "engine": brief.engine, "engine_label": ENGINE_LABELS[brief.engine], "unit": engine.unit,
        "universe": brief.universe, "universe_label": UNIVERSE_LABELS[brief.universe], "symbols": symbols,
        "data_fingerprint": engine.data_fingerprint, "code_version": ledger.code_version(),
        "periods": {"split": f"{SPLIT_DATE:%Y-%m-%d}", "train_end": f"{TRAIN_END:%Y-%m-%d}",
                    "visible_end": f"{VISIBLE_END:%Y-%m-%d}", "holdout_start": f"{HOLDOUT_START:%Y-%m-%d}"},
        "space": {"size": space.size, "dims": {d.name: list(d.values) for d in space.dims}, "fixed": space.fixed},
        "tried": {"total": search.tried, "rejected": search.rejected, "valid": search.tried - search.rejected,
                  "full": sum(1 for k in search.cache if k[1] == 1.0 and k[2] == "train" and k[3] == 1.0),
                  "robustness": search.extra_evals},
        "stopped": search.stopped, "skipped": search.skipped,
        "preset": {"train": _clean_metrics((result.get("preset") or {}).get("train"))},
        "finalists": finalists, "validation_looks": looks,
        "runtime": runtime,
    }
    report["summary"] = [ln.to_dict() for ln in explain.summary_lines(report)]
    report["warnings"] = [ln.to_dict() for ln in explain.warning_lines(report)]
    report["disclaimer"] = explain.Line("disclaimer").to_dict()
    return report


def _fmt(v, kind: str = "num", unit: str = "%") -> str:
    if v is None:
        return "–"
    if kind == "rate":
        return f"{v * 100:.1f}%"
    if kind == "ev":
        return f"{v:+.2f}{'%' if unit == '%' else ' R'}"
    if kind == "int":
        return f"{int(v)}"
    return f"{v:.2f}"


def _metrics_row(label: str, m: dict | None, unit: str) -> str:
    if not m:
        return f"| {label} | – | – | – | – | – | – | – |"
    return (f"| {label} | {_fmt(m['n'], 'int')} | {_fmt(m['per_week'])} | {_fmt(m['win'], 'rate')} "
            f"({_fmt(m['win_lo'], 'rate')}) | {_fmt(m['ev'], 'ev', unit)} ({_fmt(m['ev_lo'], 'ev', unit)}) "
            f"| {_fmt(m['pf'])} | {_fmt(m['max_dd'])}% | {_fmt(m['held'])} |")


def markdown(report: dict) -> str:
    unit = report["unit"]
    b = report["brief"]
    lines = [f"# Kutatóügynök – {b['name']}", "",
             f"Motor: {report['engine_label']} · Univerzum: {report['universe_label']} ({report['symbols']} papír) · "
             f"Költség: {b['objective']['cost_bps']:g} bps / oldal · Seed: {b['search']['seed']} · "
             f"Módszer: {b['search']['method']}", ""]
    lines += [ln["text"] for ln in report["summary"]] + [""]
    if report["warnings"]:
        lines += [f"> {ln['text']}" for ln in report["warnings"]] + [""]
    header = ("| Időszak | Kötés | Heti | Nyerési arány (alsó határ) | Átlag (alsó határ) | PF | Max. visszaesés | "
              "Tartás (nap) |\n|---|---|---|---|---|---|---|---|")
    lines += ["## Kiinduló beállítás (tanító időszak)", "", header,
              _metrics_row("Tanító", report["preset"]["train"], unit), ""]
    for f in report["finalists"]:
        lines += [f"## {f['rank']}. jelölt", ""]
        if f["changes"]:
            lines += ["Eltérés a kiinduló beállítástól: " + ", ".join(
                f"{FIELD_LABELS.get(k, k)}: {explain.value_text(old)} → {explain.value_text(new)}"
                for k, (old, new) in f["changes"].items()), ""]
        lines += [header, _metrics_row("Tanító", f["train"] or f["train_search"], unit),
                  _metrics_row("Validáció", f["test"], unit),
                  _metrics_row("Holdout előtt összesen", f["visible"], unit), ""]
        lines += [f"- {ln['text']}" for ln in f["explanation"]] + [""]
        if f.get("gate"):
            lines += ["Elfogadási feltétel:", ""] + [f"- [{'x' if ok else ' '}] {name}" for name, ok in f["gate"]["checks"]]
            lines.append("")
    lines += [f"*{report['disclaimer']['text']}*", ""]
    return "\n".join(lines)
