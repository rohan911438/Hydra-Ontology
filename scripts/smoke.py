"""Definition-of-done script: brings up the pipeline against an already-running
HydraDB, ingests the sampled dataset, resolves entities, detects conflicts,
and runs the CLI's showcase questions end to end, printing results.

Usage:
  python scripts/smoke.py                # use cached LLM extraction/resolution
  python scripts/smoke.py --reextract     # re-run live Gemini extraction
  python scripts/smoke.py --reresolve     # re-run live Gemini resolution judging
  python scripts/smoke.py --wipe          # clear the graph first (slow -- see db.wipe_graph)

Every write is a MERGE keyed on a stable id, so by default this does NOT
wipe first -- re-running against existing data is already idempotent. For a
genuinely clean slate, resetting the HydraDB docker volume (see README) is
much faster than --wipe.

Assumes `docker compose up -d` has already been run (see README) so HydraDB
is listening on 127.0.0.1:7687.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from hydra_ontology.pipeline import run_full_pipeline  # noqa: E402
from hydra_ontology.query.cli import run_demo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reextract", action="store_true")
    ap.add_argument("--reresolve", action="store_true")
    ap.add_argument("--wipe", action="store_true")
    args = ap.parse_args()

    run_full_pipeline(wipe=args.wipe, reextract=args.reextract, reresolve=args.reresolve)

    print("\n" + "=" * 70)
    print("SHOWCASE QUERIES")
    print("=" * 70)
    run_demo()


if __name__ == "__main__":
    main()
