# NOTES — how the solution works

Submission folder: `solution/` (entry point `solution/solution.py`, `build()`).

```bash
python harness/run_eval.py --submission solution --eval eval/public_eval.jsonl
```

- **API keys:** none needed. The default is plain Python rules with no network or model calls. An optional LLM layer (DeepSeek, or a local Ollama model) can be switched on (see below).
- **Packages:** none. Standard library only (`requirements.txt` is empty on purpose). Runs on Python 3.9+.
- **Speed:** loading all five snapshots takes about 4 s. Each answer takes well under 0.1 s.
- **Consistency:** answers are deterministic and cached per (question, as_of).
- **Practice set:** 0.915 (starter: 0.093). The kit's selftest (`harness/selftest`, with its conflicts gold) scores 1.0.

## The idea in one paragraph

We keep **every snapshot**, not just the latest. Each page version is tagged with what kind of page it is and when its statements were true. Two kinds of facts come out of each page. Rate tables become **rate rows**: product, term, tier, value, and the table's own "Rates are effective as of …" date. Everything else becomes **passages** for keyword search. A question is answered from the snapshot that was live on its date. Explicit, one-line rules decide which of several conflicting statements wins. If no page actually states the answer, we say so.

## How a question is routed

1. **Rate question.** It asks for a rate, APY, APR, dividend or interest, or what a deposit account pays, *and* it names a product. These are answered from rate rows (`solution/rates.py`).
2. **Hours question.** These are answered from the "Lobby / Drive-up" hour blocks found on the site.
3. **Link question** ("what's the link / URL"). These are answered with the best official page.
4. **Anything else.** These go to passage search: BM25 (a standard keyword-ranking formula) plus the rules below.

## Rules for deciding what is current (each one is one sentence)

| # | Rule |
|---|---|
| **A** | **Page authority:** rate sheet (`/rates`, `/business-rates`) > product page > promo landing page > calculator > downloaded document > news/blog article. |
| **B** | **Qualifiers are part of the fact:** a row only answers if its term, model-year range and balance or loan-amount range fit the question. Every product word in the question must be on the row. 48-month ≠ 60-month, jumbo ≠ standard, rate ≠ APY, Roth ≠ Traditional. |
| **C** | **Default tier:** variants the member didn't ask for (jumbo, IRA, business, refinance, intro or promotional, rate-relief) are left out when a plain version exists. Plain "savings" means Share Savings. "What cards do you offer" includes business cards. |
| **D** | **Same row on several pages:** the table with the newest "Rates are effective as of" date wins. Ties go to the more authoritative page (rule A). Example: the calculator still shows 5.34% (effective 1/1/2026); the rate sheet shows 5.39% (effective 08/31/2026), so we answer 5.39%. |
| **E** | **Not published = say so:** if no row fits (7/1 ARM, construction loan, VA loan, student-loan rates), we answer "isn't listed, please contact Dupaco" with `confident=False` and no numbers. |
| **F** | **as_of:** we answer from the newest snapshot taken on or before that date. With no `as_of`, we use the latest snapshot, unless the question itself names a past month ("as of May 2026", "what was … in June 2026"). |
| **G** | **Freshest statement wins among equally relevant passages:** an article is true as of its publish date; an undated article counts as old; a product page is true as of the crawl. This is how "22 branch office locations" (newest press release) beats "23" and "24" in older ones. |
| **H** | **"How many X":** we look at every passage that says "<number> … X" and group them by the wording after the number. The group that best matches the rest of the question wins. If the question adds nothing, the wording the site repeats most wins ("# branch office locations" beats "5,600 participating branches nationwide"). Then rule G applies. |
| **I** | **Partial snapshots:** a snapshot missing more than 30% of the previous snapshot's pages is treated as an update, and the pages it doesn't mention are carried over. All five Dupaco snapshots are full crawls (each loses at most 8 pages), so this never fires on them. It matters for incremental feeds such as the kit's selftest. |
| **J** | **Names must be in the evidence:** a branch, program or product name in the question must appear in the passage or rate row we quote. Otherwise we say it isn't listed, instead of answering about something else ("Mullan Avenue lobby hours", "Super Saver account", "Visa Signature"). |
| **K** | **Expired offers:** a rate that appears only on a promo, calculator or article page is treated as ended when the official rate sheet for that product family is newer and no longer lists it. |

Smaller choices, all visible in the code:

