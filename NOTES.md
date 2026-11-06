# NOTES — how the solution works

Submission folder: `solution/` (entry point `solution/solution.py`, `build()`).

```bash
python harness/run_eval.py --submission solution --eval eval/public_eval.jsonl
```

- **API keys:** none needed. The default is plain Python rules with no network or model calls. An optional LLM layer (DeepSeek, or a local Ollama model) can be switched on (see below).
- **Packages:** none. Standard library only (`requirements.txt` is empty on purpose). Runs on Python 3.9+.
- **Speed:** loading all five snapshots takes about 6 s. Each answer takes well under 0.1 s.
- **Consistency:** answers are deterministic and cached per (question, as_of).
- **Practice set:** 0.915 (starter: 0.093). The kit's selftest (`harness/selftest`, with its conflicts gold) scores 1.0.

## The idea in one paragraph

We keep **every snapshot**, not just the latest. Each page version is tagged with what kind of page it is and when its statements were true. Three kinds of things come out of each page. Rate tables become **rate rows**: product, term, tier, value, and the table's own "Rates are effective as of …" date. Fee tables, "Label: value" lines and address cards become **fact records**: subject, attribute, qualifier, value (`solution/facts.py`). Everything else becomes **passages** for keyword search. A question is answered from the snapshot that was live on its date. Explicit, one-line rules decide which of several conflicting statements wins. If no page actually states the answer, we say so.

## How a question is routed

1. **Rate question.** It asks for a rate, APY, APR, dividend or interest, or what a deposit account pays, *and* it names a product. Asking which terms, tiers or specials a product comes in counts too (rule O). These are answered from rate rows (`solution/rates.py`).
2. **Hours question.** These are answered from the "Lobby / Drive-up" hour blocks found on the site.
3. **Link question** ("what's the link / URL"). These are answered with the best official page.
4. **Fee, phone, address or routing-number question.** These are answered from fact records when one record's label covers the question (rule L). Otherwise they fall through to 5.
5. **Anything else.** These go to passage search: BM25 (a standard keyword-ranking formula) plus the rules below.

## Rules for deciding what is current (each one is one sentence)

The letters are the ones the code comments use. The rules are grouped by the kind of question they apply to; how many questions each one actually decides is measured in the ablation section below.

**Every kind of question**

| # | Rule |
|---|---|
| **A** | **Page authority:** rate sheet (`/rates`, `/business-rates`) > product page > promo landing page > calculator > downloaded document > news/blog article. |
| **D** | **Same row on several pages:** the copy with the newest "Rates are effective as of" (or fee-schedule) date wins, and ties go to the more authoritative page (rule A). On these five crawls the most authoritative page is always also the newest one, so D never changes an answer or a conflict resolution here (the calculator's 5.34%, effective 1/1/2026, loses to the rate sheet's 5.39%, effective 08/31/2026, under rule A as well); it is kept for the case where a product page is updated before the rate sheet, and it is the order `conflicts()` reports. |
| **F** | **as_of:** we answer from the newest snapshot taken on or before that date. With no `as_of`, we use the latest snapshot, unless the question itself names a past month ("as of May 2026", "what was … in June 2026"). |
| **G** | **Freshest statement wins among equally relevant passages:** an article is true as of its publish date; an undated article counts as old; a product page is true as of the crawl. This is how "22 branch office locations" (newest press release) beats "23" and "24" in older ones. |
| **I** | **Partial snapshots:** a snapshot missing more than 30% of the previous snapshot's pages is treated as an update, and the pages it doesn't mention are carried over. All five Dupaco snapshots are full crawls (each loses at most 8 pages), so this never fires on them. It matters for incremental feeds such as the kit's selftest. |
| **J** | **Names must be in the evidence:** a branch, program or product name in the question must appear in the passage or rate row we quote. Otherwise we say it isn't listed, instead of answering about something else ("Mullan Avenue lobby hours", "Super Saver account", "Visa Signature"). |

**Rate questions** (`solution/rates.py`)

