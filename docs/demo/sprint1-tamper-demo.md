# Sprint 1 review demo (two videos, ≤ 2 minutes each)

Synthetic data only. Both videos use one running server, recorded back to back.

- **Video 1: barcode scanning works.** A card scan identifies the cadet, the ID is
  hashed, the raw barcode is stored nowhere, and a bad barcode is rejected.
  (PBI-04: "raw barcode string appears in 0 log lines and 0 database rows".)
- **Video 2: AFT through tamper evidence.** AFT entry → encrypted Vault with an audit
  entry → a rogue admin edits the score → caught → rewrites history → caught.
  (PBI-04, PBI-05, SR-3, SR-4.)

## Once, before class

```powershell
cd <your class-repo clone>
git pull
python -m venv .venv
.venv\Scripts\activate
pip install -e "vault[dev]"
pip install -r gatekeeper\api\requirements.txt
pip install reportlab
python scripts\make_test_barcodes.py      # print test_barcodes.pdf ("fake CACs")
```

- **Print** `test_barcodes.pdf`. Have one real product barcode handy (a water bottle).
- **Scanner check:** scan a card into Notepad. If an Enter follows the text, the web form
  submits as soon as you scan. That's fine for Video 1. For Video 2, type the scores
  first and scan last.
- **Font size:** make PowerShell and browser text big (Ctrl + scroll) for the projector.

## Setup, right before recording (about 2 minutes)

**Window A, server** (PowerShell):

```powershell
cd <repo>\gatekeeper\api
..\..\.venv\Scripts\activate
$env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
$env:MUSTER_DB_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:MUSTER_DB_KEY | Set-Clipboard          # copies the key for window B
python live_app.py --reset
```

Leave it running. Arrange it so its request lines are visible.

**Window B, admin/checker** (PowerShell):

```powershell
cd <repo>
.venv\Scripts\activate
$env:MUSTER_DB_KEY = Get-Clipboard
$db = "gatekeeper\api\muster_demo.db"
python -m muster_vault integrity --db $db   # should say PASS (no scores yet)
```

**Browser:** open `<repo>\docs\interfaces\index.html`. Set **Active Role = Grader** and
**Session ID = 1**, and **clear** the Raw Barcode String box.

---

## Video 1: barcode scanning (≈ 90 s)

| # | Do | Say |
|---|---|---|
| 1 | Hold up the printed **Cadet Alpha** card | "Our scanner can't read real CAC barcodes yet; that's Sprint 2. These are synthetic test cards in the same role." |
| 2 | Click the Raw Barcode box, **scan Alpha** | "The scanner acts as a keyboard: the card's string lands in the form." |
| 3 | Click **Execute** (if the scanner's Enter didn't already) | "The page sends it to the Gatekeeper." |
| 4 | Scroll the response to the first `201 CREATED`: `cadet_name: "Alpha, Cadet"`, `cadet_edipi_hash: 16f3…` | "The Gatekeeper read the ID, matched the right cadet, and turned the ID into a keyed hash. That 64-character hash is all MUSTER keeps." |
| 5 | Point at window A's request lines | "The server log shows only the request path and status, never the barcode." |
| 6 | Window B: `python -m muster_vault find --db $db --text 9000000001` | "Open the database *with the key* and search every table for the cadet's ID: **0 matches**." |
| 7 | `python -m muster_vault find --db $db --text PDF417_TEST_DATA` | "And for the raw barcode string: **0 matches**. It was used once and thrown away." |
| 8 | Clear the barcode box, **scan the water bottle**, Execute | "A barcode that isn't a cadet card is rejected: **400, not in a format the Gatekeeper can read**, and nothing is written." |

## Video 2: AFT through tamper evidence (≈ 2 min)

| # | Do | Say |
|---|---|---|
| 1 | Clear the barcode box. Type Bravo's results: MDL `250`, HRP `45`, SDC `1:48`, PLK `2:50`, 2MR `14:30`. Then **scan Cadet Bravo** and Execute | "A grader enters Cadet Bravo's AFT. Opening the score, each event, and the submit each go through the Gatekeeper into the Vault." |
| 2 | Point at `201` → five `200`s → submit `200`, and `--- Flow paused` | "Each step wrote its record and an audit entry together. A grader can't sign; that's the cadet's step at the score compiler." |
| 3 | Window B: `python -m muster_vault integrity --db $db` | "The integrity check: audit chain **PASS**, and both scores match their audit trail." |
| 4 | `Select-String -Path "$db*" -Pattern "Bravo" -SimpleMatch` | "Someone who copies the database file searches it for the cadet's name: no output. The file is encrypted (AES-256)." |
| 5 | `python scripts\admin_tamper.py show --event 2` | "Now a rogue admin who *has* the key opens the database. Here is Bravo's score." |
| 6 | `python scripts\admin_tamper.py edit-score --event 2 --component MDL --points 60` | "They change Bravo's deadlift straight in the database, skipping MUSTER. The edit succeeded." |
| 7 | `python -m muster_vault integrity --db $db` | "Caught: **Score 2 TAMPERED, MDL: database says 60 pts, audit log says 100**. The edit left no history, so the record and its history disagree." |
| 8 | `python scripts\admin_tamper.py cover-tracks --event 2 --component MDL` | "So the admin removes the log's lock and rewrites the history to match." |
| 9 | `python -m muster_vault integrity --db $db` | "Caught again: **audit chain FAIL at entry 24**. Changing history breaks the chain of fingerprints. We can't stop someone with full access from editing, but we can't miss it either. That's tamper-*evident*." |

(The entry number in step 9 may differ slightly; read it off the screen.)

**Honest limits, ready for Q&A:**
- An admin who edits the score, edits the log, *and* recomputes every fingerprint after
  it is out of MVP scope. The countermeasure is copies kept outside the database (the
  cadet's receipt and the printed chain head). Signed checkpoints are the named upgrade.
- Points show 100 per event because AFT scoring tables come in Sprint 2 (DPE RFI).
- The scanner doesn't read real CAC barcodes yet (Sprint 2). Real-CAC signing is
  untested pending a non-GFE laptop.
- The encryption is AES-256, but this SQLCipher build is not FIPS 140-3 validated.

## If something goes wrong

| Symptom | Fix |
|---|---|
| `cannot open the database: wrong MUSTER_DB_KEY` | Window B's key ≠ window A's. Re-copy it (`$env:MUSTER_DB_KEY` in A). |
| Page shows nothing / network error | Window A isn't running, or you opened the page before starting it. Refresh. |
| `409 … already has a live score` | That cadet already has a score in session 1. Use another card, or restart A with `--reset`. |
| Video 1 auto-submitted the defaults | Fine: it still proves the scan. Just narrate it. |
| Start over | Ctrl+C in A, run `python live_app.py --reset`, re-copy the key to B. |
