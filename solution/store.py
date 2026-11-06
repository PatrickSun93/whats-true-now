"""Per-snapshot index: pages, passages (+BM25), rate rows, hours blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import embed
import kb
import rates
from search import BM25, tokens

DAY = r"(?:Mon|Tues?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)[a-z]*\.?"
DAY_LINE = re.compile(r"^\s*(" + DAY + r"(?:\s*(?:[-–—]|through|to|and|&)\s*" + DAY + r")?)\s*:?\s+(.*\S)\s*$", re.I)
HOURS_MARK = re.compile(r"^\W*(lobby|drive[- ]?(?:up|thru|through))\W*\s*(.*)$", re.I)


@dataclass
class HoursBlock:
    kind: str                # "lobby" | "drive-up"
    schedule: tuple          # ((days, times), ...)
    context: str             # text just before the block (names the branch)
    page: kb.PageRec

    def render(self) -> str:
        return "; ".join(f"{d} {t}" for d, t in self.schedule)


def _norm_sched(s: str) -> str:
    s = s.replace("–", "-").replace("—", "-").replace(" - ", "-")
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith(".") and not s.endswith("m."):
        s = s[:-1]
    return s


def parse_hours(page: kb.PageRec, body: str) -> list:
    lines = [kb.clean_inline(l) for l in body.split("\n")]
    lines = [l for l in lines if l.strip()]
    blocks = []
    i = 0
    while i < len(lines):
        m = HOURS_MARK.match(lines[i])
        if m and len(lines[i]) < 140:
            kind = "lobby" if m.group(1).lower().startswith("lobby") else "drive-up"
            sched = []
            rest = m.group(2).strip()
            j = i + 1
            if rest:
                dm = DAY_LINE.match(rest)
                if dm:
                    sched.append((dm.group(1), _norm_sched(dm.group(2))))
            while j < len(lines):
                dm = DAY_LINE.match(lines[j])
                if not dm or len(lines[j]) > 80:
                    break
                sched.append((dm.group(1).strip(), _norm_sched(dm.group(2))))
                j += 1
            if sched and any(re.search(r"\d", t) for _, t in sched):
                ctx = " ".join(lines[max(0, i - 6):i])[-600:]
                blocks.append(HoursBlock(kind, tuple(sched), page.title + " — " + ctx, page))
            i = max(j, i + 1)
            continue
        i += 1
    return blocks


class SnapshotIndex:
    def __init__(self, snapshot_id: str, taken_on: date):
        self.snapshot_id, self.taken_on = snapshot_id, taken_on
        self.pages = []
        self.passages = []
        self.rate_rows = []
        self.prose_rows = []     # rates written in sentences, used only when no table row fits
        self.hours = []
        self.bm25 = None
        self.page_bm25 = None
        self.dense_rows = None   # optional embeddings (embed.py)

    def finalize(self):
        for p in self.pages:
            self.passages.extend(p.passages)
            for t in p.tables:
                self.rate_rows.extend(rates.rows_from_table(t))
            if p.kind != kb.ARTICLE:
                for psg in p.passages:
                    self.prose_rows.extend(rates.rows_from_passage(psg))
        self.bm25 = BM25([tokens(p.heading + " " + p.text) for p in self.passages])
        if embed.enabled():
            self.dense_rows = embed.index(self.passages)
        self.page_docs = [p for p in self.pages if p.kind <= kb.PROMO]
        self.page_bm25 = BM25([tokens(" ".join([p.title, p.path.replace("/", " ").replace("-", " ")] +
                                               [x.heading for x in p.passages[:40]])) for p in self.page_docs])


def build_index(snapshot) -> SnapshotIndex:
    taken = date.fromisoformat(snapshot.taken_on[:10])
    idx = SnapshotIndex(snapshot.snapshot_id, taken)
    seen = set()
    for pg in snapshot.pages:
        if kb.is_junk_url(pg.url):
            continue
        key = pg.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        rec = kb.build_page(pg.url, pg.title, pg.markdown, pg.fetched_at, snapshot.snapshot_id, taken)
        idx.pages.append(rec)
        idx.hours.extend(parse_hours(rec, kb.main_body(pg.markdown)))
    idx.finalize()
    return idx
