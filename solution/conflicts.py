"""
Contradictions the website contains, with the value we believe and why.

1. Rate rows: the same row (same product, term and tier) showing different values
   - on different pages of the latest snapshot (a stale calculator / promo / article copy), or
   - on the authoritative page in different snapshots (the rate changed; old copies linger).
   Resolution = the value our answer rule uses today (rule D: newest effective date, then page kind).
2. Repeated "fact sentences" in articles whose number drifts ("... and 23 branch office locations",
   "the fourth / fifth time Forbes recognized Dupaco"). Resolution = the newest article.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(next(d / "harness" for d in Path(__file__).resolve().parents if (d / "harness" / "contract.py").exists())))
from contract import Conflict, Source  # noqa: E402

import kb  # noqa: E402
import rates  # noqa: E402


def _topic(r) -> str:
    bits = [r.display]
    if r.terms:
        bits.append(", ".join(f"{t}-month" for t in sorted(r.terms)))
    metric = "APY" if any(m == "apy" for m, _ in r.values) else "APR" if any(m == "apr" for m, _ in r.values) else "rate"
    return f"{' '.join(bits)} {metric}"


def _vals(r) -> list:
    return [f"{v} {rates.METRIC_LABEL.get(m, m)}".strip() for m, v in r.values]


def _primary(r) -> str:
    for want in ("apy", "apr", "rate", "dividend"):
        for m, v in r.values:
            if m == want:
                return v
    return r.values[0][1]


def rate_conflicts(snaps) -> list:
    if not snaps:
        return []
    latest = snaps[-1]
    # current winner per row key (rule D)
    groups = defaultdict(list)
    for r in latest.rate_rows:
        groups[r.key].append(r)
    winners = {k: sorted(rs, key=lambda r: rates._table_rank(r.table))[0] for k, rs in groups.items()}
    out = []
    for k, win in winners.items():
        # skip rows whose product family is ambiguous (no concepts at all)
        if not win.concepts - {"business"}:
            continue
        seen = {}                                 # normalised primary value -> (row, where)
        for r in groups[k]:
            seen.setdefault(rates.norm_value(_primary(r)), (r, f"{kb.KIND_NAMES[r.page.kind]} {r.page.path} ({latest.snapshot_id})"))
        # history on the winning page across snapshots
        for s in snaps[:-1]:
            for r in s.rate_rows:
                if r.key == k and r.page.path == win.page.path:
                    seen.setdefault(rates.norm_value(_primary(r)), (r, f"{r.page.path} in snapshot {s.snapshot_id}"))
        if len(seen) < 2:
            continue
        cur_n, cur = rates.norm_value(_primary(win)), _primary(win)
        values, sources = [], []
        for v, (r, where) in sorted(seen.items(), key=lambda kv: kv[0] != cur_n):
            values.append(_primary(r))
            sources.append(Source(url=r.page.url, snapshot_id=r.page.snapshot_id, quote=f"{r.display}: {r.value_str()}"))
        stale = [w for v, (r, w) in seen.items() if v != cur_n]
        reason = (f"{cur} is on the {kb.KIND_NAMES[win.page.kind]} {win.page.path}"
                  + (f" with the newest effective date ({win.effective.strftime('%m/%d/%Y')})" if win.effective else "")
                  + f" in the latest snapshot; other values come from {', '.join(stale[:3])}"
                  + (" and more" if len(stale) > 3 else "") + " (older or less authoritative).")
        out.append(Conflict(topic=_topic(win), values=values, sources=sources, resolution=cur, reason=reason))
    return out


# ------------------------------------------------------------------------------------------
ORD = "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
FACT_PATTERNS = [
    # (topic, regex with one value group)
    ("Number of Dupaco branch office locations", re.compile(r"\b(\d{1,3}) branch office locations", re.I)),
    ("Number of times Forbes recognized Dupaco", re.compile(r"\b(" + ORD + r") time Forbes recognized Dupaco", re.I)),
]


def fact_drift_conflicts(snaps) -> list:
    if not snaps:
        return []
    latest = snaps[-1]
    out = []
    for topic, rx in FACT_PATTERNS:
        found = {}       # value -> (freshness, page)
        for p in latest.pages:
            for psg in p.passages:
                for m in rx.finditer(psg.text):
                    v = m.group(1).lower()
                    if v not in found or p.freshness > found[v][0]:
                        found[v] = (p.freshness, p)
        if len(found) < 2:
            continue
        newest = max(found.items(), key=lambda kv: kv[1][0])
        values = [newest[0]] + sorted(v for v in found if v != newest[0])
        sources = [Source(url=found[v][1].url, snapshot_id=latest.snapshot_id) for v in values]
        reason = (f"newest article ({newest[1][0].isoformat()}, {newest[1][1].path}) says {newest[0]}; "
                  "older articles repeat earlier figures")
        vals = [f"{v} branch" if "branch" in topic else v for v in values]
        out.append(Conflict(topic=topic, values=vals, sources=sources, resolution=vals[0], reason=reason))
    return out


def find_conflicts(snaps) -> list:
    return rate_conflicts(snaps) + fact_drift_conflicts(snaps)
