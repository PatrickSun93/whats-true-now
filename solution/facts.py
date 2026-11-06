"""
Structured facts beyond rate tables: fees, contact details, addresses, phone numbers.

Three page "shapes" become one kind of record, Fact(subject, attr, qualifier, value, page, ...):
  1. tables           every row of a table whose cells hold money / "Free" values (fee schedules,
                      "minimum to open" rows). A row with no value is a section label for the rows below it.
  2. "Label: value"   lines such as "Routing Number: 273974581", "Address: P.O. Box 179 ...".
  3. cards & prose    a passage with a street address ("Dupaco Branch 11375 Oldenburg Ln., Galena, IL, 61036")
                      and sentences like "X is located at Y" / "the phone number for X is Y".

Answering (rule L): a question whose words ask for a fee, phone number, address or routing number is
answered from these records when one record's label covers the question's content words; several
equally good records are all listed; otherwise the question goes to passage search as before.
Rule D applies between copies: newest effective date, then the more authoritative page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import kb
from search import tokens

MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
FREE = re.compile(r"^(free|no charge|no fee|n/?a|none)\b", re.I)
PCT_FEE = re.compile(r"\b\d{1,2}(?:\.\d+)?%\*?\s*(?:of|per)\b", re.I)
ROUTING_IS = re.compile(r"\b(?:routing|ABA) number[^.\d]{0,12}(\d{9})\b", re.I)   # "routing number is / (273974581)"
PHONE = re.compile(r"(?:\(\d{3}\)\s?|\b\d{3}[-. ])\d{3}[-. ]\d{4}\b")
ROUTING = re.compile(r"\b\d{9}\b")
ADDR = re.compile(r"\b\d{2,5} [A-Z][A-Za-z0-9 .'-]{2,40}?(?:St|Ave|Rd|Ln|Dr|Blvd|Hwy|Pkwy|Street|Avenue|Road|Lane|Drive|Boulevard|Highway|Way|Pl|Ct|Sq)\.?,? [A-Z][A-Za-z .]+, (?:IA|IL|WI|Iowa|Illinois|Wisconsin),? \d{5}")
PO_BOX = re.compile(r"P\.?O\.? Box \d+[^|\n]{0,40}\d{5}(?:-\d{4})?")
LABEL_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**([A-Z][A-Za-z0-9 &/'()\-]{2,40}?)\**:\s+(\S.{0,120})$")
FILE_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-_ ](\d{1,2})[-_ ](20\d\d)\b", re.I)
LOCATED = re.compile(r"\b([A-Z][^.]{0,80}?)\s+(?:is |are |was )?located at (\d+ [^,.;]{3,60})", re.I)
PHONE_FOR = re.compile(r"phone number (?:for|of|at) (?:the )?([^.]{3,60}?) is (\(?\d{3}\)?[-. ]?\d{3}[-. ]\d{4})", re.I)

# label words that name an attribute (shape 2); the value's shape decides the attr otherwise
ATTR_LABELS = {"address": "address", "mailing address": "address", "phone": "phone", "phone number": "phone",
               "telephone": "phone", "fax": "fax", "routing number": "routing", "aba routing number": "routing",
               "routing": "routing", "email": "email", "e-mail": "email"}


@dataclass
class Fact:
    page: kb.PageRec
    subject: str                 # row label / card name / heading
    attr: str                    # fee | amount | phone | address | routing | fax | email
    qualifier: str               # column header, section label, "non-members" ...
    value: str                   # as written
    context: str                 # caption / heading chain, for matching
    effective: Optional[date]
    index: int
    generic: bool = False        # the organisation's own contact detail ("Phone: 800-...")
    key: tuple = field(default=())
    doc: set = field(default_factory=set)

    def display(self) -> str:
        q = f" ({self.qualifier})" if self.qualifier and self.qualifier.lower() not in self.subject.lower() else ""
        return f"{self.subject}{q}: {self.value}"


def value_kind(v: str) -> Optional[str]:
    if MONEY.search(v) or FREE.match(v) or PCT_FEE.search(v):
        return "fee"
    if PHONE.search(v):
        return "phone"
    if ROUTING.fullmatch(v.strip()):
        return "routing"
    if ADDR.search(v) or PO_BOX.search(v):
        return "address"
    return None


def norm_value(v: str) -> str:
    m = MONEY.search(v)
    if m:
        return m.group(0).replace(" ", "").replace(",", "")
    if FREE.match(v):
        return "free"
    return re.sub(r"[^0-9a-z]", "", v.lower())[:40]


def _norm_key(s: str) -> str:
    return " ".join(sorted(set(tokens(s))))


def _page_effective(page: kb.PageRec) -> Optional[date]:
    """A fee schedule's date is often only in its file name ("...-jan-15-2025-1.pdf")."""
    m = FILE_DATE.search(page.path)
    if m:
        try:
            return date(int(m.group(3)), kb.MONTHS.get(m.group(1).lower()[:3], 1) if hasattr(kb, "MONTHS") else 1, int(m.group(2)))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------------- shape 1
