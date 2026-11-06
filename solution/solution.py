"""
Dupaco knowledge system — rule-based, standard library only, no API keys.

    ks = build()
    ks.ingest(snapshot)            # once per snapshot, oldest first: we index EVERY snapshot
    ks.answer(question, as_of)     # answer from the snapshot that was live on `as_of`
    ks.conflicts()                 # contradictions across pages and across time

How a question is routed (see NOTES.md for the full rule list):
  1. rate question (asks for a rate/APY/APR and names a product) -> rate tables   (rates.py)
  2. "what are the hours"                                          -> hours blocks
  3. "what's the link / URL"                                       -> best official page
  4. everything else                                               -> passage search (BM25)
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))

from contract import Answer, Conflict, Snapshot, Source  # noqa: E402

import conflicts as conflict_mod  # noqa: E402
import embed  # noqa: E402
import kb  # noqa: E402
import llm  # noqa: E402
import rates  # noqa: E402
import store  # noqa: E402
from search import rewrite_question, tokens  # noqa: E402

NOT_LISTED = ("I'm sorry — that isn't listed on Dupaco's website, so I can't confirm it. "
              "Please contact Dupaco directly and a team member can help.")

# Passage-search weights per page kind (rule A): official pages are trusted more than articles.
KIND_WEIGHT = {kb.RATE_SHEET: 1.0, kb.PRODUCT: 1.0, kb.PROMO: 0.8, kb.CALCULATOR: 0.6, kb.DOCUMENT: 0.75,
               kb.ARTICLE: 0.85}


class DupacoKnowledge:
    def __init__(self):
        self.snaps = []          # SnapshotIndex, oldest first
        self._last_pages = {}
        self._cache = {}
        self._conflicts = None

    # ------------------------------------------------------------------ ingest
    def ingest(self, snapshot: Snapshot) -> None:
        # Rule I: a snapshot missing more than 30% of the previous one's pages is a partial
        # update, so pages it doesn't mention are carried over. (Every Dupaco snapshot is a
        # full crawl, losing at most 8 pages, so nothing is carried over there.)
        prev = self._last_pages
        urls = {p.url.rstrip("/") for p in snapshot.pages}
        if prev:
            missing = [p for u, p in prev.items() if u not in urls]
            if len(missing) > 0.3 * len(prev):
                snapshot = Snapshot(snapshot.snapshot_id, snapshot.taken_on, list(snapshot.pages) + missing)
        self._last_pages = {p.url.rstrip("/"): p for p in snapshot.pages}
        self.snaps.append(store.build_index(snapshot))
        self.snaps.sort(key=lambda s: s.taken_on)
        self._cache.clear()
        self._conflicts = None

    # ------------------------------------------------------------------ as_of
    def _snapshot_for(self, as_of: Optional[str], question: str):
        """Rule F: as_of -> the newest snapshot taken on or before that date (the website as it
        stood then). No as_of -> the latest snapshot, unless the question itself names a past
        month ("as of May 2026", "in June 2026")."""
        if not self.snaps:
            return None
        when = None
        if as_of:
            try:
                when = date.fromisoformat(str(as_of)[:10])
            except ValueError:
                when = None
        if when is None:
            when = question_date(question)
        if when is None:
            return self.snaps[-1]
        older = [s for s in self.snaps if s.taken_on <= when]
        return older[-1] if older else self.snaps[0]

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        key = (question.strip().lower(), as_of)
        if key not in self._cache:
            snap = self._snapshot_for(as_of, question)
            if snap is None:
                self._cache[key] = Answer(text=NOT_LISTED, confident=False)
            else:
                self._cache[key] = route(question, snap)
        return self._cache[key]

    # ------------------------------------------------------------------ conflicts
    def conflicts(self) -> list:
        if self._conflicts is None:
            self._conflicts = conflict_mod.find_conflicts(self.snaps)
        return self._conflicts


def build():
    return DupacoKnowledge()


# =============================================================================================
# Routing
# =============================================================================================
MONTH_RX = r"(january|february|march|april|may|june|july|august|september|october|november|december)"


def question_date(q: str) -> Optional[date]:
    low = q.lower()
    m = re.search(r"\b(?:as of|in|on|back in|during)\s+(?:early |mid |late )?" + MONTH_RX + r"\s+(\d{1,2},\s+)?(20\d\d)\b", low)
    if not m or not re.search(r"\b(was|were|did|had|used to)\b|as of", low):
        return None
    mo = kb.MONTHS[m.group(1)]
    y = int(m.group(3))
    if m.group(2):
        return date(y, mo, int(m.group(2).strip(" ,")))
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return nxt - timedelta(days=1)


HOURS_Q = re.compile(r"\bhours?\b|what time|\bopen (?:on|until|till)\b", re.I)
URL_Q = re.compile(r"\b(link|url|web ?page|web address|website address)\b", re.I)


def route(q: str, snap) -> Answer:
    if rates.is_rate_question(q) and not URL_Q.search(q):
        return rate_answer(q, snap)
    if HOURS_Q.search(q) and not re.search(r"\bhour(s)? (of|in)\b", q, re.I):
        return hours_answer(q, snap)
    if URL_Q.search(q):
        return url_answer(q, snap)
    return text_answer(q, snap)


# =============================================================================================
# Rates
# =============================================================================================
def rate_answer(q: str, snap) -> Answer:
    rq = rates.parse_query(q)
    res = rates.select(snap.rate_rows, rq) or rates.select(snap.prose_rows, rq)
    if res is None:
        return Answer(text="I don't see a published rate for that on Dupaco's website, so I can't quote one. "
                           "Please contact Dupaco directly and a lender can give you a current quote.",
                      confident=False)
    # Rule J for rates: every product name the member used must be on the rows we quote
    names = [n.lower() for n in entity_words(q) if len(n) > 2 and not n.isupper()]
    shown = " ".join(r.display + " " + " ".join(r.table.caption) for r in res.rows).lower()
    if any(n not in shown for n in names):
        return Answer(text="I don't see a published rate for that on Dupaco's website, so I can't quote one. "
                           "Please contact Dupaco directly for current details.", confident=False)
    # Rule K (expired offer): a product found only on a promo / calculator / article page, while
    # the official rate sheet for the same family is newer and no longer lists it, has ended.
    t = res.winner_table
    if t.page.kind > kb.PRODUCT:
        fam = (rq.products or set(res.rows[0].concepts)) & rates.PRODUCT_NAMES
        newer = [x.table for x in snap.rate_rows if x.page.kind <= kb.PRODUCT and fam <= x.concepts
                 and x.effective and (not t.effective or x.effective > t.effective)]
        if newer:
            when = f" (effective {t.effective.strftime('%m/%d/%Y')})" if t.effective else ""
            return Answer(text="That rate isn't on Dupaco's current rate sheet anymore — it only appears on an older "
                               f"page{when}, so it may have ended. Please contact Dupaco to confirm current options.",
                          sources=[Source(url=newer[0].page.url, snapshot_id=snap.snapshot_id)], confident=False,
                          conflict_note=f"Only {kb.KIND_NAMES[t.page.kind]} {t.page.url} lists it.")
    parts = [f"{r.display}: {r.value_str(rq.metrics)}" for r in res.rows]
    eff = f" (rates effective {t.effective.strftime('%m/%d/%Y')})" if t.effective else ""
    text = "; ".join(parts) + eff + "."
    pages = []
    for u in [r.page.url for r in res.rows] + res.agreeing_pages:
        if u not in pages:
            pages.append(u)
    # product pages first: they are the pages members are pointed to
    pages.sort(key=lambda u: (kb.page_kind(u) != kb.PRODUCT, kb.page_kind(u)))
    srcs = [Source(url=u, snapshot_id=snap.snapshot_id) for u in pages[:5]]
    note = None
    if res.disagreements:
        r, o = res.disagreements[0]
        note = (f"Other pages disagree, e.g. {kb.KIND_NAMES[o.page.kind]} {o.page.url} shows {o.value_str()} "
                f"(effective {o.effective.strftime('%m/%d/%Y') if o.effective else 'not stated'}); "
                f"used {rates.describe_source(r.table)} because it is newer / more authoritative.")
    return Answer(text=text, sources=srcs, confident=True, conflict_note=note)


# =============================================================================================
# Hours
# =============================================================================================
GENERIC_CAPS = {"dupaco", "lobby", "drive", "drive-up", "what", "when", "where", "do", "does", "are", "is", "can",
                "could", "the", "i", "how", "which", "all", "branch", "branches", "hours", "saturday", "sunday",
                "monday", "friday", "atm", "give", "tell", "please", "hi", "hello", "dupaco's", "you", "your", "my",
                "me", "general", "credit", "union", "office", "today", "tuesday", "wednesday", "thursday",
                "january", "february", "march", "april", "may", "june", "july", "august", "september",
                "october", "november", "december", "sept", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
                "oct", "nov", "dec", "now", "currently", "still", "also", "and", "or", "if", "for"}


def entity_words(q: str) -> list:
    """Capitalised words that aren't question words: a branch / place / product name."""
    words = re.findall(r"[A-Za-z][A-Za-z'’.-]*", q)
    out = []
    for i, w in enumerate(words):
        acronym = w.rstrip("s").isupper() or w.isupper()          # APRs, CDs, IRA, HSA
        if w[0].isupper() and not acronym and w.lower().strip(".'’") not in GENERIC_CAPS and not (i == 0):
            out.append(w.strip(".'’"))
    return out


