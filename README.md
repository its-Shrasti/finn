# FINN: Financial Investment Agent

FINN filters what reaches you. It reads every filing across a 15-stock portfolio and folds away about 70% of them. What remains is sorted into "needs you" and "worth knowing". Two things get special treatment. A results filing comes with a first cut of the numbers. A leadership change comes with the incoming person's background. On the pull side, FINN reads a company's last four earnings calls and scores each management promise against the reported numbers.

Underneath both sits the **Network**: who in your own network can help you understand this. A CEO change comes with your best way in to the new CEO. An acquisition or a court ruling names the one person best placed to explain it. The network is synthetic in this demo (31 fictional people, labelled SYN), and every link is scored in code from shared employers, years and batches.

**Try it on a phone:** https://finn-dist.vercel.app (or open `app/dist/index.html`). A "Start here" card on first open runs a three-stop tour: HDFC Bank's way in, Network coverage, and Kaynes' promise ledger. Replay it from the Data sheet.

```
app/
  index.html          the mobile app: one file, no build step, no framework
  snapshot.json       shared market data the app renders (same for every user)
  network.json        the investor's private network overlay (per user; never merged into the snapshot)
  dist/index.html     the app with both inlined, ready for any static host
backend/
  finn/
    sources/          prices.py (yfinance) · filings.py (NSE → BSE fallback) · screener.py · documents.py (PDF → text → chunks)
    classify.py       rules first, cheap model only for the leftovers
    priority.py       score → tier (act / know / noise) → cluster related filings
    results.py        Revenue/EBITDA/PBT/PAT, QoQ + YoY, and why the bottom line moved
    enrich.py         incoming-person bio · results read from the PDF when there's no table
    alerts.py         user-defined price/volume lines, per-stock overrides
    research/         metrics.py · ledger.py (promise vs delivery) · engine.py · prompts.py
    llm.py            two-tier model router, content-hash cache, cost meter
    snapshot.py       builds the app's JSON (live or replayed)
    network/          model.py (load + validate) · match.py (scoring) · explain.py (why-lines) · overlay.py
  data/inputs/        the real data captured for the demo (see "Data" below), incl. public_careers.json
  data/network/       network.json (PRIVATE, synthetic) · orgs.json (org register + value chain)
  tests/              71 tests on the deterministic core, with no network and no model
```

## Run it

```bash
cd backend && pip install -r requirements.txt
python -m pytest -q                       # 84 passed

python -m finn snapshot                   # replay captured inputs (offline)
python -m finn snapshot --live            # NSE (BSE fallback) + yfinance + screener.in, now
python -m finn probe                      # which live sources and models this machine can reach
python -m finn import-linkedin Connections.csv   # your LinkedIn data export → network contacts
python -m finn network                    # → app/network.json (private overlay; add --model for model-worded why-lines)
python -m finn build-app                  # → app/dist/index.html (single file, deploy anywhere)

export GEMINI_API_KEY=...                 # free tier is enough
python -m finn research TATAMOTORS        # pull research on ANY NSE ticker
python -m finn serve                      # FastAPI: /snapshot, /research/{ticker}, /ask
```

