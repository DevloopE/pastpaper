"""
server.py - Local photo-to-past-paper search.

Run:
    python server.py            # starts at http://localhost:8000

Flow:
    POST /search (image)
      -> (optional) DocRes enhancement if ./DocRes is installed and not ?raw=1
      -> MinerU Precise API (same VLM settings as the indexer)
      -> embed the extracted text via Chroma
      -> return top-k questions with QP + MS deep links
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---- config ---------------------------------------------------------------

HERE = Path(__file__).parent
PAPERS_DIR = HERE / "papers"
CHROMA_DIR = HERE / "chroma"
TOKEN_FILE = HERE / "apiminerU.txt"
DOCRES_DIR = HERE / "DocRes"

API_BASE = "https://mineru.net/api/v4"
COLLECTION_NAME = "questions"
LIVE_BASE = "https://devloope.github.io/pastpaper"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ---- Chroma lazy-init -----------------------------------------------------

_collection = None

def collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection

# ---- DocRes enhancement (optional) ----------------------------------------

def docres_available() -> bool:
    return DOCRES_DIR.is_dir() and (DOCRES_DIR / "inference.py").is_file()

def enhance(src: Path, tmpdir: Path) -> Path:
    """Run DocRes end2end on src. Returns path to enhanced image.

    If DocRes isn't installed, returns src unchanged.
    """
    if not docres_available():
        return src

    out_dir = tmpdir / "enhanced"
    out_dir.mkdir(exist_ok=True)

    try:
        subprocess.run(
            [
                sys.executable, "inference.py",
                "--task", "end2end",
                "--im_path", str(src.resolve()),
                "--save_dtsprompt", "0",
            ],
            cwd=str(DOCRES_DIR),
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"DocRes failed ({e}); falling back to raw image", file=sys.stderr)
        return src

    # DocRes writes output under DocRes/restorted/<task>/<name>
    # Scan DocRes output folders for the newest file
    candidates = []
    for root, _, files in os.walk(DOCRES_DIR / "restorted"):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                candidates.append(p)
    if not candidates:
        return src
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    # copy to tmpdir so we own it
    dest = out_dir / newest.name
    shutil.copy2(newest, dest)
    return dest

# ---- MinerU one-shot image parse ------------------------------------------

def _req(method: str, url: str, token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": "pastpaper-server/0.1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        return json.loads(r.read())

def _put(upload_url: str, data: bytes) -> None:
    req = urllib.request.Request(
        upload_url, data=data, method="PUT", headers={"Content-Type": ""},
    )
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        if r.status not in (200, 201):
            raise RuntimeError(f"upload failed {r.status}")

def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        return r.read()

AGENT_BASE = "https://mineru.net/api/v1/agent"

def parse_image_with_mineru(image_path: Path) -> str:
    """Upload image to MinerU's Agent Lightweight API, return markdown text.

    Why Agent, not Precise: Precise /file-urls/batch only accepts PDFs (it
    returns "source or target not a PDF" for any image regardless of
    model_version or is_ocr). Agent accepts JPG/PNG/etc directly and returns
    a markdown_url. No auth needed (IP rate-limited).
    """
    # Sanitize filename — parens/spaces break MinerU's content-type sniff
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", image_path.name)
    if "." not in safe_name:
        safe_name += ".jpg"

    # 1. Submit
    resp = _req(
        "POST", f"{AGENT_BASE}/parse/file", body={"file_name": safe_name},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"agent submit: {resp}")
    task_id = resp["data"]["task_id"]
    upload_url = resp["data"]["file_url"]

    # 2. PUT upload (OSS signed URL, empty Content-Type)
    _put(upload_url, image_path.read_bytes())

    # 3. Poll
    t0 = time.time()
    md_url = None
    while time.time() - t0 < 180:
        p = _req("GET", f"{AGENT_BASE}/parse/{task_id}")
        data = p.get("data", {})
        state = data.get("state")
        if state == "done":
            md_url = data.get("markdown_url")
            break
        if state == "failed":
            raise RuntimeError(f"agent parse failed: {data}")
        time.sleep(3)
    if not md_url:
        raise RuntimeError("agent timed out")

    # 4. Fetch markdown
    return _download_bytes(md_url).decode("utf-8", errors="replace").strip()

# ---- HTTP routes ----------------------------------------------------------

app = FastAPI(title="Past Paper Photo Search")

# Serve PDFs + assets
if PAPERS_DIR.is_dir():
    app.mount("/papers", StaticFiles(directory=str(PAPERS_DIR)), name="papers")

@app.get("/", response_class=HTMLResponse)
def root() -> str:
    html = HERE / "search.html"
    if html.is_file():
        return html.read_text(encoding="utf-8")
    return "<h1>search.html missing</h1>"

@app.get("/status")
def status() -> dict:
    try:
        c = collection()
        count = c.count()
    except Exception as e:
        count = 0
        err = str(e)[:200]
        return {"chroma": False, "error": err, "docres": docres_available()}
    return {
        "chroma": True,
        "questions_indexed": count,
        "docres": docres_available(),
    }

@app.post("/search")
async def search(
    photo: UploadFile = File(...),
    raw: bool = Query(False, description="skip DocRes enhancement"),
    top_k: int = Query(5, ge=1, le=20),
) -> JSONResponse:
    t0 = time.time()
    tmpdir = Path(tempfile.mkdtemp(prefix="pp_search_"))
    try:
        src = tmpdir / (photo.filename or "upload.jpg")
        src.write_bytes(await photo.read())

        # 1. Enhance
        t_enhance0 = time.time()
        enhanced = src if raw else enhance(src, tmpdir)
        enhanced_ms = int((time.time() - t_enhance0) * 1000)

        # 2. MinerU OCR
        t_mineru0 = time.time()
        try:
            text = parse_image_with_mineru(enhanced)
        except Exception as e:
            return JSONResponse(
                {"error": f"MinerU OCR failed: {e}", "phase": "mineru"},
                status_code=502,
            )
        mineru_ms = int((time.time() - t_mineru0) * 1000)

        if not text.strip():
            return JSONResponse({"error": "no text extracted from photo"}, status_code=422)

        # 3. Chroma search — over-fetch so grouping has enough to work with
        t_search0 = time.time()
        try:
            c = collection()
            res = c.query(query_texts=[text], n_results=max(top_k * 4, 20))
        except Exception as e:
            return JSONResponse(
                {"error": f"Chroma search failed: {e}", "phase": "chroma"},
                status_code=500,
            )
        search_ms = int((time.time() - t_search0) * 1000)

        # Group hits by (paper, page). Multiple sub-question matches on the
        # same page are really one result — keep the best distance, list all
        # matched sub-question labels.
        groups: dict[tuple[str, int], dict] = {}
        for i in range(len(res["ids"][0])):
            meta = res["metadatas"][0][i]
            dist = res["distances"][0][i]
            key = (meta["paper"], int(meta["page"]))
            preview = (res["documents"][0][i] or "")[:220]
            if key not in groups:
                groups[key] = {
                    "distance": dist,
                    "meta": meta,
                    "matches": [],
                }
            g = groups[key]
            g["matches"].append({
                "question_no": meta["question_no"],
                "distance": round(dist, 4),
                "preview": preview,
            })
            if dist < g["distance"]:
                g["distance"] = dist

        # Sort grouped results by best distance, take top_k groups
        ordered = sorted(groups.values(), key=lambda g: g["distance"])[:top_k]

        results = []
        for g in ordered:
            meta = g["meta"]
            # Dedupe identical question_nos, keep insertion order
            seen, qnos = set(), []
            for m in g["matches"]:
                q = m["question_no"]
                if q not in seen:
                    seen.add(q)
                    qnos.append(q)
            results.append({
                "distance": round(g["distance"], 4),
                "paper": meta["paper"],
                "subject": meta["subject"],
                "question_nos": qnos,
                "page": meta["page"],
                "ms_file": meta["ms_file"],
                "ms_page": meta["ms_page"],
                "qp_url": f"{LIVE_BASE}/{meta['qp_url']}#page={meta['page']}",
                "ms_url": (
                    f"{LIVE_BASE}/{meta['ms_url']}#page={meta['ms_page']}"
                    if meta["ms_page"] else f"{LIVE_BASE}/{meta['ms_url']}"
                ),
                "preview": g["matches"][0]["preview"],
                "match_count": len(qnos),
            })

        return JSONResponse({
            "elapsed_ms": int((time.time() - t0) * 1000),
            "timing": {"enhance_ms": enhanced_ms, "mineru_ms": mineru_ms, "search_ms": search_ms},
            "enhanced": not raw and docres_available(),
            "extracted_text_preview": text[:300],
            "results": results,
        })
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="127.0.0.1", port=port)