def hours_answer(q: str, snap) -> Answer:
    low = q.lower()
    kinds = ["drive-up"] if re.search(r"drive|window", low) else ["lobby"]
    if re.search(r"branch hours|both", low) and "lobby" not in low:
        kinds = ["lobby", "drive-up"]
    ents = entity_words(q)
    blocks = [b for b in snap.hours if b.kind in kinds]
    if ents:
        want = [e.lower() for e in ents]
        blocks = [b for b in blocks if all(w in b.context.lower() for w in want)]
        if not blocks:
            return Answer(text=f"I don't have posted hours for {' '.join(ents)} on Dupaco's website, so I "
                               "can't confirm them. Please check the branch locator or contact Dupaco directly.",
                          sources=[Source(url="https://www.dupaco.com/contact", snapshot_id=snap.snapshot_id)],
                          confident=False)
    if not blocks:
        return text_answer(q, snap)
    out = []
    for kind in kinds:
        kb_ = [b for b in blocks if b.kind == kind]
        if not kb_:
            continue
        counts = {}
        for b in kb_:
            counts.setdefault(b.render(), []).append(b)
        order = sorted(counts.items(), key=lambda kv: (-len(kv[1]), -max(x.page.freshness for x in kv[1]).toordinal(),
                                                     kv[0]))
        label = "Lobby" if kind == "lobby" else "Drive-up"
        if ents or len(order) == 1:
            out.append(f"{label} hours: {order[0][0]}.")
        else:
            alts = " | ".join(s for s, _ in order[1:5])
            out.append(f"{label} hours vary by branch. Most branches: {order[0][0]}. Other branches: {alts}.")
        srcs_blocks = [b for _, bs in order[:5] for b in bs[:1]]
    if not ents:
        out.append("Which branch would you like? I can give you that branch's exact hours.")
    srcs = [Source(url="https://www.dupaco.com/contact", snapshot_id=snap.snapshot_id)]
    srcs += [Source(url=b.page.url, snapshot_id=snap.snapshot_id) for b in srcs_blocks[:4]]
    return Answer(text=" ".join(out), sources=srcs, confident=True)