| # | Rule |
|---|---|
| **B** | **Qualifiers are part of the fact:** a row only answers if its term, model-year range and balance or loan-amount range fit the question. Every product word in the question must be on the row. 48-month ≠ 60-month, jumbo ≠ standard, rate ≠ APY, Roth ≠ Traditional. |
| **C** | **Default tier:** variants the member didn't ask for (jumbo, IRA, business, refinance, intro or promotional, rate-relief) are left out when a plain version exists. |
| **E** | **Not published = say so:** if no row fits (7/1 ARM, construction loan, VA loan, student-loan rates, a special that left the rate sheet), we answer "isn't listed, please contact Dupaco" with `confident=False` and no numbers. |
| **M** | **A term makes a certificate:** a deposit row with a term in months and an APY is a certificate even if its label doesn't say so ("Regular IRAs — 60 Months" answers "60-month IRA certificate"). |
| **O** | **"Which terms / tiers / specials do you have" is a rate question:** it is answered with every row of the product's winning table (rule D), one per line with its rate, so the list is as complete as the rate sheet ("Regular Certificates — 60 Month: 1.80% APY; 48 Month: …"). |

**Fee, phone, address and routing-number questions** (`solution/facts.py`)

| # | Rule |
|---|---|
| **L** | **Structured before searched:** fees, phone numbers, addresses and the routing number are read from the page's own structure (table rows, "Label: value" lines, address cards) into records, and a question of that type is answered from the record whose label covers it; copies are ranked by rule D. If no record fits, the question goes to passage search. Example: "Incoming wire transfer \| $10.00" answers "What is the fee for receiving a wire?" and not the $25 domestic row. |
| **N** | **A fee nobody states isn't listed:** when a fee question matches no record and the best passage names no amount at all, we say so (`confident=False`) instead of quoting unrelated text. (Refusing whenever the best passage is the fee schedule was tried and dropped: it refused real fees whose wording the records missed.) |

**Counting questions**

| # | Rule |
|---|---|
| **H** | **"How many X":** we look at every passage that says "<number> … X" and group them by the wording after the number. The group that best matches the rest of the question wins. If the question adds nothing, the wording the site repeats most wins ("# branch office locations" beats "5,600 participating branches nationwide"). Then rule G applies. |

There was a rule K ("a rate found only on a promo, calculator or article page has ended when the newer rate sheet drops it"). The ablation showed it never fired: a product that left the rate sheet has no row at all in the latest snapshot, so rule E already refuses it. It was removed.

Smaller choices, all visible in the code:

- When a rate question doesn't say APY or APR, we print both the rate and the APY or APR. When it does say, we print only that metric.
- Plain "savings" means Share Savings. No test question depends on it; without it a bare "savings rate" question would list every savings product.
- A listing question ("what cards do you offer", "which certificates are available") keeps the business versions next to the personal ones instead of dropping them under rule C.
- Rates written in sentences ("6-month certificate APY: 3.50%") are used only when no table row fits. Every Dupaco rate is in a table, so this never fires here; the kit's selftest publishes its rates as sentences, and its "offer ended" promo line is skipped.
- Passage ranking multiplies BM25 by the page-kind weight and by the share of question words the passage covers. The first paragraphs of a page get a small boost, and very short button or nav labels are down-weighted.
- The quoted snippet is the smallest set of sentences that covers the question words. A word found in many sentences of the passage counts as the topic, not the answer.
- A question that asks how a rate *changed* ("what happened to … since May") gets the current value plus every earlier distinct value the same page showed, oldest first, each with its effective date. Every other question gets the current value only.
- Promo tables with two rate columns ("APY for 1st 6 Months" / "APY After Promotional Period", the Achieve Money Market landing page) keep the period of each value: an "introductory" question gets the first column, an "after the promotion" question the second, and a plain question both, labelled.
- Fact records: a row with no value is a section label for the rows under it ("Safe-Deposit Box"); the same schedule rendered as plain lines ("Official check $5.00* each", how the PDF comes through in the older snapshots) is read the same way; a fee schedule's date is read from its file name when the page has none; "largest / cheapest" picks among equally matching rows by amount; negations ("an ATM that isn't Dupaco's") add the word "non" so "Non-Dupaco ATM usage" matches. The organisation's own phone, address and routing number ("Phone: 800-…" on the contact page) answer when the question names nothing more specific.

