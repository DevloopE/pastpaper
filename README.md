# AS-Level Past Papers

Offline-friendly menu for Cambridge International (CAIE) AS-level past papers, 2020-2024.

Open `index.html` locally, or visit the GitHub Pages URL for this repo.

## Subjects

- **Mathematics 9709** — P1, P3, M1, S1
- **Chemistry 9701** — Paper 1 (MCQ), Paper 2 (AS Structured)
- **Physics 9702** — Paper 1 (MCQ), Paper 2 (AS Structured)
- **Computer Science 9618** — Paper 1 (Theory), Paper 2 (Problem-solving)

## Features

- Subject tabs with live progress counts
- Checkboxes auto-save to your browser (localStorage)
- Export / Import progress as JSON
- Search by session, variant, or filename
- Hide-done toggle
- Light / dark theme

## Re-running the downloader

```
python download.py
```

Resumable — skips files already on disk and regenerates `papers.js`.

Paper source: [xtremepape.rs](https://papers.xtremepape.rs/).