- When a rate question doesn't say APY or APR, we print both the rate and the APY or APR. When it does say, we print only that metric.
- Rates written in sentences ("6-month certificate APY: 3.50%") are used only when no table row fits.
- "Handpicked for You" teaser blocks are dropped, because they quote other, often old, articles.
- Passage ranking multiplies BM25 by the page-kind weight and by the share of question words the passage covers. The first paragraphs of a page get a small boost, and very short button or nav labels are down-weighted.
- The quoted snippet is the smallest set of sentences that covers the question words. A word found in many sentences of the passage counts as the topic, not the answer.

## Conflicts (`conflicts()`)

1. **Rate rows.** The same row can show different values:
   - on different pages of the latest snapshot (stale calculator or promo copies), or
   - on its authoritative page in different snapshots (the rate changed).

   Rows are matched by product, term and tier. Balance tiers are matched by their dollar range. Model-year tiers are matched by rank, because their year labels shift: "2023–26" on the stale calculator lines up with "2024-27" on the rate sheet. The resolution is the value rule D picks today. The reason names the winning page and its effective date.
2. **Drifting article facts.** These are boilerplate sentences whose number changes over time ("… 23 branch office locations", "the fourth / fifth time Forbes recognized Dupaco"). The resolution is the newest article.

There are about 36 entries. We keep the list focused rather than listing every number, because the conflict score is an F1.

## Known misses on the practice set

- **dup-081 (who can join):** the join page says "live or work within our charter area". The three states appear in a different section of the same page. A rule-based snippet doesn't connect the two.
- **dup-088 (Volunteer Time Off "last year"):** the newest article (April 2025) says 256 employees in 2024. The expected answer is the older article's 294 (2023). We follow rule G and did not special-case it.
- **dup-005:** has no `must_include`, so the scorer gives it 0 whatever we answer.

## What we borrowed

- **Rule J** comes from Patrick's `pdf-rag-chat-template` (the `terms` check in `app/confidence.py`: names in an answer must be in the cited passages). It is adapted here to refuse at retrieval time.
- **The optional LLM layer's guard rails** come from `pdf-rag-chat-template` (`app/rag.py`): the cite-or-refuse check and `ungrounded_numbers` (every figure must be in a cited passage), plus `expand` (widen a hit with its neighbouring chunks). The prompt follows its `SYSTEM_PROMPT`.
- Not used, and why: dense or hybrid retrieval with RRF (rank fusion) and cross-encoder reranking, from `healthcare-agentic-rag`. These need a local embedding model download or an API key.

## Optional LLM layer (DeepSeek or Ollama) — off by default

`solution/llm.py`. It is switched on by `DUPACO_LLM`, set in the environment or in a repo-root `.env` file (git ignores `.env`):

- `DUPACO_LLM=deepseek` plus `DEEPSEEK_API_KEY` (optional `DEEPSEEK_MODEL`, default `deepseek-chat`, and `DEEPSEEK_BASE_URL`). The `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` names from `pdf-rag-chat-template` also work, but only when `LLM_BASE_URL` points at `api.deepseek.com`, so a key meant for another provider is never sent there.
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
| + Ollama `gemma4:12b` (local Mac) | 0.868 | 20 s (timeouts) | 0.0 | 0.715 |

- **DeepSeek:**
  - It answered 15 of the 40 questions, taking 1.3–2.5 s each.
  - The answers read better: one sentence instead of a quoted paragraph. But the per-question scores are identical to the rules-only run.
  - It didn't fix dup-081 either. The three states were in its context, and it still wrote only "charter area".
  - The first DeepSeek run scored 0.886. The model dropped the join link on dup-082 and replaced the rules' sources on dup-054, which lost the source bonus. Keeping the rules' links and sources fixed both.
- **Ollama:** about 4k tokens of context took 5–20 s per question on this Mac. It also lost two answers: "fifth time" on dup-087 and the join URL on dup-082.

So the submission runs with the layer **off**. It is documented as an option for when answers should read as prose rather than quoted passages.

## AI tools used

The code, the rule design and these notes were written with **Claude Code** (Anthropic), working from the assignment, the kit and the snapshot data.

## Files

| File | What it does |
|---|---|
| `solution/solution.py` | `build()`, ingest / answer / conflicts, question routing, hours / link / passage answers |
| `solution/kb.py` | page kinds, publish dates, cleaning, table + caption + effective-date parsing, passages |
| `solution/rates.py` | product concepts, qualifiers, rate rows, matching and the winner rule |
| `solution/store.py` | per-snapshot index (passages + BM25, rate rows, hour blocks) |
| `solution/search.py` | tokenizer, synonyms, BM25 |
| `solution/conflicts.py` | the contradictions list |
| `solution/llm.py` | optional DeepSeek rewrite with grounding checks (off by default) |

The snapshot data is not in this repository (it may not be redistributed). Put the kit's `snapshots_zip/` next to `harness/`; the scorer unpacks it on its first run.