## Conflicts (`conflicts()`)

1. **Rate rows.** The same row can show different values:
   - on different pages of the latest snapshot (stale calculator or promo copies), or
   - on its authoritative page in different snapshots (the rate changed).

   Rows are matched by product, term and tier. Balance tiers are matched by their dollar range. Model-year tiers are matched by rank, because their year labels shift: "2023–26" on the stale calculator lines up with "2024-27" on the rate sheet. The resolution is the value rule D picks today. The reason names the winning page and its effective date.
2. **Fact records** (fees, the routing number, phone numbers) whose copies disagree, under the same rule D. Addresses are left out: branch cards share one label, so different addresses are different branches, not a contradiction.
3. **Drifting article facts.** These are boilerplate sentences whose number changes over time ("… 23 branch office locations", "the fourth / fifth time Forbes recognized Dupaco"). The resolution is the newest article.

There are about 36 entries. We keep the list focused rather than listing every number, because the conflict score is an F1.

## Held-out questions (how we checked the fact records weren't tuned to the practice set)

The practice set has no fee questions and only two contact questions, so we wrote our own before changing the code: 39 seed questions on fees, branches, contact details, per-branch hours, "as of" dates and refusals, each expected string checked in the snapshots, plus 72 paraphrases of 36 of them generated with DeepSeek and Claude (test data only, not in the repo). The code was changed looking only at the seed misses; the paraphrases were scored before and after.

| Set | Rules before fact records | With fact records (rules L–N) |
|---|---|---|
| Practice set (40) | 0.915 | 0.915 |
| Seed questions (39) | 0.60 | 0.97 |
| Paraphrases (72) | 0.50 | 0.93 |
| 30 deliberately reworded passage questions | 9 / 30 | 10 / 30 |

What still misses: paraphrases whose wording drops the qualifier ("the cost to get a wire"), and words outside the site's vocabulary ("dormant", "notarized", "within the U.S."), which now get an "isn't listed" instead of a wrong fee.

A second round of 54 questions targeted what the hidden set announces: values that changed between snapshots (home-loan rates move in every snapshot, auto rates and the special certificates in the last ones), stale copies on promo and calculator pages, balance tiers, products that left the rate sheet (25-month special, HELOC intro rate), list completeness and the promo-only Achieve Money Market. Scored before any change: as-of 20/20, stale 8/8, tiers 13/13, gone 4/4, but lists 0/4, promo 2/3 and change history 1.5/2. Rule O, the promo-column periods and the fuller change history fixed those (54/54) with the practice set unchanged question by question.

## Which rules carry which questions (ablation)

Each rule was switched off on its own (patched in memory, nothing changed on disk) and all five question sets were re-run: the practice set (40), the seed questions (39), round 2 (54), the paraphrases (72) and 70 reworded probes, 275 questions in all. The table gives the number of questions that lose score when the rule is off. Switching a rule off never gained a question.

| Rule | Questions lost when off | Rule | Questions lost when off |
|---|---|---|---|
| B qualifiers | 50 (term / year / amount) + 35 (product words) | C default tier | 6 |
| F as_of | 37 | O list questions | 4 |
| L fact records | 30 | M a term makes a certificate | 3 |
| E not published = say so | 15 | H "how many" | 2 |
| A page authority | 6 | N unstated fee | 1 |
| J names in the evidence | 6 | D newest date | 0 here; the 36 conflicts and the selftest are unchanged without it |
| G freshest statement | 4 | I partial snapshots | 0 here; the kit's selftest drops from 1.0 to 0.83 without it |

Among the smaller choices, the question-word coverage factor (7 questions), the change-history list (5) and the same-page second passage (4) matter most; the promo periods, the "non" negation, the fee synonyms and the search synonyms each carry one to three held-out questions. Three things changed nothing on any set: rule K and a filter that dropped "Handpicked for You" teaser blocks, both removed, and the prose rates, which stay because the kit's selftest needs them. "What cards do you offer" used to be a special case that kept business cards (one practice question); it is now the general listing choice above, with every score unchanged.

