# Gatekeeper full demo script, Sprint 1 review (L26, 29 Oct)

Owner: Alena. One person, one laptop, one continuous recording. Proves PBI-04 and PBI-05 and shows the IOCT timing path live. Synthetic data only. Target length: under 2 minutes for the Gatekeeper part (the full run is longer, so cut to the scenes you need or agree a time with the team).

Rubric row: "A grader write returns 200, a cadet write returns 403 with an error screen, and a forced failure writes 0 records."

Instructor ask: a full run on one device by one person, and it must test different access levels.

## What the demo proves

| Claim | Where it shows | Acceptance criterion it answers |
|---|---|---|
| Grader write succeeds | Scene 1 (terminal) and the browser console: open event 201, five components 200, submit 200. Each write adds 1 record and 1 audit entry | PBI-04: a POST creates one fitness record and one audit entry together |
| Every access level is tested | Scene 2: 7 access levels against 6 write endpoints. 42 attempts, 0 records added. Faked `X-Role` header is 401 | PBI-05: no role gets 401 or 403 and writes nothing; grader can create, cadet cannot |
| Cadet write returns 403 with an error screen | Browser console: pick "cadet", click run, red "Request rejected (403)" banner | PBI-05 |
| Timing path is live | Scene 3: pair wristband TAG-017, START and END gate reads by the gate station, compute gives 2:35.00, matching the shared pack. Gate station is refused compute | IOCT writes go through the same one-transaction rule |
| Forced failure writes 0 records | Scene 4: audit write broken on purpose, POST fails with 500, 0 new records and 0 new audit entries. Repair it and the same POST works | PBI-04: if the audit write fails, no record is saved |
| Tampering is caught | Scene 5: audit chain verifies, then one entry is edited and the verifier fails at entry 22 | Vault audit chain |

The rubric says "returns 200". Opening a record returns 201 Created and entering a component returns 200 OK. Both are success. Say "200 and 201".

## Before you record (checklist)

1. `git pull`, then run the unit tests against the real Vault: from `gatekeeper\api`, `python -m unittest discover -s tests -v`. Expect 67 tests and `OK`. If anything fails, send me the output before you record.
2. `pip install -r requirements.txt` (once).
3. Set the key in the same terminal: `$env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"`. The demo makes its own throwaway database key (the Vault database is now encrypted), so nothing else is needed.
4. Do one dry run of each part below. Both build their own throwaway database, so nothing can be damaged and you can repeat as often as you like.
5. Make the terminal readable: VS Code terminal, font size 16 or bigger, dark theme, close other panels.
6. Test the audio. The rubric deducts for poor audio.

## Part A: terminal run (scenes 1 to 5)

From `gatekeeper\api` in PowerShell:

```
python demo_full_run.py --pause
```

`--pause` waits for Enter between scenes so you can talk at your own pace.

## Part B: browser console (the error screen)

Open a second terminal in `gatekeeper\api`, set the key again, and run:

```
python demo_full_run.py --screen
```

Leave it running. Open `docs\interfaces\live_demo.html` in your browser. Pick an access level, pick a cadet, click the run button. Show at least: grader (green Saved banner), cadet (red Request rejected, 403), and no login (401). Press Ctrl+C in the terminal when done. The token file is deleted when it stops.

## What to say

**Opening (10 s).** "I own the Gatekeeper, the API between the web app and the Vault. This is a real Vault database and real HTTP calls, with synthetic data. I will run the whole path on this one laptop and test every access level."

**Scene 1, grader scorecard (25 s).** "A grader opens a scorecard, enters five components and submits. Look at the counts: each write added exactly one record and one audit entry. The cadet's ID is stored as a hash only."

**Scene 2, access levels (35 s).** "Now seven access levels against all six write endpoints. Grader and OIC can write. Compiler station, gate station and admin can do only their own jobs. A cadet gets 403. No login gets 401, and a faked role header does nothing, because the role is read from the Vault, not from the request. 42 attempts, zero records added by anyone who should not."

**Scene 3, timing run (25 s).** "A grader pairs a wristband. The gate station sends a start read and an end read. The Vault computes 2:35.00, which matches the shared test pack. The gate station cannot compute a run, so it is refused. These gate reads are simulated, not real RFID hardware."

**Scene 4, forced failure (20 s).** "I break the audit log on purpose and send a valid grader write. It fails with zero new records and zero new audit entries. I repair it and the same request works."

**Scene 5, tamper check (15 s).** "The audit chain verifies. I edit one entry and the verifier fails at that exact entry."

**Browser (20 s).** "This is the screen a user sees. Grader saves. Cadet is rejected with 403 and a plain message."

**Close (5 s).** "PBI-04, the one-transaction write, and PBI-05, the role check, are covered by 67 passing tests."

