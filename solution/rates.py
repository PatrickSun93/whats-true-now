"""
Rate tables -> rate facts, and answering rate questions from them.

A rate fact is one table row: product concepts (what it is), qualifiers (term, model-year range,
balance / loan-amount range, variants such as jumbo / IRA / business / refinance / intro) and its
values (dividend rate, APY, rate, APR), plus where it came from and its "effective as of" date.

Choosing what to say (rules B-E in NOTES.md):
  B. Respect qualifiers: a row is only eligible if its term / model year / amount range fit the
     question, and if every product word in the question is a product word of the row.
  C. Default tier: variants the member didn't ask for (jumbo, IRA, business, refinance, intro
     rates...) are dropped when a plain version exists. Plain "savings" means Share Savings.
  D. The same row shown on several pages -> the page whose table says the NEWEST
     "Rates are effective as of" date wins; ties go to the more authoritative page kind
     (rate sheet > product page > promo page > calculator > document > article).
  E. No eligible row -> the website doesn't publish that rate -> say so (confident=False).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from kb import KIND_NAMES, PRODUCT as PRODUCT_KIND, Table

# ---------------------------------------------------------------------------------------
# Concepts: (name, pattern in the QUESTION, pattern in the TABLE caption/header/row label)
# ---------------------------------------------------------------------------------------
PRODUCTS = [
    ("certificate", r"\b(certificates?|cds?|share certificates?|term shares?|certificate of deposit)\b", r"certificate"),
    ("special", r"\bspecials?\b|\bpromo(tional)? (cd|certificate)", r"special"),
    ("savings", r"\bsavings?\b", r"savings"),
    ("share_savings", r"\bshare savings\b|\bregular savings\b|\bbasic savings\b|\bprimary savings\b", r"share savings"),
    ("bright", r"\bbright\b", r"\bbright\b"),
    ("you_name_it", r"you[- ]?name[- ]?it", r"you name it"),
    ("holiday", r"\bholiday\b|christmas club", r"holiday"),
    ("launchpad", r"launch ?pad", r"launchpad"),
    ("high_yield", r"high[- ]?yield", r"high[- ]yield"),
    ("money_market", r"money market|investor'?s choice", r"money market"),
    ("investors_choice", r"investor'?s choice", r"investor'?s choice"),
    ("achieve", r"\bachieve\b", r"\bachieve\b"),
    ("hsa", r"\bhsa\b|health savings", r"health savings"),
    ("checking", r"\bchecking\b", r"checking"),
    ("first_rate", r"\b1st rate\b|\bfirst rate\b", r"1st rate"),
    ("free_checking", r"\bfree checking\b", r"free checking"),
    ("auto", r"\b(auto|autos|car|cars|vehicles?|trucks?|suvs?|motorcycles?)\b", r"\bauto|motorcycle"),
    ("mortgage", r"\b(mortgages?|home loans?|home purchase|house|\d{2}[- ]year fixed)\b", r"conventional|home loan|mortgage"),
    ("home_equity", r"home equity|\bheloc\b", r"home equity|heloc"),
    ("heloc", r"\bheloc\b|home equity line|line of credit", r"heloc|home equity line|line of credit"),
    ("home_equity_loan", r"home equity loan|second mortgage|fixed home equity", r"home equity loan|loan . standard"),
    ("home_improvement", r"home improvement|remodel|renovat", r"home improvement"),
    ("personal_loan", r"\bpersonal loans?\b|\bsignature loans?\b", r"personal loan"),
    ("boat_rv", r"\b(boats?|rvs?|campers?|motorhomes?)\b", r"\bboat|\brv\b"),
    ("atv", r"\b(atvs?|snowmobiles?|jet ?skis?|powersports?|utvs?)\b", r"\batv|snowmobile|jet ski"),
    ("visa", r"credit cards?|\bvisa\b", r"\bvisa\b|credit card|card type"),
    ("platinum", r"\bplatinum\b", r"platinum"),
    ("rewards", r"\brewards?\b", r"rewards"),
    # Products the site describes but publishes NO rate for. They have no table, so naming
    # them makes every row ineligible and we answer "not listed" instead of guessing.
    ("arm", r"\barms?\b|adjustable|\b\d{1,2}/1\b", r"adjustable|\barm\b"),
    ("construction", r"construction", r"construction"),
    ("land", r"\bland\b|\blot loans?\b", r"\bland\b"),
    ("student", r"\bstudent\b", r"student"),
    ("gov_loan", r"\b(va|fha|usda)\b", r"\b(va|fha|usda)\b"),
    ("business_loan", r"business loans?|commercial|equipment loans?|sba|real estate loans?|business lines? of credit",
     r"business loan|commercial|equipment|real estate"),
    ("ira_only_products", r"\bsep\b|\bcoverdell\b|education ira", r"\bsep\b|coverdell"),
]

# Variants: required when the question asks for them, otherwise rows carrying them are only used
# when no plain row fits (rule C).
VARIANTS = [
    ("jumbo", r"\bjumbo\b", r"jumbo"),
    ("ira", r"\bira\b|\biras\b|\broth\b|\btraditional\b|retirement", r"\bira\b|\broth\b|\btraditional\b"),
    ("roth", r"\broth\b", r"\broth\b"),
    ("traditional", r"\btraditional\b", r"\btraditional\b"),
    ("business", r"\bbusiness\b|\bcompany\b|\bcommercial\b", r"\bbusiness\b"),
    ("refinance", r"refinanc|\brefi\b", r"refinanc"),
    ("intro", r"\bintro(ductory)?\b|balance transfer|teaser", r"\bintro(ductory)?\b|balance transfer"),
    ("relief", r"relief|80% area median", r"relief"),
    ("after_promo", r"after (the )?promo", r"after promo"),
]

SAVINGS_SUBPRODUCTS = {"share_savings", "bright", "you_name_it", "holiday", "launchpad", "high_yield", "hsa",
                       "money_market"}

RATE_INTENT = re.compile(r"\b(rates?|apy|apys|apr|aprs|interest|dividends?|yields?|percent(age)?)\b|%", re.I)

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "seventeen": 17, "eighteen": 18, "twenty": 20,
            "twenty-four": 24, "thirty": 30, "thirty-six": 36, "forty-eight": 48, "sixty": 60,
            "seventy-two": 72, "eighty-four": 84}

TERM_RE = re.compile(r"\b(\d{1,3}|" + "|".join(sorted(WORD_NUM, key=len, reverse=True)) +
                     r")[- ]?(months?|mos?\b|mo\.|years?|yrs?)\b", re.I)


def concepts(text: str, table_side: bool) -> set:
    low = text.lower().replace("’", "'")
    out = set()
    for name, qp, tp in PRODUCTS + VARIANTS:
        if re.search(tp if table_side else qp, low):
            out.add(name)
    return out


def terms(text: str) -> set:
    out = set()
    for m in TERM_RE.finditer(text):
        n = m.group(1).lower()
        n = WORD_NUM.get(n, None) if not n.isdigit() else int(n)
        if n is None:
            continue
        unit = m.group(2).lower()
        out.add(n * 12 if unit.startswith("y") else n)
    return out


def _money(s: str) -> float:
    return float(s.replace(",", "").rstrip("."))


RANGE_PATTERNS = [
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:[-–—]|to)\s*\$?\s?([\d,]+(?:\.\d+)?)"), lambda a, b: (_money(a), _money(b))),
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*\+"), lambda a: (_money(a), float("inf"))),
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:or|and)\s*(?:less|under|below)"), lambda a: (0.0, _money(a))),
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:or more|and up|and above|and over)"), lambda a: (_money(a), float("inf"))),
]


def amount_range(label: str):
    for rx, fn in RANGE_PATTERNS:
        m = rx.search(label)
        if m:
            return fn(*m.groups())
    return None


def model_year_range(label: str):
    low = label.lower()
    m = re.search(r"(\d{4})\s*model year\s*\(and older\)", low)
    if m:
        return (1900, int(m.group(1)))
    m = re.search(r"(\d{4})\s*[-–]\s*(\d{2,4})\s*model year", low) or re.search(r"model year\s*(\d{4})\s*[-–]\s*(\d{2,4})", low)
    if m:
        a, b = m.group(1), m.group(2)
        a, b = int(a), int(b) if len(b) == 4 else int(a[:2] + b)
        return (min(a, b), max(a, b))
    m = re.search(r"(\d{4})\s*model year|model year\s*(\d{4})", low)
    if m:
        y = int(m.group(1) or m.group(2))
        return (y, y)
    return None


# ---------------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------------
@dataclass
class RateRow:
    table: Table
    label: str                 # e.g. "2024-27 Model Year · 60 Months"
    display: str               # caption + label, for answers
    values: list               # [(metric, value_text)]
    concepts: set
    terms: set
    amount: Optional[tuple]
    model_years: Optional[tuple]
    range_rank: Optional[int] = None
    key: tuple = field(default=())
    periods: list = field(default_factory=list)   # per value: "" | "intro" | "after" (promo tables)

    @property
    def page(self):
        return self.table.page

    @property
    def effective(self) -> Optional[date]:
        return self.table.effective

    def value_str(self, metrics: Optional[set] = None, period: Optional[str] = None) -> str:
        items = list(zip(self.values, self.periods or [""] * len(self.values)))
        vals = [(m, v, p) for (m, v), p in items if metrics is None or m in metrics] or [(m, v, p) for (m, v), p in items]
        if period and any(p == period for _, _, p in vals):
            vals = [x for x in vals if x[2] == period]           # the member asked for one period only
        label_period = len({p for _, _, p in vals}) > 1
        return ", ".join(f"{v} {METRIC_LABEL.get(m, m)}".strip() + (PERIOD_LABEL[p] if label_period else "")
                         for m, v, p in vals)


METRIC_LABEL = {"apy": "APY", "apr": "APR", "dividend": "dividend rate", "rate": "rate", "": ""}
PERIOD_LABEL = {"": "", "intro": " (introductory)", "after": " (after the promotional period)"}
PERIOD_INTRO = re.compile(r"\b(1st|first|intro\w*|promo\w*)\b", re.I)
PERIOD_AFTER = re.compile(r"\bafter\b", re.I)
SKIP_COL = re.compile(r"\bmin\b|min\.|minimum|payment|points|to open|to earn", re.I)


def _metric(header: str, cell: str) -> str:
    c, h = cell.lower(), header.lower()
    if "apr" in c:
        return "apr"
    if "apy" in c:
        return "apy"
    if "apy" in h or "yield" in h:
        return "apy"
    if "apr" in h or "annual percentage rate" in h:
        return "apr"
    if "dividend" in h:
        return "dividend"
    return "rate"


def _clean_value(cell: str) -> str:
    v = re.sub(r"\s*\b(APR|APY)\b\s*$", "", cell).strip()
    return v


def rows_from_table(t: Table) -> list:
    out = []
    caption = " · ".join(t.caption)
    # a caption that names no product ("Regular", "Special") continues the table above it
    k = t.index

    def names_family(text):
        return concepts(text, table_side=True) - {"special"}

    while k > 0 and t.index - k < 3 and not names_family(caption + " " + " ".join(t.header)):
        k -= 1
        above = " · ".join(c for c in t.page.tables[k].caption if names_family(c))   # family only, not "Special"
        if above:
            caption = f"{above} · {caption}".strip(" ·")
    is_business_page = t.page.path.startswith("/business")
    for cells in t.rows:
        label_parts, values, periods = [], [], []
        for i, cell in enumerate(cells):
            head = t.header[i] if i < len(t.header) else ""
            if not cell or cell in ("—", "-", "N/A", "None"):
                continue
            if "%" in cell and PCT.search(cell) and not SKIP_COL.search(head):
                values.append((_metric(head, cell), _clean_value(cell)))
                # promo tables: "APY for 1st 6 Months" / "APY After Promotional Period"
                periods.append("after" if PERIOD_AFTER.search(head) else "intro" if PERIOD_INTRO.search(head) else "")
            elif SKIP_COL.search(head) or re.fullmatch(r"\$?\s?[\d,]+(\.\d+)?", cell):
                continue
            else:
                label_parts.append(cell)
        if not values or not label_parts:
            continue
        label = " · ".join(label_parts)
        doc = f"{caption} {' '.join(t.header)} {label}"
        cs = concepts(doc, table_side=True)
        if is_business_page:
            cs.add("business")
        # "Account Type" columns repeat the caption; don't print it twice
        display = label if (not caption or label_parts[0].lower() in caption.lower()
                            or caption.lower() in label.lower()) else f"{caption} — {label}"
        out.append(RateRow(table=t, label=label, display=display, values=values, concepts=cs,
                           terms=terms(label), amount=amount_range(label), model_years=model_year_range(label),
                           periods=periods if any(periods) else []))
    # rank of each row's range among the table's distinct ranges (aligns tiers across pages)
    for attr in ("model_years", "amount"):
        rngs = sorted({getattr(r, attr) for r in out if getattr(r, attr)}, key=lambda x: (-x[1], -x[0]))
        for r in out:
            if getattr(r, attr) and r.range_rank is None:
                r.range_rank = rngs.index(getattr(r, attr))
    for r in out:
        # same product + term + tier rank + same kind of columns = the same fact on any page
        tier = r.amount if r.amount else r.range_rank   # balance tiers by value, model years by rank
        r.key = (frozenset(r.concepts), frozenset(r.terms), tier,
                 _norm_label_words(r.label) if not r.terms and r.range_rank is None else "",
                 tuple(sorted(m for m, _ in r.values)))
    return out


def _norm_label_words(label: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z]+", label.lower()) if w not in ("account", "type"))


# ---------------------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------------------
@dataclass
class RateQuery:
    text: str
    concepts: set
    products: set
    variants: set
    term: Optional[int]
    model_year: Optional[int]
    amount: Optional[float]
    metrics: Optional[set]
    listing: bool


PRODUCT_NAMES = {p[0] for p in PRODUCTS}
VARIANT_NAMES = {v[0] for v in VARIANTS}


def parse_query(q: str) -> RateQuery:
    low = q.lower()
    cs = concepts(q, table_side=False)
    products, variants = cs & PRODUCT_NAMES, cs & VARIANT_NAMES
    if "savings" in products and not (products & SAVINGS_SUBPRODUCTS) and not products & {"certificate"} \
            and "ira" not in variants:
        products.add("share_savings")          # rule C: plain "savings" = Share Savings
    if "savings" in products and products & {"certificate", "money_market"}:
        products.discard("savings")
    if products & {"heloc", "home_equity_loan"}:
        products.add("home_equity")
    if products & {"home_equity", "heloc", "home_equity_loan"} and "mortgage" in products \
            and not re.search(r"mortgages?\b", low.replace("second mortgage", "")):
        products.discard("mortgage")
    if "refinance" in variants and not products:
        products.add("mortgage")               # a bare "refinance" means a mortgage refinance
    if re.search(r"\b(15|20|30)[- ]?year\b", low) and not products - {"home_equity"}:
        products.add("mortgage")               # 15/20/30-year terms are mortgage terms here
    ts = terms(q)
    term = min(ts) if ts else None
    model_year = None
    if products & {"auto", "atv", "boat_rv"} or "model" in low:
        m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", q)
        if m:
            model_year = int(m.group(1))
    amount = None
    m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(k|thousand)?\b", low) or re.search(r"\b(\d+(?:\.\d+)?)\s*(k)\b", low)
    if m:
        amount = _money(m.group(1)) * (1000 if m.group(2) else 1)
    metrics = None
    if re.search(r"\bapy\b|annual percentage yield|\byield\b", low):
        metrics = {"apy"}
    elif re.search(r"\bapr\b|annual percentage rate", low):
        metrics = {"apr"}
    elif re.search(r"dividend rate", low):
        metrics = {"dividend"}
    listing = bool(re.search(r"\b(offer|available|options|all|list|kinds?|types?|every)\b", low))
    return RateQuery(q, cs, products, variants, term, model_year, amount, metrics, listing)


DEPOSIT_PRODUCTS = {"certificate", "savings", "share_savings", "bright", "you_name_it", "holiday", "launchpad",
                    "high_yield", "money_market", "hsa", "checking", "first_rate", "free_checking", "ira"}
PAY_INTENT = re.compile(r"\b(pay|pays|paying|earn|earns|earning)\b", re.I)


LIST_WORDS = re.compile(r"\b(terms?|tiers?|lengths?|durations?|maturities|specials?)\b", re.I)
LIST_INTENT = re.compile(r"\b(available|offer|offers|offered|have|has|come in|are there|exist|currently|choose from)\b", re.I)


def is_rate_question(q: str) -> bool:
    """Asks for a rate (or what a deposit account pays) AND names a product or variant.
    Rule O: asking which terms / tiers / specials a product comes in is answered from its rate table too."""
    rq = parse_query(q)
    named = rq.products | (rq.variants - {"business"})
    if not named:
        return False
    plain = re.sub(r"high[- ]?yield", "", q, flags=re.I)
    return (bool(RATE_INTENT.search(plain)) or bool(PAY_INTENT.search(q) and (named & DEPOSIT_PRODUCTS))
            or bool(LIST_WORDS.search(q) and LIST_INTENT.search(q)))


# ---------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------
def eligible(row: RateRow, rq: RateQuery) -> Optional[float]:
    """None = row cannot answer the question; otherwise a score (higher = better fit)."""
    need = set(rq.products)
    if "certificate" in need and row.terms and any(m in ("apy", "dividend") for m, _ in row.values):
        need.discard("certificate")        # a deposit row with a term in months is a certificate ("60 Months" IRA)
    have = set(row.concepts)
    if "intro" in row.periods:
        have.add("intro")                  # a promo table's "1st 6 months" column answers an "introductory" question
    if "after" in row.periods:
        have.add("after_promo")
    if not need <= have or not rq.variants <= have:
        return None
    score = 0.0
    if rq.term is not None:
        if row.terms and rq.term not in row.terms:
            return None
        if not row.terms and row.table.index < 0:      # prose without a term can't answer a term question
            return None
        if rq.term in row.terms:
            score += 3
    if rq.model_year is not None and row.model_years:
        lo, hi = row.model_years
        if not lo <= rq.model_year <= hi:
            return None
        score += 3
    if rq.amount is not None and row.amount:
        lo, hi = row.amount
        if not lo - 0.005 <= rq.amount <= hi + 0.005:
            return None
        score += 3
    extra = (row.concepts & (VARIANT_NAMES | {"home_equity"})) - rq.variants - rq.products - {"roth", "traditional"}
    if "business" in extra and rq.listing:
        extra = extra - {"business"}     # a listing question ("what cards do you offer") covers the business versions too
    score -= 5 * len(extra)
    return score


def _table_rank(t: Table):
    eff = t.effective or date(1900, 1, 1)
    return (-eff.toordinal(), t.page.kind, t.page.path, t.index)


@dataclass
class RateResult:
    rows: list
    winner_table: Optional[Table]
    agreeing_pages: list
    disagreements: list          # [(row, other_row)]


def select(all_rows: list, rq: RateQuery, cap: int = 14) -> Optional[RateResult]:
    scored = [(eligible(r, rq), r) for r in all_rows]
    scored = [(s, r) for s, r in scored if s is not None]
    if not scored:
        return None
    best = max(s for s, _ in scored)
    cands = [r for s, r in scored if s >= best - 1e-9]
    # narrowest amount range wins among tiers that all contain the amount
    if rq.amount is not None:
        def width(r):
            return (r.amount[1] - r.amount[0]) if r.amount else float("inf")
        by_table = {}
        for r in cands:
            by_table.setdefault(id(r.table), []).append(r)
        cands = []
        for rs in by_table.values():
            w = min(width(r) for r in rs)
            cands += [r for r in rs if width(r) == w]
    tables = sorted({id(r.table): r.table for r in cands}.values(), key=_table_rank)
    # Rule D: the newest-effective, most authoritative table answers. Rows of the SAME product
    # family on other tables (e.g. business cards on their own page) are added when the winning
    # table doesn't carry them.
    winner = tables[0]
    chosen = [r for r in cands if r.table is winner]
    covered = {r.key for r in chosen}
    products_shown = {frozenset(r.concepts) for r in chosen}
    for t in tables[1:]:
        if t.page.kind > PRODUCT_KIND:      # only official pages may add products
            continue
        for r in cands:
            if r.table is t and r.key not in covered and frozenset(r.concepts) not in products_shown:
                chosen.append(r)
                covered.add(r.key)
    chosen = chosen[:cap]
    # agreement / disagreement with other pages showing the same row
    agreeing, disagree = [], []
    by_key = {}
    for r in cands:
        by_key.setdefault(r.key, []).append(r)
    for r in chosen:
        for o in by_key.get(r.key, []):
            if o is r or o.page.url == r.page.url:
                continue
            if _vals(o) == _vals(r):
                if o.page.url not in agreeing:
                    agreeing.append(o.page.url)
            else:
                disagree.append((r, o))
    return RateResult(chosen, winner, agreeing, disagree)


PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%(?:\s*[-–—]\s*(\d+(?:\.\d+)?)\s*%)?")


def norm_value(v: str) -> tuple:
    """'12.50% – 19.50%' == '12.50% - 19.50%'; '2.90% APR for 6 mos...' == '2.90%'."""
    m = PCT.search(v)
    if not m:
        return (v.strip().lower(),)
    return tuple(round(float(x), 4) for x in m.groups() if x)


def _vals(r: RateRow) -> tuple:
    return tuple(sorted((m, norm_value(v)) for m, v in r.values))


def describe_source(t: Table) -> str:
    eff = f", effective {t.effective.strftime('%m/%d/%Y')}" if t.effective else ""
    return f"{KIND_NAMES[t.page.kind]} {t.page.url}{eff}"


# ---------------------------------------------------------------------------------------
# Rates written in prose ("6-month certificate APY: 3.50%"). Used only when no table row fits.
# Never needed on the Dupaco snapshots (every rate there is in a table); the kit's selftest
# publishes its rates this way.
# ---------------------------------------------------------------------------------------
EXPIRED = re.compile(r"offer (has )?ended|\bexpired?\b|no longer (available|offered)|\bended\b", re.I)
METRIC_WORD = re.compile(r"\b(apy|apr|annual percentage (yield|rate)|dividend rate|interest rate|rate)\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\s+-\s+|\n")


def rows_from_passage(psg) -> list:
    out = []
    if "%" not in psg.text or psg.page.kind > PRODUCT_KIND + 1:     # rate sheet / product / promo only
        return out
    for sent in SENT.split(psg.text):
        if "%" not in sent or len(sent) > 260 or EXPIRED.search(sent) or not METRIC_WORD.search(sent):
            continue
        m = PCT.search(sent)
        if not m:
            continue
        label = sent[:m.start()].strip(" :-–—")
        label = re.sub(r"\b(apy|apr|is|of|at|annual percentage (yield|rate))\b", " ", label, flags=re.I)
        label = re.sub(r"\s+", " ", label).strip(" :-–—(")
        if len(label) < 3:
            continue
        low = sent.lower()
        metric = "apy" if re.search(r"apy|yield", low) else "apr" if re.search(r"\bapr\b|annual percentage rate", low) \
            else "dividend" if "dividend" in low else "rate"
        doc = f"{psg.heading} {label}"
        t = Table(page=psg.page, caption=[psg.heading], header=[], rows=[], effective=None, index=-1 - psg.pos)
        row = RateRow(table=t, label=label, display=label, values=[(metric, m.group(0).strip())],
                      concepts=concepts(doc, table_side=True) | ({"business"} if psg.page.path.startswith("/business") else set()),
                      terms=terms(label), amount=amount_range(label), model_years=model_year_range(label))
        row.key = (frozenset(row.concepts), frozenset(row.terms), row.amount, _norm_label_words(label), (metric,))
        out.append(row)
    return out