# =============================================================================================
# Links
# =============================================================================================
def url_answer(q: str, snap) -> Answer:
    qq = URL_Q.sub(" ", q)
    urls = []
    if rates.is_rate_question(qq):
        res = rates.select(snap.rate_rows, rates.parse_query(qq))
        if res:
            pages = [r.page for r in res.rows] + [p for p in snap.pages if p.url in res.agreeing_pages]
            pages = [p for p in pages if p.kind in (kb.PRODUCT, kb.RATE_SHEET)]
            pages.sort(key=lambda p: (p.kind != kb.PRODUCT, p.kind, len(p.path)))
            for p in pages:
                u = p.url.rstrip("/")
                if u not in urls:
                    urls.append(u)
    if not urls:
        hits = snap.page_bm25.search(tokens(qq), top=10)
        cands = [(s * (1.0 if snap.page_docs[i].kind <= kb.PRODUCT else 0.7), snap.page_docs[i]) for i, s in hits]
        cands.sort(key=lambda x: (-x[0], len(x[1].path)))
        urls = [p.url.rstrip("/") for _, p in cands[:2]]
    if not urls:
        return Answer(text=NOT_LISTED, confident=False)
    text = f"You can find that here: {urls[0]}"
    if len(urls) > 1:
        text += f" (also see {urls[1]})"
    return Answer(text=text + ".", sources=[Source(url=u, snapshot_id=snap.snapshot_id) for u in urls[:3]])


