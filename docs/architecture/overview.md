# MUSTER Architecture Overview

## One app, three trust zones

The trust boundary is the **API**, not a split between programs.

```
 ZONE 1: EDGE (untrusted)            ZONE 2: GATEKEEPER (verifies)        ZONE 3: VAULT (trusted)
 ─────────────────────────           ─────────────────────────────        ──────────────────────
 Grader app (AFT)        ─┐
 IOCT start/end gates    ─┤          • role check                         • working tables
 Score compiler station  ─┼─TLS 1.3─▶• barcode parse + EDIPI HMAC  ──────▶• append-only,
   (contact CAC reader)   │          • CAC signature verify                 hash-chained audit log
 Attendance scanner      ─┤          • offline buffer + resync            • re-verifies signatures
 Arms-room station       ─┘                                               • export ─▶ DPE / AMS
```

A forged entry at the Edge can't reach the Vault without passing the Gatekeeper, and
the Vault re-checks roles and signatures itself (defense in depth).

## Fitness flow (primary)

```
AFT:   grader scans CAC barcode ─▶ enters 5 events ─▶ submit ─▶ score compiler
IOCT:  grader pairs wristband ─▶ START gate read ─▶ END gate read ─▶ Vault computes time
                                                                     ─▶ submit ─▶ score compiler

score compiler: show scorecard ─▶ cadet inserts CAC + PIN ─▶ card signs exact bytes
                ─▶ Gatekeeper + Vault verify vs enrolled key ─▶ SIGNED ─▶ receipt to cadet
                ─▶ (later) export to DPE / AMS
```

## Assurance by function

| Function | Identifier (low) | Proof (high) |
|---|---|---|
| AFT | CAC barcode | CAC signature on scorecard |
| IOCT | Wristband (paired by watching grader) | CAC signature on time |
| Attendance | CAC barcode | Instructor's eyes |
| Arms room | — | CAC signature on sign-out record |

## Hardware (all commodity USB)

- 2-D barcode scanner (PDF417 / Code39)
- USB contact CAC reader (CCID, OpenSC/PKCS#11)
- **New (IOCT):** RFID wristbands + 2 RFID gate readers for IOCT
  (update budget and hardware slides)
