# AS-Level Past Papers

Two things live in this repo:

1. **Public study menu** — a static site listing CAIE past papers with checkboxes, filters, and a Dev mode for your school's subset. Hosted at <https://devloope.github.io/pastpaper/>.
2. **Local photo search** (optional, local-only) — upload a phone photo of a question, get back the exact paper + mark scheme page.

## The public site

### Subjects

- **Mathematics 9709** — P1, P2, P3, M (Mechanics), S1, S2
- **Chemistry 9701** — Paper 1 (MCQ), Paper 2 (AS Structured), Paper 3 (Practical), Paper 4 (A Level), Paper 5 (Planning)
- **Physics 9702** — same 5-paper structure as chemistry
- **Computer Science 9618** — Paper 1 (Theory), Paper 2 (Problem-solving), Paper 3 (Advanced Theory), Paper 4 (Practical)
- **Economics 9708** — Paper 1-4 (AS + A Level)

### Features

- Subject tabs with live progress counts
- Paper-first layout with year × variant matrix
- Filter chips for year, session, variant
- **Dev mode toggle** — when on, only shows your school's tested subset
- Checkboxes auto-save via localStorage; Export/Import progress as JSON
- Light / dark theme

### Refresh the paper catalogue

```bash
python download.py
```

Resumable — skips already-downloaded PDFs. Regenerates `papers.js`.

Source: <https://papers.xtremepape.rs/>.

---

## Local photo search (optional)

### Requirements

- Python 3.10+
- ~500 MB disk for Chroma + cached embedding model
- MinerU API token in `apiminerU.txt` (gitignored)

### Install

```bash
pip install -r requirements.txt
```

### One-time: build the question index

```bash
# 2-PDF sample first (validates setup in ~3 min):
python build_index.py --scope school-qp --sample 2

# Full school subset, QPs only (~1 day of API time, resumable):
python build_index.py --scope school-qp

# Full school subset including mark schemes:
python build_index.py --scope school

# Everything (all subjects including Econ + A-level papers):
python build_index.py --scope all
```

Stages run in order: `fetch` → `parse` → `embed`. Re-running `python build_index.py` resumes from wherever it stopped. To re-run a single stage only:

```bash
python build_index.py --stage parse
python build_index.py --stage embed
```

### Run the search server

```bash
python server.py
```

Open <http://localhost:8000>, drop a photo, hit Search. Result cards link directly to the QP and MS pages on GitHub Pages.

### Optional: DocRes image enhancement

DocRes dewarps, deshadows, and cleans up phone photos before OCR. Skip this for clean photos; it adds ~10-20s per query.

```bash
git clone https://github.com/ZZZHANG-jx/DocRes
# download the pretrained weights per the DocRes README
```

Place the `DocRes/` folder next to `server.py`. The server auto-detects it at `/status` and applies it per query (toggle off via the "Skip enhancement" checkbox).

### Clash / VPN users

The MinerU CDN (`cdn-mineru.openxlab.org.cn`) is mainland-China hosted. If you route through an overseas proxy, downloads may fail with SSL errors. Add DIRECT rules for:

- `DOMAIN,cdn-mineru.openxlab.org.cn,DIRECT`
- `DOMAIN-SUFFIX,openxlab.org.cn,DIRECT`
- `DOMAIN-SUFFIX,aliyuncs.com,DIRECT`

Reload your Clash profile after editing.

### File layout

```
pastpaper/
├── download.py           # PDF downloader (xtremepape.rs)
├── papers.js             # manifest consumed by index.html
├── index.html            # the public menu (→ GitHub Pages)
├── papers/               # downloaded PDFs
│
├── build_index.py        # photo-search indexer (MinerU → Chroma)
├── server.py             # local FastAPI search server
├── search.html           # photo-search frontend
├── requirements.txt
├── apiminerU.txt         # MinerU token (gitignored)
├── index_state/          # fetch/parse checkpoints (gitignored)
├── chroma/               # vector DB (gitignored)
└── DocRes/               # optional enhancement model (gitignored)
```