# =============================================================================================
# Everything else: passage search
# =============================================================================================
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
WANT_URLS = re.compile(r"\b(online|link|apply|join|sign up|enroll|download|app|where can i)\b", re.I)


def _best_sentences(text: str, qt: set, idf: dict, limit: int = 420, context: str = "") -> str:
    """Shortest run of sentences that covers the question words this passage contains:
    take the best sentence, then add the sentence covering the most still-missing words.
    Words already in the passage's heading are its topic, not the answer, so they don't count
    (unless nothing else is left)."""
    sents = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    if len(text) <= limit or len(sents) <= 1:
        return text[:max(limit, 600)]
    ctx = set(tokens(context))
    q_focus = (qt - ctx) if any(t in set(tokens(text)) for t in qt - ctx) else qt
    toks = [set(tokens(s)) & q_focus for s in sents]
    present = set().union(*toks)
    # a question word found in many of this passage's sentences is its topic; one found in a
    # single sentence points at the answer ("late fee" vs "Platinum Visa")
    sdf = {t: sum(1 for ts in toks if t in ts) for t in present}
    w = {t: idf.get(t, 0) / sdf[t] for t in present}
    picked, covered = [], set()
    while len(picked) < 3:
        gain = [(sum(w[t] for t in toks[i] - covered), -i, i) for i in range(len(sents)) if i not in picked]
        if not gain or gain and max(gain)[0] <= 0:
            break
        g, _, i = max(gain)
        if picked and sum(len(sents[j]) for j in picked) + len(sents[i]) > limit + 180:
            break
        picked.append(i)
        covered |= toks[i]
        if covered >= present:
            break
    if not picked:
        picked = [0]
    picked.sort()
    out = " ".join(sents[i] for i in picked)
    # keep a short follow-on sentence (often the actual value: "This was the fifth time...")
    nxt = picked[-1] + 1
    if nxt < len(sents) and len(out) + len(sents[nxt]) < limit * 0.6:
        out += " " + sents[nxt]
    return out


ACCOUNT_SERVICE = re.compile(r"\b(update|change|reset|close|replace|report|dispute|order|add)\b.*\b(my|account|card|"
                             r"address|contact|information|info|phone|email|password)\b", re.I)
MEMBER_SERVICE = ("For help with your own account you can log in to Shine Online & Mobile Banking or call "
                  "Dupaco at 800-373-7600.")


