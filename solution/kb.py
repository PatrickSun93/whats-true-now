"""
Turning raw crawled pages into things we can reason about.

For every page version we keep:
  * what KIND of page it is (rate sheet, product page, promo landing page, calculator,
    downloaded document, news/blog article) -> drives the "who is more authoritative" rules
  * its publish date when it is an article (news releases describe the day they were written)
  * clean text passages (for search)
  * every markdown table, with the caption above it and the "Rates are effective as of ..."
    date printed under it (the single most useful freshness signal on this site)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlparse

# --------------------------------------------------------------------------------------
# Page kinds. Lower number = more authoritative.  (Rule A in NOTES.md)
# --------------------------------------------------------------------------------------
RATE_SHEET, PRODUCT, PROMO, CALCULATOR, DOCUMENT, ARTICLE = 0, 1, 2, 3, 4, 5
KIND_NAMES = {RATE_SHEET: "official rate sheet", PRODUCT: "product page", PROMO: "promo landing page",
              CALCULATOR: "calculator page", DOCUMENT: "downloaded document", ARTICLE: "news/blog article"}

PROMO_PATHS = ("/switch-and-earn", "/earn-more", "/offer-expired", "/homebuying-guidance", "/mortgage-refi",
               "/21-day-sprint-to-savings-challenge", "/sprint-to-savings-challenge", "/not-for-sale")
ARTICLE_PREFIXES = ("/newsroom", "/learn", "/resources", "/business/resources", "/dupacodaily", "/covid-19",
                    "/2018/", "/2019/", "/2020/", "/2021/", "/2022/", "/2023/", "/2024/", "/2025/", "/2026/")


def url_path(url: str) -> str:
    p = urlparse(url)
    return (p.path.rstrip("/") or "/").lower()


def page_kind(url: str) -> int:
    path = url_path(url)
    if path in ("/rates", "/business-rates"):
        return RATE_SHEET
    if path.startswith("/calculators") or path == "/business-calculators":
        return CALCULATOR
    if path.startswith("/hubfs"):
        return DOCUMENT
    if any(path == a.rstrip("/") or path.startswith(a) for a in ARTICLE_PREFIXES):
        return ARTICLE
    if path.endswith("-lp") or path in PROMO_PATHS:
        return PROMO
    return PRODUCT


def is_junk_url(url: str) -> bool:
    """Privacy-policy modal copies with tracking query strings are duplicates of one page."""
    return "campaignId=" in url or "hsLang=" in url


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

LONG_DATE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.? (\d{1,2}),? (\d{4})\b", re.I)
NUM_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
EFFECTIVE = re.compile(r"effective(?: as of)?:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)


def parse_num_date(s: str) -> Optional[date]:
    m = NUM_DATE.search(s)
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_long_date(s: str) -> Optional[date]:
    m = LONG_DATE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None


def article_date(url: str, md: str) -> Optional[date]:
    """Publish date of a news/blog article: the byline date near the top, else /YYYY/MM/DD/ in the URL."""
    d = parse_long_date(md[:500])
    if d:
        return d
    m = re.search(r"/(20\d\d)/(\d\d)/(\d\d)/", url)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"/(20\d\d)/(\d\d)/", url)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


# --------------------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------------------
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
FOOTNOTE_PCT = re.compile(r"(\d%)(\d)\b")              # "4.20%1" -> "4.20%"
FOOTNOTE_APR = re.compile(r"\b(APR|APY)\s?\d\b")       # "5.39% APR1" -> "5.39% APR"


def strip_footnotes(cell: str) -> str:
    cell = FOOTNOTE_PCT.sub(r"\1", cell)
    cell = FOOTNOTE_APR.sub(r"\1", cell)
    cell = cell.replace("\\*", "*").replace("\\", "")
    return re.sub(r"\s+", " ", cell).strip()


def clean_inline(text: str, keep_urls: bool = False) -> str:
    text = IMG.sub("", text)
    if keep_urls:
        def _link(m):
            label, url = m.group(1).strip(), m.group(2)
            if url.startswith("http") and "#" not in url[-1:] and label and url.rstrip("/") not in label:
                return f"{label} ({url})"
            return label or url
        text = LINK.sub(_link, text)
    else:
        text = LINK.sub(lambda m: m.group(1), text)
    text = text.replace("\\r\\n", " ").replace("\\n", " ").replace("**", "").replace("\\*", "*")
    text = text.replace("\\", "")
    return re.sub(r"[ \t]+", " ", text).strip()


HANDPICKED = re.compile(r"\n## (?:Handpicked for You|Related [^\n]*|You may also like)\n.*?(?=\n## |\Z)", re.S)


def main_body(md: str) -> str:
    """Drop the 'Handpicked for You' teaser blocks: they quote other (often old) articles."""
    return HANDPICKED.sub("\n", md)


# --------------------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------------------
@dataclass
class Table:
    page: "PageRec"
    caption: list            # nearest short lines above the table, outermost first
    header: list
    rows: list               # list of list[str] (cleaned cells)
    effective: Optional[date]
    index: int               # position on the page


@dataclass
class Passage:
    page: "PageRec"
    text: str                # cleaned text (links reduced to their label)
    text_urls: str           # same, but with link URLs kept (for "what's the link" questions)
    heading: str
    pos: int


@dataclass
class PageRec:
    url: str
    path: str
    title: str
    snapshot_id: str
    taken_on: date
    fetched_at: str
    kind: int
    pub_date: Optional[date]
    tables: list = field(default_factory=list)
    passages: list = field(default_factory=list)

    @property
    def freshness(self) -> date:
        """When the page's statements were true: an article's publish date (undated articles count
        as old), any other page as of the crawl."""
        if self.kind == ARTICLE:
            return self.pub_date or date(2000, 1, 1)
        return self.taken_on


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------
TABLE_SEP = re.compile(r"^\|\s*:?-{2,}")


def _split_cells(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [strip_footnotes(clean_inline(c)) for c in line.split("|")]


def _is_caption_line(s: str) -> bool:
    if not s or len(s) > 90 or s.startswith(("|", "[", "!", "- [", "*", "✕")):
        return False
    low = s.lower()
    if "effective" in low or low.startswith("rates ") or re.match(r"^\d[A-Z*]", s) or s.endswith((".", ":", "!", "?")):
        return False
    return True


def parse_tables(page: PageRec, lines: list) -> list:
    tables, i, n = [], 0, len(lines)
    last_end = 0
    while i < n:
        if lines[i].lstrip().startswith("|") and i + 1 < n and TABLE_SEP.match(lines[i + 1].strip()):
            start = i
            header = _split_cells(lines[i])
            j = i + 2
            rows = []
            while j < n and lines[j].lstrip().startswith("|"):
                cells = _split_cells(lines[j])
                if any(cells):
                    rows.append(cells)
                j += 1
            # caption: up to two short lines above (stop at the previous table)
            cap = []
            k = start - 1
            while k >= last_end and len(cap) < 2 and start - k < 40:
                raw = lines[k].strip().lstrip("#").strip()
                if raw.startswith("|"):
                    break
                s = clean_inline(raw)
                tab = re.match(r"^- \[([^\]]{3,60})\]\([^)]*#\)\s*$", raw)   # section tab: "- [Certificate Rates](/rates#)"
                if tab:
                    cap.insert(0, tab.group(1))
                elif not raw.startswith(("[", "!", "- ", "* ", "*")) and _is_caption_line(s):
                    cap.insert(0, s)
                k -= 1
            # effective date: first "effective as of" after the table, before the next table
            eff = None
            k = j
            while k < n and k < j + 30:
                if lines[k].lstrip().startswith("|") and k + 1 < n and TABLE_SEP.match(lines[k + 1].strip()):
                    break
                m = EFFECTIVE.search(lines[k])
                if m:
                    eff = parse_num_date(m.group(1))
                    break
                k += 1
            tables.append(Table(page, cap, header, rows, eff, len(tables)))
            last_end = j
            i = j
            continue
        i += 1
    return tables


def parse_passages(page: PageRec, body: str) -> list:
    """Paragraphs (tables removed), with short consecutive paragraphs merged (headings, FAQ
    questions, hours lists) so a passage carries its own context."""
    out, heading, buf, buf_u = [], page.title, [], []
    paras = re.split(r"\n\s*\n", body)
    pos = 0

    def flush():
        nonlocal buf, buf_u, pos
        if buf:
            t = " ".join(buf).strip()
            if len(t) > 2:
                out.append(Passage(page, t, " ".join(buf_u).strip(), heading, pos))
                pos += 1
        buf, buf_u = [], []

    for para in paras:
        raw = para.strip()
        if not raw or raw.startswith("|") or raw in ("✕", "* * *", "\\# # #"):
            continue
        if "Cancel" in raw and "[Continue]" in raw:
            continue
        txt = clean_inline(raw)
        if not txt or len(txt) < 2:
            continue
        txt_u = clean_inline(raw, keep_urls=True)
        if raw.startswith("#"):
            flush()
            heading = txt.lstrip("#").strip()
            buf, buf_u = [heading], [heading]
            continue
        buf.append(txt)
        buf_u.append(txt_u)
        if sum(len(b) for b in buf) >= 90:
            flush()
    flush()
    return out


def build_page(url: str, title: str, md: str, fetched_at: str, snapshot_id: str, taken_on: date) -> PageRec:
    kind = page_kind(url)
    page = PageRec(url=url, path=url_path(url), title=(title or "").replace(" | Dupaco", "").strip(),
                   snapshot_id=snapshot_id, taken_on=taken_on, fetched_at=fetched_at, kind=kind,
                   pub_date=article_date(url, md) if kind == ARTICLE else None)
    body = main_body(md)
    lines = body.split("\n")
    page.tables = parse_tables(page, lines)
    page.passages = parse_passages(page, body)
    return page
