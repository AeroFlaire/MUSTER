# Fitness Flow Design (AFT + IOCT) — Primary Stakeholder: DPE

## Score lifecycle

```
ENTERED ──submit──▶ SUBMITTED ──cadet signs──▶ SIGNED ──export──▶ EXPORTED
   ▲                  │    ▲                     │
   │               dispute │                     │ supersede (OIC correction)
   │                  ▼    │                     ▼
   └──── reopen ─── DISPUTED                  SUPERSEDED (+ new linked ENTERED event)
```

| Step | Who | Vault call |
|---|---|---|
| Open AFT scorecard | Grader (scans CAC barcode) | `fitness.open_event` |
| Enter each event | Grader | `fitness.record_component` |
| Pair IOCT wristband | Grader | `ioct.pair` |
| Gate reads | Gate controller | `ioct.record_gate_read` |
| Compute IOCT time | Grader / station | `ioct.compute_run` |
| Send to compiler | Grader (optionally signs) | `fitness.submit` |
| Show + sign | Score compiler + cadet CAC | `fitness.request_cadet_challenge`, `fitness.accept` |
| Refuse to sign | Score compiler | `fitness.dispute` |
| Fix before signing | OIC | `fitness.reopen` |
| Fix after signing | OIC | `fitness.supersede` |
| Receipt | Score compiler | `receipts.issue` |
| Send to DPE/AMS | OIC | `export.export_signed` |

## AFT events
`MDL` deadlift, `HRP` hand-release push-up, `SDC` sprint-drag-carry, `PLK` plank,
`2MR` 2-mile run. Stored as rows, not columns, so the list can change.
**Confirm with DPE (LTC Inman):** event list, and the source of the raw→points tables.

## IOCT timing
- Wristband identifies the band, not the cadet. Identity = grader-watched pairing + cadet signature.
- Gates send **raw reads only**. The Vault computes: **start = last START read**,
  **end = first END read** after pairing (a cadet waiting at the start is read repeatedly;
  the last read is when they left).
- Reads stay stored even if they match nobody (`ioct.unmatched_reads`).
- Reused band: reads at or just after the previous run's finish are ignored.
- Times outside 1:00–15:00 are refused for OIC review (bounds to confirm with DPE).
- **Clock risk:** both gates must share one clock or be synced. Put on the risk slide.

## Open decisions for the team
1. **Grader signature on submit** — `REQUIRE_GRADER_SIGNATURE` (off by default).
   On = both "grader says X" and "cadet agrees X" are provable.
2. Points: grader-entered (now) vs. computed from DPE tables in the Vault.
3. Receipt delivery: print at the station vs. email.
