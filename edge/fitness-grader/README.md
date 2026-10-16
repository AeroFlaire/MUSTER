# Edge — Fitness Grader App (AFT)

**Zone 1 — untrusted.** Owner: Edge team.

The web app a DPE grader uses on the testing floor.

## Flow
1. Grader scans the cadet's CAC barcode (back, Code39) to identify the cadet.
   The raw barcode is sent to the Gatekeeper, which hashes the EDIPI and discards the raw string.
2. Grader enters raw results for each AFT event.
3. Grader submits the scorecard to the **score compiler**.

## Rules
- Talks **only** to the Gatekeeper API. Never writes to the database.
- Everything this app sends is a *claim*. The Gatekeeper checks the grader's role
  and the Vault records who entered what.
- Never stores the raw barcode or DoD ID locally.

## Vault calls (through the Gatekeeper)
`fitness.open_event` → `fitness.record_component` (one per event) → `fitness.submit`
