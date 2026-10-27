# Sprint 1 demo: web entry → encrypted Vault → rogue admin gets caught

About 2 minutes. Synthetic data only. Shows PBI-04 (record + audit entry together),
PBI-05 (encrypted at rest), and SR-3 (tamper-evident).

## Before class (once)

```powershell
cd <repo>
.venv\Scripts\activate
pip install -e "vault[dev]"; pip install -r gatekeeper\api\requirements.txt; pip install reportlab
python scripts\make_test_barcodes.py        # print test_barcodes.pdf ("fake CACs")
```

Scanner check: scan a card into Notepad. If it adds an Enter after the text, the web
form will submit as soon as you scan. Either turn off the scanner's Enter suffix
(setup barcode in its manual), or enter the scores first and scan last.

## Setup (just before recording)

**Window 1, the server** (from `gatekeeper\api`):

```powershell
$env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
$env:MUSTER_DB_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:MUSTER_DB_KEY        # copy this value for window 2
python live_app.py --reset
```

**Window 2, the "admin"** (from the repo root, same venv):

```powershell
$env:MUSTER_DB_KEY = "<paste the same key>"
$db = "gatekeeper\api\muster_demo.db"
```

**Browser:** open `docs\interfaces\index.html`. Set **Role = Grader** and **Session ID = 1**,
and clear the Raw Barcode field.

## The demo

| # | Do | Say |
|---|---|---|
| 1 | Click the barcode field, scan **Cadet Alpha**, enter the five AFT results, click **Execute** | "The grader scans the cadet's card and enters the AFT. Each step goes through the Gatekeeper into the Vault." Point at the `201`/`200` responses. |
| 2 | Window 2: `python -m muster_vault integrity --db $db` | "Integrity check: audit chain PASS, Score 1 OK. Every record matches its audit trail." |
| 3 | `Select-String -Path "$db*" -Pattern "Alpha" -SimpleMatch` | "Someone who only has the file searches it for the cadet's name: nothing. It's encrypted." |
| 4 | `python scripts\admin_tamper.py edit-score --event 1 --component MDL --points 60` | "Now a rogue admin who *has* the key changes the deadlift score directly in the database, skipping MUSTER entirely. It worked." |
| 5 | `python -m muster_vault integrity --db $db` | "Caught: **MDL: database says 60 pts, audit log says 100**. The edit left no audit entry, so the record and its history disagree." |
| 6 | `python scripts\admin_tamper.py cover-tracks --event 1 --component MDL` | "So the admin removes the log's lock and rewrites the history to match." |
| 7 | `python -m muster_vault integrity --db $db` | "Caught again: **audit chain FAIL at that entry**. Changing history breaks the chain of fingerprints. Either way, tampering is *evident*." |

**Close with the honest limits** (they will ask):
- An admin who edits the score, edits the log, *and* recomputes every fingerprint after
  it is out of scope for the MVP. The countermeasure is copies outside the database:
  the cadet's receipt and the printed chain head. Signed checkpoints are the named
  upgrade.
- The scanner can't read real CAC barcodes yet, so these are synthetic Code 128 cards.
  Reading the real back barcode (Code 39) is a Sprint 2 item.
- Points show 100 each because AFT scoring tables arrive in Sprint 2 (pending the DPE RFI).

## If something goes wrong

- `cannot open the database: wrong MUSTER_DB_KEY`: window 2's key doesn't match window 1's.
- Web page shows `Network Error` / nothing: the server in window 1 isn't running.
- `409 already has a live score`: Alpha already has a score in session 1. Scan Bravo, or re-run `--reset`.
- Want a clean start: stop the server, then `python live_app.py --reset`.
