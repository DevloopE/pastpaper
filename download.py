"""
Downloads CAIE AS-level past papers (2020-2024) from xtremepape.rs
and writes a manifest (papers.js) consumed by index.html.

Subjects:
  - Mathematics 9709   (P1, P3, M1, S1)
  - Chemistry 9701     (Paper 1 MCQ, Paper 2 AS Structured)
  - Physics 9702       (Paper 1 MCQ, Paper 2 AS Structured)
  - Computer Science 9618 (Paper 1 Theory, Paper 2 Problem-solving)

Resumable: skips files that already exist locally. 404s are logged and skipped
(not every session/variant/type exists on the mirror).

Usage:
    python download.py
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://papers.xtremepape.rs/CAIE/AS and A Level"
USER_AGENT = "Mozilla/5.0 (pastpaper-downloader)"
TIMEOUT = 30
MAX_WORKERS = 12

# Permissive SSL context for networks that MITM (corporate proxies, etc.)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
SESSIONS = [
    ("s", "May/June"),
    ("w", "Oct/Nov"),
    ("m", "Feb/March"),
]
DOC_TYPES = ["qp", "ms"]

# subject_key -> { code, folder, papers: { paper_name: (paper_digit, "label") } }
# variants are {paper_digit}{1..3}
# School-tested subset (used by Dev mode filter in the HTML).
# Embedded here so the manifest carries the info.
SCHOOL_SUBSET = {
    "math-9709":      ["P1", "P3", "M1", "S1"],
    "chemistry-9701": ["P1", "P2"],
    "physics-9702":   ["P1", "P2"],
    "cs-9618":        ["P1", "P2"],
    "economics-9708": [],  # not tested at school
}

SUBJECTS = {
    "math-9709": {
        "code": "9709",
        "name": "Mathematics",
        "folder": "Mathematics (9709)",
        "papers": {
            "P1": (1, "Pure Mathematics 1"),
            "P2": (2, "Pure Mathematics 2"),
            "P3": (3, "Pure Mathematics 3"),
            "M1": (4, "Mechanics"),
            "S1": (5, "Probability & Statistics 1"),
            "S2": (6, "Probability & Statistics 2"),
        },
    },
    "chemistry-9701": {
        "code": "9701",
        "name": "Chemistry",
        "folder": "Chemistry (9701)",
        "papers": {
            "P1": (1, "Multiple Choice"),
            "P2": (2, "AS Structured Questions"),
            "P3": (3, "Advanced Practical Skills"),
            "P4": (4, "A Level Structured Questions"),
            "P5": (5, "Planning, Analysis, Evaluation"),
        },
    },
    "physics-9702": {
        "code": "9702",
        "name": "Physics",
        "folder": "Physics (9702)",
        "papers": {
            "P1": (1, "Multiple Choice"),
            "P2": (2, "AS Structured Questions"),
            "P3": (3, "Advanced Practical Skills"),
            "P4": (4, "A Level Structured Questions"),
            "P5": (5, "Planning, Analysis, Evaluation"),
        },
    },
    "cs-9618": {
        "code": "9618",
        "name": "Computer Science",
        "folder": "Computer Science (for first examination in 2021) (9618)",
        "papers": {
            "P1": (1, "Theory Fundamentals"),
            "P2": (2, "Fundamental Problem-solving & Programming"),
            "P3": (3, "Advanced Theory"),
            "P4": (4, "Practical"),
        },
    },
    "economics-9708": {
        "code": "9708",
        "name": "Economics",
        "folder": "Economics (9708)",
        "papers": {
            "P1": (1, "Multiple Choice (AS)"),
            "P2": (2, "Data Response & Essay (AS)"),
            "P3": (3, "Multiple Choice (A Level)"),
            "P4": (4, "Data Response & Essays (A Level)"),
        },
    },
}


def build_targets():
    """Yield dicts describing every file to attempt."""
    for subj_key, subj in SUBJECTS.items():
        for year in YEARS:
            yy = f"{year % 100:02d}"
            for session, session_label in SESSIONS:
                for paper_name, (paper_digit, paper_label) in subj["papers"].items():
                    for variant_suffix in (1, 2, 3):
                        variant = paper_digit * 10 + variant_suffix
                        for doc in DOC_TYPES:
                            filename = (
                                f"{subj['code']}_{session}{yy}_{doc}_{variant:02d}.pdf"
                            )
                            yield {
                                "subject": subj_key,
                                "subject_name": subj["name"],
                                "code": subj["code"],
                                "folder": subj["folder"],
                                "year": year,
                                "session": session,
                                "session_label": session_label,
                                "paper": paper_name,
                                "paper_label": paper_label,
                                "variant": variant,
                                "type": doc,
                                "filename": filename,
                            }


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return r.read()


def download_one(target: dict, out_root: Path) -> tuple[str, dict | None]:
    """Returns (status, manifest_entry or None). status is 'ok', 'cached', 'miss', or 'err:...'"""
    subj_dir = out_root / target["subject"]
    local = subj_dir / target["filename"]
    rel = f"papers/{target['subject']}/{target['filename']}"

    entry = {
        "subject": target["subject"],
        "subjectName": target["subject_name"],
        "code": target["code"],
        "year": target["year"],
        "session": target["session"],
        "sessionLabel": target["session_label"],
        "paper": target["paper"],
        "paperLabel": target["paper_label"],
        "variant": target["variant"],
        "type": target["type"],
        "file": rel,
    }

    if local.exists() and local.stat().st_size > 0:
        return "cached", entry

    path = f"{BASE}/{target['folder']}/{target['filename']}"
    url = urllib.parse.quote(path, safe=":/")

    try:
        data = fetch(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "miss", None
        return f"err:{e.code}", None
    except Exception as e:
        return f"err:{type(e).__name__}", None

    if not data.startswith(b"%PDF"):
        return "err:not-pdf", None

    subj_dir.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return "ok", entry


def main():
    here = Path(__file__).parent
    papers_dir = here / "papers"
    papers_dir.mkdir(exist_ok=True)

    targets = list(build_targets())
    total = len(targets)
    print(f"Attempting {total} files across {len(SUBJECTS)} subjects, years {YEARS[0]}-{YEARS[-1]}...")

    manifest: list[dict] = []
    counts = {"ok": 0, "cached": 0, "miss": 0, "err": 0}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(download_one, t, papers_dir): t for t in targets}
        done = 0
        for fut in as_completed(futures):
            target = futures[fut]
            status, entry = fut.result()
            done += 1
            if status == "ok":
                counts["ok"] += 1
                manifest.append(entry)
            elif status == "cached":
                counts["cached"] += 1
                manifest.append(entry)
            elif status == "miss":
                counts["miss"] += 1
            else:
                counts["err"] += 1
                print(f"  [{status}] {target['filename']}", file=sys.stderr)
            if done % 25 == 0 or done == total:
                elapsed = time.time() - t0
                print(
                    f"  {done}/{total} "
                    f"ok={counts['ok']} cached={counts['cached']} "
                    f"miss={counts['miss']} err={counts['err']} "
                    f"({elapsed:.0f}s)"
                )

    manifest.sort(key=lambda e: (e["subject"], -e["year"], e["session"], e["paper"], e["variant"], e["type"]))

    subject_meta = {
        k: {
            "name": v["name"],
            "code": v["code"],
            "paperLabels": {p: lbl for p, (_, lbl) in v["papers"].items()},
            "school": SCHOOL_SUBSET.get(k, []),
        }
        for k, v in SUBJECTS.items()
    }

    manifest_path = here / "papers.js"
    manifest_path.write_text(
        "// Auto-generated by download.py. Do not edit by hand.\n"
        "window.PAPERS = " + json.dumps(manifest, indent=2) + ";\n"
        "window.SUBJECTS_META = " + json.dumps(subject_meta, indent=2) + ";\n",
        encoding="utf-8",
    )

    elapsed = time.time() - t0
    print(
        f"\nDone in {elapsed:.0f}s — "
        f"downloaded {counts['ok']}, cached {counts['cached']}, "
        f"missing {counts['miss']}, errors {counts['err']}."
    )
    print(f"Manifest: {manifest_path}")
    print(f"Open index.html in your browser to use the menu.")


if __name__ == "__main__":
    main()
