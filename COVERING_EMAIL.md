To: assessment@enam.com
Subject: ENAM - IIT screening - 2027

Hello ENAM team,

Here is my submission for the FINN build challenge.

- Prototype (open on a phone): [DEPLOYED LINK]
- Repository: [GITHUB LINK]


**What I built**

FINN filters what reaches you. It is a four-tab mobile app (Brief, Alerts, Results, Research) with an Ask bar on every screen, backed by a Python pipeline.

- **Brief.** A windowed digest of every filing across a 15-stock portfolio, ranked into "needs you", "worth knowing" and "routine". Over 30 days, 72 filings reduce to 13 cards.
  - Results cards give Revenue/EBITDA/PBT/PAT QoQ and YoY, plus a line on why the bottom line moved.
  - Leadership changes come with the incoming person's background.
  - You can have the brief read aloud.
- **Alerts.** Price and volume thresholds are written as an editable sentence, with per-stock overrides and a choice of filing categories to watch.
- **Results.** A Q2 FY27 season scoreboard.
- **Research.** Works on any NSE ticker. Its centrepiece is a promise ledger: each guidance item from the last four earnings calls gets a checkable measure, and code scores it against reported numbers.

**Key decisions**

1. **No number comes from a model.** Models extract text and make judgements; every figure and every ledger verdict is computed in Python from source tables.
2. **Priority depends on context, not just category.** A results filing is signal; its newspaper ad is noise. A debt raise at an NBFC is routine. A profit miss needs you; a clean beat is only worth knowing.
3. **Analyse management, don't summarise them.** The ledger turned up concrete findings:
   - Kaynes kept 0 of 3 scoreable promises and cut its newest guide about 24% within a quarter.
   - Polycab beat its margin guides and declined to raise them.
   - Persistent's "$2bn by March 2027" became an exit run-rate.
4. **Cheap by design.**
   - Regex rules classify all 72 captured filings at zero cost.
   - Transcripts are keyword-filtered before any model sees them.
   - About 12 cheap calls and 2 strong calls per company, under $0.05 at list prices and free on Gemini's free tier.
   - A content-hash cache makes re-runs free.

**What's real**

The demo uses real Q2 FY27 numbers, real filings, 8 real call transcripts and real bios, captured from primary sources. The end-of-day price series are simulated and labelled SIM in the app, because my build sandbox couldn't reach Yahoo Finance. `python -m finn snapshot --live` runs the same code against NSE/BSE, yfinance and screener.in.

**Left out on purpose**

Intraday data, accounts and real push delivery, news and broker notes, and portfolio P&L.

**Next**

1. A post-close worker that diffs snapshots and sends web push for "needs you" items only.
2. A guidance-credibility history per management team, fed back into priority.
3. Results read straight from NSE XBRL.
4. Peer context on results cards.
5. A labelled evaluation set for the classifier.

Thank you for your time.

Shrasti Dwivedi
