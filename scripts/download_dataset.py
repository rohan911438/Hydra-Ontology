"""Downloads one slice each of the GitHub / Jira / Slack sources from the
EnterpriseRAG-Bench v1.0.0 GitHub release and unzips them under data/raw/.

Usage: python scripts/download_dataset.py
"""
from __future__ import annotations

import io
import pathlib
import zipfile

import requests

RELEASE_BASE = "https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0"
SLICES = {
    "github": "github_slice_0001.zip",
    "jira": "jira_slice_0001.zip",
    "slack": "slack_slice_0001.zip",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for source, filename in SLICES.items():
        dest_dir = RAW_DIR / "unz" / source
        if dest_dir.exists() and any(dest_dir.rglob("*.txt")):
            print(f"[{source}] already present at {dest_dir}, skipping")
            continue
        url = f"{RELEASE_BASE}/{filename}"
        print(f"[{source}] downloading {url}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(dest_dir)
        print(f"[{source}] extracted to {dest_dir}")


if __name__ == "__main__":
    main()
