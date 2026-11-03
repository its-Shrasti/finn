"""Prompts for the research pipeline. Kept separate so they can be versioned
and evaluated independently of the code."""

GROUNDING = (
    "You are an equity analyst working ONLY from the primary-source material provided "
    "(company filings, annual-report tables, earnings-call transcripts). Do not use outside "
    "knowledge of the company, news, or broker views. If the material doesn't support a "
    "claim, don't make it. Every claim must cite its source id in square brackets, e.g. "
    "[Q2FY27 call] or [P&L FY26]. Never state a number that is not in the material. JSON only."
)

# MAP — cheap tier, once per transcript (on pre-filtered chunks).
MAP = """Transcript id: {call_id} ({date}). Extract from this earnings-call excerpt:
- guidance: forward-looking statements by MANAGEMENT about revenue/growth, margins, capex,
  segment targets, working capital, cash flow, order book — with numbers, horizon, and whether
  management called it guidance or an aspiration.
- reported: numbers management cited for the quarter (segment growth, margins, order book, days).
  Use snake_case keys; percentages as plain numbers.
- pushback: what analysts pressed on (one line each).
Return {{"guidance":[{{"metric":..,"statement":"<=25 words paraphrase","horizon":..,"firmness":"guidance|aspiration|commentary"}}],
"reported":{{"key": number}}, "pushback":[".."], "tone":".."}}

EXCERPT:
{text}"""

# MEASURE — strong tier, once per company. Turns promises into checkable measures.
MEASURE = """Below are guidance statements from several calls and the numeric series available.
Deduplicate repeated promises (keep the earliest made_in; note later revisions or clarifications).
For each, choose a measure the code can check, using ONLY these series keys: {series_keys}
Types: range{{lo,hi,unit,series}} | threshold{{min,unit,series|actual}} | direction{{series,expect,from_call,to_call}} |
revision{{old_mid,new_mid}} | pending{{deadline}} | qualitative{{}} (with "evidence" text citing a source).
Return {{"guidance":[{{"id":"g1","made_in":"<call id>","metric":..,"promise":..,"measure":{{...}},"evidence":"..."}}]}}

GUIDANCE BY CALL:
{guidance}

SERIES:
{series}"""

# REDUCE — strong tier, once per company. Judgement on top of computed facts.
REDUCE = """Company: {name} ({ticker}).
Computed facts (authoritative — use these numbers only):
{facts}

Promise ledger (verdicts computed by code):
{ledger}

Analyst pushback across calls:
{pushback}

Write:
- snapshot: 2 sentences — what the company does and its revenue mix, from the material.
- narrative_vs_numbers: 3-5 items where management's story and the financials agree or diverge.
  Each {{"claim": what mgmt says [src], "numbers": what the data shows [src], "read": "agree|diverge|too early"}}
- bull: 3 tight points; bear: 3 tight points. Each <= 22 words with [src].
- watch: the 2-3 numbers that would change the thesis next quarter.
Return {{"snapshot":..,"narrative_vs_numbers":[..],"bull":[..],"bear":[..],"watch":[..]}}"""
