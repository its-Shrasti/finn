"""CLI.

  python -m finn snapshot [--live]     build app/snapshot.json
  python -m finn research TICKER       run pull research on any NSE ticker (needs an LLM key)
  python -m finn build-app             inline snapshot into app/dist/index.html (single-file deploy)
  python -m finn serve                 run the API on :8000
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "help"
    root = Path(__file__).resolve().parents[2]
    if cmd == "snapshot":
        from . import snapshot
        print(snapshot.write(live="--live" in argv))
    elif cmd == "research":
        from . import llm
        from .research import engine
        out = engine.run_live(argv[1].upper())
        print(json.dumps(out, indent=1, default=str)[:4000])
        print("\ncost:", llm.METER.__dict__)
    elif cmd == "build-app":
        src = (root / "app" / "index.html").read_text()
        snap = (root / "app" / "snapshot.json").read_text()
        dist = root / "app" / "dist"
        dist.mkdir(exist_ok=True)
        body = src.replace("/*__SNAPSHOT__*/null", snap.replace("</", "<\\/"))
        # artifact.html: bare page (the host adds the document skeleton)
        (dist / "artifact.html").write_text(body)
        # index.html: standalone page for any static host (Vercel, Netlify, GitHub Pages)
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                '<meta name="theme-color" content="#0D1318"></head><body>' + body + '</body></html>')
        (dist / "index.html").write_text(page)
        print(dist / "index.html", f"{len(page) / 1024:.0f} KB")
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("finn.api:app", host="0.0.0.0", port=8000)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