def facts_from_table(t: kb.Table) -> list:
    """Rows whose value cells are money / Free. Rate tables (% values) are handled by rates.py."""
    page = t.page
    if page.kind == kb.CALCULATOR or re.search(r"\.(xlsx?|csv)$", page.path, re.I):
        return []                                   # calculator tables and spreadsheets show example inputs
    rows = list(t.rows)
    if t.header and any(value_kind(c) for c in t.header[1:]) and value_kind(t.header[0]) is None:
        rows = [t.header] + rows                    # a header that is really the first data row
        header = [""] * len(t.header)
    else:
        header = t.header
    caption = " · ".join(c for c in t.caption if c)
    eff = t.effective or _page_effective(page)
    out, section = [], ""
    for cells in rows:
        label, vals = "", []
        for i, cell in enumerate(cells):
            cell = cell.strip()
            if not cell:
                continue
            kind = value_kind(cell)
            if kind == "fee" and "%" not in cell or (kind == "fee" and PCT_FEE.search(cell)):
                vals.append((header[i].strip() if i < len(header) else "", cell))
            elif not label and len(cell) < 160 and not value_kind(cell):
                label = cell
        if label and not vals:
            if len(label) < 70:
                section = label                     # "Deposit Services Business", "Safe-Deposit Box"
            continue
        if not label or len(re.findall(r"[A-Za-z]", label)) < 3:
            continue                                # a row labelled "2" is an example, not a fact
        for col, v in vals:
            qual = col if col and not col.lower().startswith("col") else ""
            attr = "fee" if re.search(r"fee|charge|service|price|cost|schedule|penalt",
                                      " ".join((page.title, page.path, caption, section, label, col)), re.I) else "amount"
            ctx = " ".join(x for x in (page.title, caption, section) if x)
            out.append(Fact(page, label, attr, qual, v, ctx, eff, t.index))
    return out


TEXT_ROW = re.compile(r"^(?P<label>[A-Z(][^$|\n]{2,90}?)\s+(?P<value>(?:\$\s?\d[\d,]*(?:\.\d{1,2})?|Free\b)[^|\n]{0,70})$")


def facts_from_text_rows(page: kb.PageRec, lines: list) -> list:
    """Shape 1b: a fee schedule rendered as plain lines ("Official check $5.00* each"), as the PDF is in
    the older snapshots. A line with no amount is the section for the lines under it; a line starting
    with "(" continues the label above. Only pages with at least four such rows count."""
    rows, section, pending = [], "", ""
    for raw in lines:
        s = kb.clean_inline(raw.replace("\\*", "*")).strip()
        if not s or s.startswith(("|", "*", "#", "-", "•")):
            continue
        m = TEXT_ROW.match(s)
        if m:
            label = (pending + " " + m.group("label")).strip() if pending else m.group("label").strip()
            pending = ""
            if len(re.findall(r"[A-Za-z]", label)) >= 3 and not label.endswith((".", ":")):
                rows.append((label, m.group("value").strip(), section))
        elif len(s) < 70 and not s.endswith((".", ":", "?", "!")) and not MONEY.search(s):
            if s.startswith("(") or (pending and not re.search(r"[A-Z]", s[:1])):
                pending = (pending + " " + s).strip()          # wrapped label: "Foreign currency exchange fee"
            else:
                section, pending = s, (s if re.search(r"\b(fee|inquiry|transfer|charge)\b", s, re.I) and not s.istitle() else "")
        else:
            pending = ""
    if len(rows) < 4:
        return []
    eff = _page_effective(page)
    out = []
    for i, (label, value, section) in enumerate(rows):
        attr = "fee" if re.search(r"fee|charge|service|price|cost|schedule|penalt", page.title + " " + section + " " + label, re.I) else "amount"
        out.append(Fact(page, label, attr, "", value, " ".join(x for x in (page.title, section) if x), eff, 1000 + i))
    return out