## Known misses on the practice set

- **dup-081 (who can join):** the join page says "live or work within our charter area". The three states appear in a different section of the same page. A rule-based snippet doesn't connect the two.
- **dup-088 (Volunteer Time Off "last year"):** the newest article (April 2025) says 256 employees in 2024. The expected answer is the older article's 294 (2023). We follow rule G and did not special-case it.
- **dup-005:** has no `must_include`, so the scorer gives it 0 whatever we answer.

## What we borrowed

- **Rule J** comes from Patrick's `pdf-rag-chat-template` (the `terms` check in `app/confidence.py`: names in an answer must be in the cited passages). It is adapted here to refuse at retrieval time.
- **The optional LLM layer's guard rails** come from `pdf-rag-chat-template` (`app/rag.py`): the cite-or-refuse check and `ungrounded_numbers` (every figure must be in a cited passage), plus `expand` (widen a hit with its neighbouring chunks). The prompt follows its `SYSTEM_PROMPT`.
- **Optional hybrid retrieval** (`solution/embed.py`, off by default) follows `healthcare-agentic-rag`'s BM25 + dense idea. It uses a weighted sum instead of RRF (rank fusion), because the rules downstream compare score ratios. Cross-encoder reranking was not used, because it needs a model download.

## Optional LLM layer (DeepSeek or Ollama) — off by default

`solution/llm.py`. It is switched on by `DUPACO_LLM`, set in the environment or in a repo-root `.env` file (git ignores `.env`):

- `DUPACO_LLM=deepseek` plus `DEEPSEEK_API_KEY` (optional `DEEPSEEK_MODEL`, default `deepseek-chat`, and `DEEPSEEK_BASE_URL`). The `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` names from `pdf-rag-chat-template` also work, but only when `LLM_BASE_URL` points at `api.deepseek.com`, so a key meant for another provider is never sent there.
- `DUPACO_LLM=gemini` plus `GEMINI_API_KEY` (optional `GEMINI_MODEL`, default `gemini-3.8-flash`). It uses Google's OpenAI-compatible endpoint with thinking off.
- `DUPACO_LLM=anthropic` plus `ANTHROPIC_API_KEY` (optional `ANTHROPIC_MODEL`, default `claude-opus-5-5`). It uses the official `anthropic` SDK, which is imported only in this mode (`pip install anthropic`); the default run still needs no packages. Effort is set to `low`, because thinking can't be turned off on this model. Server-side refusal fallbacks are on, and a refusal or truncated reply falls back to the rule answer.
- `DUPACO_LLM=ollama` for a local model with no key (optional `OLLAMA_MODEL`, default `gemma4:12b`, and `OLLAMA_BASE_URL`). It uses Ollama's native `/api/chat` with thinking off.
- `DUPACO_LLM_TIMEOUT` (default 20 s).

A key alone does not switch it on. The graders' machine may have other model keys set, and the scored run should stay fast and deterministic unless we choose otherwise.

When it is on:

- It is used only for passage-search answers. Rates, hours, links and "not listed" refusals stay rule-based.
- The model sees the passages the rules already ranked, the rules' pick first. Each hit is widened with up to 3 neighbouring passages from the same page. Each passage is labelled with its page kind and the date it was true, and the model is told to apply rules A and G.
- It runs at temperature 0, with one call per question. Answers are cached per question, so a repeated question gets the same answer.
- The model's reply is **kept only if it cites a passage and every figure in it appears in the cited passages or the question.** Otherwise the rule answer is returned unchanged. The rule answer is also returned on `NOT_FOUND`, on a timeout (20 s) and on any error.
- When the question asks for a link, the links the rules found are appended, because the model tends to drop them. The rules' sources are kept alongside the model's citations.
- Only the standard library is used (`urllib`), so `requirements.txt` stays empty.

**Measured on the practice set (11-05):**

| Mode | Accuracy | p95 answer time | Speed score | Final |
|---|---|---|---|---|
| Rules only (default) | 0.915 | 0.05 s | 1.0 | **0.915** |
| + DeepSeek `deepseek-chat` | 0.915 | 2.42 s | 0.98 | 0.911 |
| + Claude `claude-opus-5-5` (effort low) | 0.915 | 2.38 s | 0.98 | 0.911 |
| + Ollama `gemma4:12b` (local Mac) | 0.868 | 20 s (timeouts) | 0.0 | 0.715 |

