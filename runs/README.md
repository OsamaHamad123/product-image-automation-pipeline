# Live run outputs

Measurement runs on real sheet rows (`scripts/smoke_live.py --dry-run`). Nothing in them is written to the
sheet or to Cloudinary, and they hold no keys.

- `2026-10-03/`: the owner's runs before phase 3 (rows 2-61, Google Images only, one label reader).
  `smoke_6.json` is the baseline the phase 3 runs are compared with.

New runs stay on your machine: this folder and the run files in the project root are in `.gitignore`.
Write them here and compare two of them:

```bat
.venv\Scripts\python.exe scripts\smoke_live.py --rows 2-61 --dry-run --no-expansion --json runs\before.json
.venv\Scripts\python.exe scripts\smoke_live.py --rows 2-61 --dry-run --json runs\after.json
.venv\Scripts\python.exe scripts\compare_runs.py runs\before.json runs\after.json --md --out runs\compare.md
```