def _rank_passages(qt: list, snap, top: int = 80, q: str = "") -> list:
    """BM25, times page-kind weight (rule A), times the share of question words the passage
    covers (so one word repeated many times doesn't win), with a small bonus for a page's
    opening paragraphs (they summarise the page). With optional embeddings (embed.py) the
    passages most similar to the question add a cosine score on top, scaled to the best BM25 score."""
    q_set = set(qt)
    idf = snap.bm25.idf
    q_w = sum(idf.get(t, 0) for t in q_set) or 1.0

    def shape(p):
        lede = 1.3 if p.pos <= 2 else 1.0
        stub = 0.5 if len(p.text) < 50 and not re.search(r"\d", p.text) else 1.0   # button / nav labels
        return KIND_WEIGHT[p.page.kind] * lede * stub

    scores, best_cover = {}, 0.0
    for i, s in snap.bm25.search(qt, top=top):
        p = snap.passages[i]
        pt = set(tokens(p.heading + " " + p.text))
        cover = sum(idf.get(t, 0) for t in q_set & pt) / q_w
        best_cover = max(best_cover, cover)
        scores[i] = s * (0.4 + 0.6 * cover) * shape(p)
    # embeddings only when no passage uses most of the question's own words (a reworded question)
    if q and snap.dense_rows is not None and best_cover < embed.min_cover():
        dense = embed.search(q, snap.dense_rows)
        if len(dense) > 1:
            best = max(scores.values(), default=1.0)
            hi, lo = dense[0][1], dense[-1][1]
            for i, c in dense:
                scores[i] = scores.get(i, 0.0) + embed.weight() * best * (c - lo) / (hi - lo + 1e-9) * shape(snap.passages[i])
    return [(s, snap.passages[i]) for i, s in scores.items()]


def _count_candidates(noun: str, qt: list, snap, cands: list) -> list:
    """'How many X' (rule H): look at every passage that states '<number> ... X'. Group them by
    the wording that follows the number ('# branch office locations' vs '# participating
    branches nationwide'). The group that best matches the REST of the question wins; if the
    rest says nothing ("how many branches do you have?") the wording the site repeats most wins.
    The winning group then goes through rule G (freshest statement wins)."""
    rx = re.compile(r"(?<![\d,.$])(\d{1,4}(?:,\d{3})?)\+?\s+((?:[A-Za-z’'-]+\s+){0,2}" + re.escape(noun[:5]) +
                    r"[a-z]*(?:\s+[a-z]+){0,2})")
    noun_t = set(tokens(noun))
    other = [t for t in qt if t not in noun_t]
    rel = dict(snap.bm25.search(other, top=5000)) if other else {}
    groups = {}
    for i, p in enumerate(snap.passages):
        mm = rx.search(p.text)
        if mm:
            groups.setdefault(mm.group(2).lower(), []).append((rel.get(i, 0.0), p))
    if not groups:
        return cands
    best = max(groups.values(), key=lambda g: (sum(s for s, _ in g) + 0.01 * len(g), len(g)))
    return [(s + 1.0, p) for s, p in best]


def _passages_naming(names: list, qt: list, snap) -> list:
    """Passages anywhere in the snapshot that contain every name, ranked by the question."""
    first = names[0]
    hits = [i for i, p in enumerate(snap.passages)
            if all(n in (p.heading + " " + p.text + " " + p.page.title).lower() for n in names)]
    if not hits:
        return []
    rel = dict(snap.bm25.search(qt, top=len(snap.passages)))
    out = [((rel.get(i, 0.0) + 0.1) * KIND_WEIGHT[snap.passages[i].page.kind], snap.passages[i]) for i in hits]
    return sorted(out, key=lambda x: -x[0])[:80] if first else out