On Windows, `backend\scripts\live.ps1` wraps each live step (`setup`, `probe`, `step1`, `research`, `why`, `build`) and logs every run to `backend\data\runs\`. It asks for the Gemini key with hidden input if `GEMINI_API_KEY` isn't set and never writes it to a file.

To route the models elsewhere, set `FINN_CHEAP_PROVIDER/MODEL` and `FINN_STRONG_PROVIDER/MODEL`. The supported providers are `gemini`, `groq`, `ollama` (fully local) and `openai` (any OpenAI-compatible endpoint).

## What it does

**Brief (push, windowed digest).** The window defaults to 24 hours, with chips for 3, 7, 14 and 30 days or a custom date range. Every filing in the window gets a score and lands in one of three tiers:

| Tier | Gets | Example |
|---|---|---|
| Needs you (≥60) | an expanded card with enrichment | HDFC Bank CEO change; Sun Pharma PAT −36% |
| Worth knowing (30–59) | one line | Maruti's record October sales; Polycab's clean beat |
| Routine (<30) | folded into a count, grouped by type | call recordings, ESOP allotments, results ads, NCDs at lenders |

Related filings are clustered. The results filing, its deck, the call audio and the newspaper ad become one card with "+3 related filings folded in". The top three items form a readable summary, and **Listen** reads it aloud (en-IN) for the commute.

**Alerts (push, triggered).** Thresholds are written as a sentence with editable numbers: "Tell me when a holding moves more than ±4% in a day, or trades 2× its 20-day average volume." You can set a custom line for one stock and choose which filing categories you watch. The alert log re-runs the moment you change a number.

**Results.** A season scoreboard covers all 15 holdings for Q2 FY27: a heat grid of revenue, EBITDA and PAT growth, plus the holdings still to report. Tap a row for QoQ, margins and the read-through.

**Research (pull).** It starts with a promise ledger. Each guidance item from the last four calls is given a checkable measure and scored in code as beat, delivered, on track, at risk, missed or walked back. Items that can't be measured stay unscored. The research view also covers story vs numbers, bull vs bear, a trajectory chart, cash quality, the balance sheet and working capital, what to watch next quarter, and sources. Every claim carries a source tag.

**Network (woven into Push and Pull).** Shows up at the moment it matters, not as a contact list. Throughout, a *material event* is a non-routine filing that names a new leader, or reports M&A, an equity raise, litigation, capex or an exchange clarification. It counts as *covered* when someone in the network has a medium or strong link to it.

- *Top of the brief → "From your network".* One card above the summary, in the first screen: the strongest network moment in the chosen window. In the default 24 hours of the live snapshot (16 Nov) that's Kaynes' joint-venture agreements, with the ex-Kaynes corp-dev contact. It also shows how many material events in that window your network covers, with the window named: "1 of 1 covered · last 24h". The pick is made in code: "needs you" first, then link strength.

- *Leadership change → "Your way in".* The best path is drawn inline: You → contact → (contact) → executive. Each line's thickness is that hop's strength, and the last hop is dotted because it is inferred. HDFC Bank's new CEO, Anup Bagchi: your best path is Vikram Rao, your IIT Kanpur batchmate, who overlapped with Bagchi at ICICI Bank for five years (2017–22) while Bagchi was Executive Director (strong). Next is Ritu Malhotra, whom you reach through your IIM-A batchmate Pranav (two hops). FINN also says your own link is weak: you're both IIT Kanpur alumni, but his graduation year isn't public. Trent's new chairman, Bahram Vakil: one medium path, through an ex-AZB lawyer. A contact who worked at Trent gets no link, because Vakil's Trent role was a board seat. HDFC Bank's new whole-time director, V. Srinivasa Rangan, has no sourced career in FINN's register, so FINN says it can't compute a path and points to the person closest to the company (Vikram Rao) instead.
- *Material event → "Best placed to explain".* One person and why: the ex-Kaynes corporate-development lead for Kaynes' joint-venture agreements, an insolvency lawyer for Polycab's insolvency filing, an ex-Bajaj risk head for Bajaj Finance's fund raise. Each event type weighs expertise against knowing the company: litigation leans on the lawyer, and a clarification leans only on company knowledge.
- *Compliance built in.* A contact who works at the company today is flagged ("keep it to public information") and ranked last. An ECM banker on a fund raise gets "never live mandates". Reconnect nudges never point you at a current insider.
- *Research → "Who to call".* Ex-employees, competitors, suppliers and customers for the company, each with what to ask. The question is taken from the promise ledger: Kaynes' FY26 revenue miss goes to the ex-corp-dev contact, the operating-cash-flow miss to the ex-ops head, and the walked-back OSAT plan to a contact running an OSAT ramp-up.
- *Network tab.* Small on purpose. It shows how many material events your network covers, always with the window. In the live snapshot of 16 Nov: 2 of 4 in the 7 days to 16 Nov, and 11 of 16 across every material filing in the snapshot (19 Oct – 15 Nov). All five gaps are board appointments (V. Srinivasa Rangan at HDFC Bank, Leo Puri at Asian Paints, Diwakar Nath Misra at NTPC) whose careers aren't in FINN's sourced register, so FINN names the gap rather than guess a path. It also shows thin spots in the book, a "who's your go-to for…" map, and up to three "worth a call" reconnects. Those are people you haven't spoken to in months whose former employer just filed something, never a current insider.
- *Onboarding.* The real starting point is the investor's own LinkedIn data export (`Connections.csv`, which LinkedIn lets every member download): `python -m finn import-linkedin` turns it into contacts. Name, current company and title are kept; URL and email are dropped; unknown companies are flagged for review; join years are left blank rather than guessed.
  No scraping, ever. The demo network is synthetic; a test runs the importer on a synthetic sample export (`tests/fixtures/Connections_sample.csv`).
- Tap anyone for how you know them, their career, and every event they appear on. Tap "sources" for the real executive's career facts, each with its link.

**Ask.** A bar sits above the tabs on every screen. Network questions ("who knows Anup Bagchi?", "go-to for QIPs?", "who should I call about Polycab?"), and any question that names someone in your network, are answered on the device from the overlay and never sent to a model. Other questions go to Claude, with only the snapshot as context, when FINN runs as a Claude artifact. On the Vercel deploy there is no model behind the page, so a small on-device router answers the common questions.

## Key decisions

1. **Numbers never come from a model.** Models extract text and make judgements. Every figure on screen (QoQ, YoY, margins, CAGRs, CFO/PAT, the ledger verdicts) is computed in Python from source tables. This is the one rule I would not trade away in a finance product.
2. **The category isn't enough to set priority.** A results filing is signal; the newspaper ad repeating it is paperwork. Both are category `results`, so the classifier adds a subtype. Context changes priority too:
   - an NCD issue at a bank or NBFC is routine;
   - an "acquisition" that is really a CAD 5,000 shell subsidiary is noise;
   - a results filing is promoted to "needs you" only when profit fell or a line deteriorated. A clean beat is "worth knowing".
3. **The results card says why the bottom line moved.** Sun Pharma's PAT fell 36% while EBITDA grew 5%. FINN shows the gap sits below the operating line: other income fell ₹1,222 cr and the tax rate went from 25% to 37%. A PAT miss caused by a tax line and one caused by weak operations lead to different decisions.
4. **The promise ledger is scored, not summarised.** The model's job stops at turning a promise into a measure ("W&C EBIT margin, range 11–13%"). The verdict is arithmetic. That makes it auditable, and it surfaced real findings:
   - Kaynes: 0 of 3 scoreable promises kept. The newest guide was cut about 24% within a quarter.
   - Polycab: beat 2 of 3 margin guides and declined to raise them.
   - Persistent: the "$2bn by March 2027" target was quietly restated as an exit run-rate.
5. **The network is matched in code; a model writes at most one line.** Links are scored from evidence (same employer with overlapping years, same group, same batch, links you entered), decayed for age, and combined by noisy-OR. Every score comes with its reason. The "why call them" line is a template by default and labelled as such. With `--model`, the cheap model may reword it from pseudonymised facts, but it cannot change who is ranked or how.
6. **Real people are never "known".** People named in filings connect to your contacts only through public career overlap, with a source for each fact. Those links are marked hypothetical. Board seats don't count, and a year that isn't public is left out, never inferred. The loader rejects any "knows" link to a real person.
7. **Saying "can't tell" is allowed.** Qualitative promises stay unscored, with the evidence shown. When Dixon's captured page (in the replayed demo data) turned out to be standalone, FINN showed only the consolidated headline rather than mix bases. A model-written background on a real person is kept only if it cites a page the search actually returned; otherwise the card says "No sourced background found". In the live run that happened for three of the five people named in filings.

## Cost and efficiency

Measured on the live run of 16 Nov 2026, at Gemini API list prices. On the free tier, Google billed $0 for all of it.

- **Classification.** The live feed had 198 filings for the 15 holdings in 30 days. Ordered regex rules labelled 169 of them at zero cost; the other 29 went to the cheap model, cached by subject. (On the 72 captured filings in the replay data, the rules cover every one; a test enforces this.)
- **Transcripts.** Chunks are pre-filtered by keyword before any model sees them. Guidance lives in roughly 15% of a call, so only those 2–3 chunks are sent.
- **Research.** One ticker is 12 cheap map calls (`gemini-3.5-flash-lite`) and 2 strong calls (`gemini-3.8-flash`, falling back to `gemini-3.6-flash` when it is overloaded): one to attach measures to promises, one to write the narrative. Measured: Navin Fluorine 14 calls, ~37,400 tokens, **$0.055**; PI Industries 14 calls, ~30,200 tokens, **$0.039**. Calls whose output was thrown away (a write-up regenerated after a bug fix) aren't counted.
- **Network "why call them" lines** (`network --model`): 29 calls to `gemini-3.5-flash-lite`, 4,709 tokens, **$0.0033**.
- **Cache.** Keyed by a hash of the content. Filings and transcripts never change, so re-running costs nothing. Cached answers keep the token counts metered when they were generated, so a re-run still reports what producing the output cost. `llm.METER` reports calls, cache hits, retries, tokens and USD per tier.

## Data: what's real and what isn't

The app now ships the **live snapshot of 16 Nov 2026**, built on a Windows laptop with `backend\scripts\live.ps1`. The first build ran in a sandbox that couldn't reach the exchanges, so the original demo data was captured by a web-fetch tool; that captured set still lives in `backend/data/inputs/` and `python -m finn snapshot` replays it offline.

**Ran live on 16 Nov 2026:**
- **Filings:** NSE's announcements API answered for 15 of 15 holdings (198 filings, 8 Oct – 16 Nov). BSE's API returned HTTP 403 from the same laptop, so the BSE fallback is in the code but has not run live.
- **Prices and volume:** Yahoo Finance via yfinance for 15 of 15 holdings; last full bar 13 Nov (today's partial bar is dropped before the close). Nothing is simulated, and the SIM label no longer appears. All 915 daily closes from 17 Aug to 13 Nov match screener.in's price series. Yahoo has no bar for the one-hour Diwali session on 8 Nov, and its volumes run 4–8% below screener.in's, which moves the volume multiples by 1–4% and changed no alert.
- **Results:** screener.in consolidated tables for 15 of 15 holdings, Q2 FY27. All 329 figures on the results cards (four lines, three quarters, QoQ, YoY, margins) were checked against screener.in on 16 Nov and match.
- **People named in filings:** five, read from the filing or, when NSE's text didn't name them, from the attached PDF (HDFC Bank's CEO and a director, Trent's chairman). Bagchi and Vakil carry sourced backgrounds from `public_careers.json`; the live web search found no citable source for the other three, so their cards say so.
- **Research on two companies outside the demo set:** Navin Fluorine and PI Industries, labelled "generated live on 16 Nov 2026" with models and cost. All 440 computed figures were checked against screener.in and match. `gemini-3.8-flash` gave no answer (HTTP 503 or a 3-minute timeout) on every write-up call that day, so `gemini-3.6-flash` wrote both narratives, and each report says so.
- **Network "why call them" lines:** 29 of 68 reworded by `gemini-3.5-flash-lite` from pseudonymised facts and marked "model-worded"; the other 39 are templates by design (company lists and fallbacks).

**Not live, and labelled as such:**
- **Research on Persistent, Polycab and Kaynes:** the narrative was written by Claude in the build session from the captured transcripts. The app says "Demo research … not generated live". The ledger verdicts and every number are computed by code.
- **Synthetic: the whole network.** All 31 contacts, their ties and their notes are fictional and marked SYN. Seven employers (e.g. Hartwell Health, Meridian Capital, Kestrel Advisors) are fictional too, and marked so in `orgs.json`. Real companies appear only as places fictional people worked. The value chain is hand-curated by industry role, not claimed contracts.
- **Real, cited per fact:** the public career history of people named in leadership-change filings (`data/inputs/public_careers.json`), gathered by web search during the build, with sources such as Business Today, Moneylife, CXO Digital Pulse, HDFC Bank's site and MarketScreener.

## Deliberately left out

- **Real-time intraday data.** The brief says end-of-day, and an intraday feed is a different product with different costs.
- **Push notifications and accounts.** A background worker that diffs snapshots and calls a push service is about a day of plumbing that proves nothing new.
- **News, broker notes, sentiment.** Excluded on purpose (pull side: primary sources only).
- **Portfolio P&L and valuation models.** That's another app; FINN is about what changed.
- **Network search, a contact list, a graph view.** The Network answers questions at the moment they come up; it isn't a CRM.
- **Scraping profiles.** Not done, by design. See consent and onboarding in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#security--privacy).

## Next

1. A scheduled worker that runs `snapshot --live` after the market close and on each new filing, diffs against the last run, and sends web push for "needs you" items only.
2. A ledger that persists across quarters, so guidance credibility becomes a time series per management team and can feed back into priority (a missed guide from a 0-of-3 team ranks higher).
3. Results parsed straight from the exchange XBRL. NSE's integrated filings already carry tagged figures, which removes the table-scraping step.
4. Peer context on the results card: "PAT −36% vs pharma holdings +8%".
5. An evaluation set of ~200 hand-labelled filings to measure classifier and priority precision.
6. Onboarding past the LinkedIn import (which is built): a calendar scan for "last spoke", alma maters, and a review screen where the investor confirms company matches and adds join years.
7. Feedback on suggestions ("called them, useful / not useful") to tune the scoring weights per investor, still in code.

## Architecture, data security & privacy

- **Two data planes.** A shared market snapshot (filings, prices, results, research) is built once for everyone. A private per-investor network overlay is never merged into it.
- **Code computes every number, score and network link.** Models only extract text from public documents and, optionally, reword one "why call them" line from pseudonymised facts.
- **The network never reaches a model.** Network questions are answered on the device, and the one optional model call sees "Contact A".
- **The people in an investor's network haven't consented.** A design under India's DPDP Act is written up but not built: pending contacts, one purpose, erasure within 30 days.
- **Hosting.** Today FINN is one static file on Vercel. Production would be a scheduled worker plus a small authenticated API in an India region.

The full write-up has the diagram, trust boundaries, one event traced end to end, data pipelines, the DPDP section, model use and failure modes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