# ---------------------------------------------------------------------------------- shape 2
def facts_from_lines(page: kb.PageRec, lines: list) -> list:
    out, heading = [], page.title
    for pos, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#"):
            heading = kb.clean_inline(s.lstrip("#").strip())
            continue
        m = LABEL_LINE.match(kb.clean_inline(s))
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        attr = ATTR_LABELS.get(label.lower()) or value_kind(value)
        if attr is None or attr == "fee" and label.lower() in ("example", "hint", "tip"):
            continue
        generic = label.lower() in ATTR_LABELS and page.kind <= kb.PRODUCT
        out.append(Fact(page, label if generic else f"{heading} — {label}", attr, "", value, heading, None, -1 - pos, generic))
    return out


# ---------------------------------------------------------------------------------- shape 3
def facts_from_passage(psg: kb.Passage) -> list:
    out, text, page = [], psg.text, psg.page
    if page.kind == kb.ARTICLE and not page.pub_date:
        return out
    for m in ADDR.finditer(text):
        before = text[:m.start()].strip(" ·-—")
        after = text[m.end():].strip(" ·-—")
        name = re.sub(r"^(Dupaco Credit Union|Dupaco)\s*", "", before).strip() or psg.heading
        name = name[-80:]
        out.append(Fact(page, name or psg.heading, "address", "", m.group(0), psg.heading, None, psg.pos))
        services = [x.strip() for x in re.split(r"\s·\s", after) if 2 < len(x.strip()) < 40]
        services = [x for x in services if not re.match(r"^(Closed|Open)\b", x)]
        if services:
            out.append(Fact(page, name or psg.heading, "services", "", ", ".join(services[:6]), psg.heading, None, psg.pos))
    for m in LOCATED.finditer(text):
        subj = re.sub(r"^(The|Our|A)\s+", "", m.group(1).strip())
        if 3 < len(subj) < 80:
            out.append(Fact(page, subj, "address", "", m.group(2).strip(), psg.heading, None, psg.pos))
            # "... is located at 1100 E. Business Highway 151, and the phone number is (608) 348-4499"
            tail = re.match(r"[^.]{0,80}?phone(?: number)? is (\(?\d{3}\)?[-. ]?\d{3}[-. ]\d{4})", text[m.end():], re.I)
            if tail:
                out.append(Fact(page, subj, "phone", "", tail.group(1), psg.heading, None, psg.pos))
    for m in PHONE_FOR.finditer(text):
        out.append(Fact(page, m.group(1).strip(), "phone", "", m.group(2), psg.heading, None, psg.pos))
    for m in ROUTING_IS.finditer(text):
        out.append(Fact(page, "Routing Number", "routing", "", m.group(1), psg.heading, None, psg.pos, generic=page.kind <= kb.PRODUCT))
    return out


def finish(facts: list) -> list:
    for f in facts:
        # an address names its place ("..., Galena, IL"), so the value is part of what can be matched
        extra = f.value if f.attr in ("address", "phone", "services") else ""
        f.doc = set(tokens(f.subject + " " + f.qualifier + " " + f.context + " " + extra))
        f.key = ("money" if f.attr in ("fee", "amount") else f.attr, _norm_key(f.subject), _norm_key(f.qualifier))
    return facts


# ---------------------------------------------------------------------------------- questions
TYPE_WORDS = {
    "fee": r"\b(fee|fees|charge|charges?|charged|cost|costs?|price|pay|paying|rent|how much|penalty|expensive)\b",
    "phone": r"\b(phone|telephone|call|number to call|contact number)\b",
    "routing": r"\b(routing|aba|direct deposit|payroll)\b",
    "address": r"\b(address|located|location|where is|where's|mail(?:ing)?|street|find)\b",
}
TYPE_RX = {k: re.compile(v, re.I) for k, v in TYPE_WORDS.items()}
STOP_T = set(tokens("what is are the a an for of to do does i my you your dupaco there much how it can me give tell "
                    "please if get need want would will charge charged cost costs fee fees number pay paying use using "
                    "usage reach contact call when which who someone anyone"))
# words that only restate the question type, per type
TYPE_STOP = {
    "fee": set(tokens("price rent penalty expensive have paid charges")),
    "phone": set(tokens("telephone speak talk representative customer service support hotline line")),
    "routing": set(tokens("direct deposit payroll paycheck employer aba number")),
    "address": set(tokens("mail mailing letter send write correspondence located location street find office")),
}
# everyday words -> the website's word (stemmed like the index)
SYN = {tokens(k)[0]: tokens(v)[0] for k, v in {
    "receive": "incoming", "receiving": "incoming", "inbound": "incoming", "abroad": "international",
    "overseas": "international", "foreign": "international", "cashier": "official", "personal": "consumer",
    "bounce": "overdraft", "bounced": "overdraft", "nsf": "overdraft", "yearly": "annual", "counter": "counting",
}.items()}
NEGATION = re.compile(r"\b(not|isn't|isnt|aren't|non|other|outside|another|different)\b", re.I)
SUPERLATIVE = re.compile(r"\b(largest|biggest|most expensive|highest|smallest|cheapest|lowest|least)\b", re.I)


