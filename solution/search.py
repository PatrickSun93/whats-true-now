"""Tiny BM25 index (standard library only)."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

STOP = set("""a an the and or of to in on for at by with from as is are was were be been being it its this that
these those i me my we our you your he she they them their what which who whom whose when where why how do does did
doing done can could would should will shall may might must have has had having not no yes if then than so such
there here about into over under up down out off again also just only any all each some more most other much many
very too own same both few per via get got please tell know want like need us dupaco dupacos dupaco's s t""".split())

# Words members use -> the word the website uses (applied to questions and pages alike).
SYNONYMS = {
    "cd": "certificate", "cds": "certificate", "certificates": "certificate", "certs": "certificate",
    "car": "auto", "cars": "auto", "vehicle": "auto", "vehicles": "auto", "truck": "auto",
    "mortgages": "mortgage", "atm": "atm", "atms": "atm", "hrs": "hours", "drive-thru": "drive",
    "drivethru": "drive", "drivethrough": "drive", "telephone": "phone", "joining": "join",
    "e-mail": "email", "web": "online", "website": "online",
}

# Phrases rewritten before tokenising (questions only).
PHRASES = [
    (re.compile(r"\bbecome (?:a |an )?(?:dupaco )?member(?:-owner)?s?\b|\bsign up\b|\bopen (?:a |an )?membership\b", re.I), "join"),
    (re.compile(r"\bcertificates? of deposits?\b", re.I), "certificate"),
    (re.compile(r"\bcredit cards?\b", re.I), "credit card visa"),
]


def rewrite_question(q: str) -> str:
    for rx, rep in PHRASES:
        q = rx.sub(lambda m: m.group(0) + " " + rep, q)
    return q


TOKEN = re.compile(r"[a-z0-9]+(?:[$'][a-z0-9]+)*")


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith(("ches", "shes", "sses", "xes")):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    return w[:7]


def tokens(text: str, keep_stop: bool = False) -> list:
    out = []
    for w in TOKEN.findall((text or "").lower().replace("’", "'")):
        w = w.replace("'s", "") if w.endswith("'s") else w
        w = SYNONYMS.get(w, w)
        if not keep_stop and w in STOP:
            continue
        out.append(stem(w))
    return out


class BM25:
    def __init__(self, docs: list, k1: float = 1.2, b: float = 0.6):
        """docs: list of token lists."""
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / self.n) if self.n else 1.0
        self.post = defaultdict(list)       # term -> [(doc, tf)]
        for i, d in enumerate(docs):
            for t, c in Counter(d).items():
                self.post[t].append((i, c))
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}

    def search(self, q_tokens: list, top: int = 50) -> list:
        scores = defaultdict(float)
        for t in set(q_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in self.post[t]:
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
                scores[i] += idf * norm
        return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top]