## Recording on Windows

- **Easiest:** Xbox Game Bar. Click the window, press `Win + Alt + R` to start and again to stop. Turn on the microphone in capture settings first. Videos save to `Videos\Captures`.
- Or record in Teams, or with OBS.
- Record Part A then Part B in one take if you can. If the instructor wants literally one take, alt-tab between the terminal and browser.
- Save a copy to the laptop you will present from, and play it once before class.

## Evidence for the PBI cards (one piece each)

| Criterion | Evidence to show |
|---|---|
| PBI-02: contract v0 covers fitness record, score, audit event | `docs/interfaces/api-contract-v0.md` (revision 2.1) |
| PBI-03: stub endpoints return the contract examples | The 21 stub tests pass |
| PBI-04: one POST writes one record and one audit entry | Scene 1, and `test_live_vault.py` (16 tests) |
| PBI-04: audit failure leaves no record | Scene 4, and the `test_rollback_*` tests |
| PBI-05: no role gets 401 or 403 and writes nothing | Scene 2, browser console, `test_role_check.py` (15 tests) |
| PBI-05: grader can create, cadet cannot | Scenes 1 and 2, browser console |
| IOCT writes live | Scene 3, and `test_live_ioct.py` (15 tests) |

## Be honest about what is not done (for the unfinished-work minutes)

- Gate reads are simulated. Real RFID hardware is Eliakim's part and is not wired in here.
- Signing, dispute, reopen, supersede and audit reads still return canned examples.
- Staff tokens are a demo shortcut. Real sign-in with a CAC is not built.
- The Gatekeeper reads only the test pack barcode format. The real CAC barcode parser is not built.
- The grader picks each cadet's age group for now. The pass rule (60 on every event) and the columns (men M|C, women F) are settled.
- Encryption at rest is the Vault's card (Arjo) and is not shown as done.
- This is a web demo, not a phone app. Ask the instructor whether a phone browser page is acceptable.

## If asked

- **Why 401 and 403?** 401 means "we do not know who you are", 403 means "we know, and you may not".
- **Why not check the role in the web app?** The web app is untrusted. The Gatekeeper checks every request and the Vault checks again.
- **What stops a record being saved without its audit entry?** Both are written inside one database transaction. If either fails, both are undone.


---

# Sprint 2 additions (L33 review, 19 Nov)

`python demo_full_run.py --pause` now has seven scenes. Scene 5 (attendance) and scene 6 (who sees which scores) are new:

| Claim | Where it shows | Criterion |
|---|---|---|
| An instructor marks their own section | Scene 5: 10 of 10 cadets marked, 1 record and 1 audit entry each | PBI-09 criterion 1 |
| Another instructor's section is refused and logged | Scene 5: 3 attempts, each 403, each 1 rejected-attempt audit entry, 0 records | PBI-09 criterion 2 |
| A cadet not on the roster is refused | Scene 5: 3 attempts, 403 | PBI-09 criterion 3 |
| 0 raw barcodes or IDs stored | `test_vault_holds_no_raw_barcode_or_id` in `tests/test_live_attendance.py` | PBI-09 criterion 4 |
| Grader and OIC see all scores, a cadet sees only their own | Scene 6: grader 4 scores, OIC 4 scores, each cadet 1 name only | Demo 3 |
| A cadet cannot write, a grader cannot supersede | Scene 6: both 403, 0 audit entries added | Role test |
| Tamper is caught | Scene 7 (was scene 5) | PBI-10 |

API steps to say out loud (about 40 seconds):

1. "Scene 1: a grader scans, enters five results, and submits. The Gatekeeper looks up the points from the official tables, nobody types a score, and the response shows the total and PASS or FAIL."
2. "Scene 2: every access level against every write. 42 attempts, 0 records, and a faked role header is refused."
3. "Scene 3: gate reads come in, the Vault computes the time, and the run is graded with the DPE grade scale: points, letter grade, PASS or FAIL, and TAB for A- or better."
4. "Scene 5: Instructor A marks their section. Instructor B tries A's section and strangers, gets 403 every time, and each refusal is written to the audit log."
5. "Scene 6: the grader and the OIC see every score. Each cadet token sees only that cadet's own. A cadet cannot write, and a grader cannot correct a score."
6. "Scene 7: the audit chain verifies, one entry is edited, and the verifier names it."

Then run `python run_security_tests.py` and show `docs/evidence/gatekeeper-tests-1-2-results.md`.

Not done, say so plainly: real CAC sign-in, the real CAC barcode parser, real gate hardware, the DPE IOCT standard, the manual typed-time fallback for a failed gate (needs a Vault field), and the AMS export (Arjo).
