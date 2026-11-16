"""CLI.

  python -m finn probe                 check each live source once (NSE, BSE, Yahoo, screener.in, Gemini)
  python -m finn snapshot [--live] [--live-research]
                                       build app/snapshot.json (shared: same for every user)
  python -m finn snapshot --add-research
                                       add saved live research to the existing snapshot (no refetch)
  python -m finn network [--model]     build app/network.json (private per-user overlay; --model lets the
                                       cheap model reword the "why call them" lines from pseudonymised facts)
  python -m finn import-linkedin Connections.csv [--merge]
                                       LinkedIn's official data export -> network contacts
  python -m finn research TICKER       run pull research on any NSE ticker (needs an LLM key);
                                       saved to data/research_live/TICKER.json with its date and cost
  Add --log NAME to any command to also write data/runs/NAME.log (UTF-8, includes tracebacks).
  python -m finn build-app             inline snapshot + overlay into app/dist/index.html (single-file deploy)
  python -m finn serve                 run the API on :8000
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()

    def flush(self):
        for st in self.streams:
            st.flush()


def main(argv: list[str]) -> None:
    if "--log" in argv:
        i = argv.index("--log")
        name = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
        from .config import DATA
        runs = DATA / "runs"
        runs.mkdir(exist_ok=True)
        f = open(runs / f"{name}.log", "w", encoding="utf-8")
        sys.stdout, sys.stderr = _Tee(sys.__stdout__, f), _Tee(sys.__stderr__, f)
        import traceback
        from datetime import datetime
        print(f"# finn {' '.join(argv)} · {datetime.now().isoformat(timespec='seconds')} · python {sys.version.split()[0]}")
        try:
            _run(argv)
        except BaseException:
            traceback.print_exc()
            raise
        finally:
            print("# done", datetime.now().isoformat(timespec="seconds"))
        return
    _run(argv)


def _run(argv: list[str]) -> None:
    cmd = argv[0] if argv else "help"
    root = Path(__file__).resolve().parents[2]
    if cmd == "probe":
        from . import probe
        probe.run()
    elif cmd == "snapshot":
        from . import snapshot
        if "--add-research" in argv:
            print(snapshot.add_research())
        else:
            print(snapshot.write(live="--live" in argv, live_research="--live-research" in argv))
    elif cmd == "network":
        from .network import overlay
        print(overlay.write(use_model="--model" in argv))
    elif cmd == "import-linkedin":
        from .network import importer
        print(importer.run(argv[1], merge="--merge" in argv))
    elif cmd == "research":
        from . import llm
        from .research import engine
        t = argv[1].upper()
        out = engine.run_live(t)
        from .config import DATA
        d = DATA / "research_live"
        d.mkdir(exist_ok=True)
        (d / f"{t}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        led = out["ledger"]
        print(f"{t}: {out['name']} · {len(out['sources']) - 1} transcripts · {led.get('scored')} scoreable promises · tally {led.get('tally')}")
        print("skipped:", out.get("skipped") or "none")
        print("models:", out.get("models"), "| fallbacks:", out.get("fallbacks") or "none")
        print("cost:", json.dumps(out["cost"]))
        print("saved:", d / f"{t}.json")
    elif cmd == "build-app":
        src = (root / "app" / "index.html").read_text(encoding="utf-8")
        snap = (root / "app" / "snapshot.json").read_text(encoding="utf-8")
        dist = root / "app" / "dist"
        dist.mkdir(exist_ok=True)
        body = src.replace("/*__SNAPSHOT__*/null", snap.replace("</", "<\\/"))
        # The private overlay goes in its own slot, never merged into the shared snapshot.
        # This demo's network is synthetic, so bundling it into a public page is acceptable;
        # a real deployment serves it per user behind auth instead (see README).
        netf = root / "app" / "network.json"
        if netf.exists():
            body = body.replace("/*__NETWORK__*/null", netf.read_text(encoding="utf-8").replace("</", "<\\/"))
        # artifact.html: bare page (the host adds the document skeleton)
        (dist / "artifact.html").write_text(body, encoding="utf-8")
        # index.html: standalone page for any static host (Vercel, Netlify, GitHub Pages)
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                '<meta name="theme-color" content="#0D1318"></head><body>' + body + '</body></html>')
        (dist / "index.html").write_text(page, encoding="utf-8")
        print(dist / "index.html", f"{len(page) / 1024:.0f} KB")
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("finn.api:app", host="0.0.0.0", port=8000)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