- **DeepSeek:**
  - It answered 15 of the 40 questions, taking 1.3–2.5 s each.
  - The answers read better: one sentence instead of a quoted paragraph. But the per-question scores are identical to the rules-only run.
  - It didn't fix dup-081 either. The three states were in its context, and it still wrote only "charter area".
  - The first DeepSeek run scored 0.886. The model dropped the join link on dup-082 and replaced the rules' sources on dup-054, which lost the source bonus. Keeping the rules' links and sources fixed both.
- **Claude Opus 5.5:** it rewrote 14 of the 15 passage answers, taking 1.2–3.1 s each. Like DeepSeek, its per-question scores are identical to the rules-only run, and it also wrote only "charter area" on dup-081.
- **Gemini `gemini-3.8-flash`:** not measured; the API returned 503 (high demand) throughout the test window.
- **Ollama:** about 4k tokens of context took 5–20 s per question on this Mac. It also lost two answers: "fifth time" on dup-087 and the join URL on dup-082.

So the submission runs with the layer **off**. It is documented as an option for when answers should read as prose rather than quoted passages.

## Optional embeddings (local Ollama) — off by default

`solution/embed.py` is switched on with `DUPACO_EMBED=ollama`. It uses `nomic-embed-text` and needs numpy, which is imported only in this mode. Without Ollama or numpy, search silently stays BM25-only.

- **How it works:** each unique passage is embedded once at ingest. That is about 14k texts and took about 200 s on a Mac; ingest isn't scored, and the limit is 30 minutes. A question costs one embedding call, about 20 ms.
- **When it is used:** only when the best BM25 passage covers less than 75% of the question's idf-weighted words, i.e. when the member's wording isn't the website's. The cosine score is then added at twice the best BM25 score's scale.

**Measured (11-05).** The probe set is 30 reworded passage questions I wrote, avoiding the site's words ("grocery-store branch" for Hy-Vee, "ABA number" for routing number):

| Mode | Reworded probes hit | Practice set |
|---|---|---|
| BM25 only (default) | 9 / 30 | 0.915 |
| + embeddings, always on (weight 1 / 2) | 12 / 30, 13 / 30 | 0.881, 0.858 |
| + embeddings, gated at 75% coverage (default when on) | 12 / 30 | 0.907 |

- **What helped:** dense search ranked the right passage in its top 5 for most reworded questions, including drive-up hours, notary fee, college loans and wire transfers.
- **What hurt:** it also promoted passages that are similar but wrong, such as "Galena Chrysler" for the Galena branch address and a different Forbes article.
- **Where else it loses:** the downstream steps are lexical. The name check, sentence picking and same-page second passage are all keyword-based, so a passage ranked first can still lose there.

It stays off: the graders' machine is unlikely to run Ollama, and the practice set drops a little.

## AI tools used

The code, the rule design and these notes were written with **Claude Code** (Anthropic), working from the assignment, the kit and the snapshot data.

## Files

| File | What it does |
|---|---|
| `solution/solution.py` | `build()`, ingest / answer / conflicts, question routing, hours / link / passage answers |
| `solution/kb.py` | page kinds, publish dates, cleaning, table + caption + effective-date parsing, passages |
| `solution/rates.py` | product concepts, qualifiers, rate rows, matching and the winner rule |
| `solution/facts.py` | fact records from tables, "Label: value" lines and address cards; fee / phone / address / routing answers |
| `solution/store.py` | per-snapshot index (passages + BM25, rate rows, hour blocks) |
| `solution/search.py` | tokenizer, synonyms, BM25 |
| `solution/conflicts.py` | the contradictions list |
| `solution/llm.py` | optional DeepSeek rewrite with grounding checks (off by default) |

The snapshot data is not in this repository (it may not be redistributed). Put the kit's `snapshots_zip/` next to `harness/`; the scorer unpacks it on its first run.