DEVICE_PHONE = re.compile(r"\b(my|your|the|a|with|using|on|from|mobile|cell|smart|lost|stolen)\s+phone\b", re.I)


def question_type(q: str) -> Optional[str]:
    low = q.lower()
    for t in ("routing", "phone", "address", "fee"):
        if TYPE_RX[t].search(low):
            if t == "phone" and re.search(r"\bnumber\b", low) and "routing" in low:
                continue
            if t == "phone" and DEVICE_PHONE.search(low) and not re.search(r"phone number|\bcall\b", low):
                continue                           # "pay with my phone": the phone is a device, not the ask
            return t
    return None


def content_tokens(q: str, qtype: str = "") -> list:
    out = []
    for w in tokens(q):
        w = SYN.get(w, w)
        if w not in STOP_T and w not in TYPE_STOP.get(qtype, ()) and "'" not in w:    # isn't, I've
            out.append(w)
    if NEGATION.search(q):
        out.append("non")                        # "an ATM that isn't Dupaco's" -> "Non-Dupaco ATM"
    return out


@dataclass
class FactResult:
    facts: list
    winner: Fact
    others: list          # same key, different value (older / less authoritative)


def _rank(f: Fact):
    eff = f.effective or date(1900, 1, 1)
    return (-eff.toordinal(), f.page.kind, -f.page.freshness.toordinal(), f.page.path, f.index)


def select(facts: list, q: str, idf: dict) -> Optional[FactResult]:
    qtype = question_type(q)
    if not qtype:
        return None
    attrs = {"fee": ("fee", "amount"), "phone": ("phone",), "routing": ("routing",), "address": ("address",)}[qtype]
    cands = [f for f in facts if f.attr in attrs]
    if not cands:
        return None
    cset = set(content_tokens(q, qtype))
    if qtype != "fee" and cset and not cset & set().union(*(f.doc for f in cands)):
        cset = set()                               # "get ahold of Dupaco": no record uses any of these words
    if not cset:                                   # "What's Dupaco's phone number?" -> the organisation's own
        gen = [f for f in cands if f.generic]
        if not gen:
            return None
        gen.sort(key=_rank)
        return FactResult([gen[0]], gen[0], [])
    q_w = sum(idf.get(t, 1.0) for t in cset) or 1.0
    scored = []
    for f in cands:
        hit = cset & f.doc
        cover = sum(idf.get(t, 1.0) for t in hit) / q_w          # share of the question's words on the record
        subj = set(tokens(re.sub(r"\(.*?\)", " ", f.subject))) - STOP_T     # "(check, ACH & bill pay)" is detail
        back = len(subj & cset) / max(1, len(subj))                # share of the record's label in the question
        # the record must cover most of the question, or be a label the member named almost fully
        if cover < 0.5 or (cover < 0.75 and back < 0.5):
            continue
        scored.append((round(cover, 3), round(back, 3), f))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1], _rank(x[2])))
    top_cover, top_back = scored[0][0], scored[0][1]
    tied = [f for c, b, f in scored if c >= top_cover - 1e-9 and b >= top_back - 1e-9]
    # one record per key; rule D picks the copy
    by_key = {}
    for f in tied:
        by_key.setdefault(f.key, []).append(f)
    chosen, others = [], []
    for k, fs in by_key.items():
        fs.sort(key=_rank)
        chosen.append(fs[0])
        others += [o for o in fs[1:] if norm_value(o.value) != norm_value(fs[0].value)]
    chosen.sort(key=_rank)
    sup = SUPERLATIVE.search(q)
    if sup and len(chosen) > 1 and all(MONEY.search(f.value) for f in chosen):
        # "largest box", "cheapest option": among equally matching rows, the dearest / cheapest one
        amount = lambda f: float(MONEY.search(f.value).group(0).strip("$ ").replace(",", ""))
        pick = max if sup.group(1).lower() in ("largest", "biggest", "most expensive", "highest") else min
        chosen = [pick(chosen, key=amount)]
    return FactResult(chosen[:3], chosen[0], others)


def agreeing_pages(facts: list, winner: Fact) -> list:
    """Other pages stating the same value for the same attribute (sources for the answer)."""
    v = norm_value(winner.value)
    urls = []
    for f in facts:
        if f.attr == winner.attr and norm_value(f.value) == v and f.page.url not in urls and f.page.url != winner.page.url:
            urls.append(f.page.url)
    urls.sort(key=lambda u: kb.page_kind(u))
    return urls
