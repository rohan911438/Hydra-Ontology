"""Top-level orchestration: ingest all three sources, then resolve entities,
then detect conflicts. Used by scripts/smoke.py."""
from __future__ import annotations

from .conflicts.detect import detect_contradictions, write_status_claims
from .db import verify_roundtrip, wipe_graph
from .ingest.extract import extract_source
from .ingest.load_dataset import SOURCES, load_documents
from .ingest.write_graph import GraphBatch, write_document
from .resolution.resolve import run_resolution


def ingest_source(source: str, *, reextract: bool = False, verbose: bool = True) -> int:
    docs = load_documents(source)
    extractions = extract_source(source, docs, reextract=reextract, verbose=verbose)
    batch = GraphBatch()
    written = 0
    for doc in docs:
        extraction = extractions.get(doc["doc_id"])
        if extraction is None:
            continue
        _key, entity_label, entity_id = write_document(batch, doc, extraction)
        write_status_claims(batch, doc, extraction, entity_label, entity_id)
        written += 1
    if verbose:
        print(f"[pipeline] {source}: flushing {written} documents' worth of writes to HydraDB...")
    batch.flush()
    if verbose:
        print(f"[pipeline] {source}: wrote {written}/{len(docs)} documents into HydraDB")
    return written


def run_full_pipeline(*, wipe: bool = False, reextract: bool = False, reresolve: bool = False, verbose: bool = True) -> None:
    """wipe defaults to False: every write in this pipeline is a MERGE keyed
    on a stable id, so re-running against existing data is already
    idempotent. Only pass wipe=True for a small/test graph -- DETACH DELETE
    is slow on this engine (see db.wipe_graph); for a large graph, reset the
    HydraDB docker volume instead (see README)."""
    if verbose:
        print("[pipeline] verifying HydraDB round-trip...")
    if not verify_roundtrip():
        raise RuntimeError("HydraDB round-trip check failed -- is the server running?")

    if wipe:
        if verbose:
            print("[pipeline] wiping existing graph for a clean ingest...")
        wipe_graph()

    for source in SOURCES:
        ingest_source(source, reextract=reextract, verbose=verbose)

    if verbose:
        print("[pipeline] running entity resolution...")
    run_resolution(reresolve=reresolve, verbose=verbose)

    if verbose:
        print("[pipeline] detecting conflicts...")
    n = detect_contradictions(verbose=verbose)
    if verbose:
        print(f"[pipeline] done. {n} contradiction(s) flagged.")