def text_answer(q: str, snap) -> Answer:
    qt = tokens(rewrite_question(q))
    if not qt:
        return Answer(text=NOT_LISTED, confident=False)
    cands = _rank_passages(qt, snap, q=q)
    if not cands:
        return Answer(text=NOT_LISTED, confident=False)
    # Rule J (from Patrick's pdf-chat-poc "terms" check): a name in the question (a branch, a
    # program, a product) must appear in the evidence. Keep only passages that contain every name;
    # if no passage on the site does, say it isn't listed rather than answer about something else.
    names = [n.lower() for n in entity_words(q) if len(n) > 2 and not n.isupper()]
    if names:
        named = [(s, p) for s, p in cands if all(n in (p.heading + " " + p.text + " " + p.page.title).lower() for n in names)]
        if not named:
            named = _passages_naming(names, qt, snap)
        if not named:
            return Answer(text=NOT_LISTED, confident=False)
        cands = named
    m = re.search(r"\bhow many\s+(?:of\s+)?(?:dupaco'?s?\s+|your\s+|the\s+)?([a-z-]+)", q.lower())
    if m and m.group(1) not in ("times", "time", "years", "year"):
        cands = _count_candidates(m.group(1), qt, snap, cands)
    top = max(s for s, _ in cands)
    # Rule G: among passages about equally relevant, the freshest statement wins (a product page
    # is "as of the crawl"; an article is as of its publish date).
    pool = [(s, p) for s, p in cands if s >= 0.8 * top]
    pool.sort(key=lambda x: (-x[1].page.freshness.toordinal(), -x[0], x[1].page.kind, x[1].page.url))
    best_s, best = pool[0]
    idf = snap.bm25.idf
    want_urls = bool(WANT_URLS.search(q))

    def body(p):
        b = p.text_urls if want_urls else p.text
        if want_urls:
            b = re.sub(r"\((https?://[^)\s]*\d[^)\s]*)\)", "", b)   # drop dated/opaque URLs
        return b

    snippet = _best_sentences(body(best), set(qt), idf, context=best.heading)
    # a second strong passage from the same page often completes the answer
    same = [(s, p) for s, p in cands if p.page is best.page and p is not best and s >= 0.6 * best_s]
    if same:
        other = max(same, key=lambda x: x[0])[1]
        extra = _best_sentences(body(other), set(qt), idf, limit=380, context=other.heading)
        if extra not in snippet:
            snippet = snippet + " " + extra
    if ACCOUNT_SERVICE.search(q):
        snippet = snippet + " " + MEMBER_SERVICE
    srcs = [Source(url=best.page.url, snapshot_id=snap.snapshot_id, quote=snippet[:200])]
    for s, p in pool[1:]:
        if len(srcs) >= 3:
            break
        if p.page.url not in [x.url for x in srcs]:
            srcs.append(Source(url=p.page.url, snapshot_id=snap.snapshot_id))
    if llm.enabled():
        better = _llm_rewrite(q, best, cands, snap)
        if better:
            # keep what the rules found: their links (the model tends to drop them) and their sources
            links = [u for u in dict.fromkeys(LINK.findall(snippet)) if u not in better.text][:3] if want_urls else []
            text = better.text + (" Links: " + ", ".join(links) if links else "")
            merged = list({x.url: x for x in better.sources + srcs}.values())[:4]
            return Answer(text=text, sources=merged, confident=True)
    return Answer(text=snippet, sources=srcs, confident=True)


LINK = re.compile(r"https?://[^\s)\]]+")


def _llm_rewrite(q: str, best, cands: list, snap) -> Optional[Answer]:
    """Optional (llm.py): let the model (DeepSeek or local Ollama) phrase the answer from the passages the rules ranked,
    the rules' pick first. Ungrounded or uncited replies are discarded by llm.grounded_answer."""
    chosen, seen = [best], {id(best)}
    for s, p in sorted(cands, key=lambda x: -x[0]):
        if len(chosen) >= 6:
            break
        if id(p) not in seen and p.text not in {c.text for c in chosen}:
            chosen.append(p)
            seen.add(id(p))
    # widen each hit with up to 3 passages either side on the same page and merge windows that
    # touch (pdf-rag-chat-template's `expand`), so a fact one section away comes along
    windows = []                                # [page, set(pos)]
    for p in chosen:
        span = set(range(max(0, p.pos - 3), p.pos + 4))
        hit = next((w for w in windows if w[0] is p.page and min(w[1]) - 1 <= max(span) and min(span) <= max(w[1]) + 1), None)
        if hit:
            hit[1] |= span
        else:
            windows.append([p.page, span])
    items = []
    for page, span in windows:
        text = " ".join(x.text for x in page.passages if x.pos in span)[:2000]
        when = (f"published {page.pub_date.isoformat()}" if page.pub_date else
                "undated article" if page.kind == kb.ARTICLE else f"live on {page.taken_on.isoformat()}")
        items.append((f"{kb.KIND_NAMES[page.kind]}, {page.url}, {when}", text, page.url))
    out = llm.grounded_answer(q, items)
    if not out:
        return None
    text, urls = out
    return Answer(text=text, sources=[Source(url=u, snapshot_id=snap.snapshot_id) for u in urls[:3]], confident=True)
