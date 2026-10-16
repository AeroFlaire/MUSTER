# Edge — IOCT RFID Timing Gates

**Zone 1 — untrusted.** Owner: Physical / Edge.

## Hardware
- RFID wristbands (one per cadet on the course at a time).
- A **start gate** and an **end gate**, each an RFID reader.

## Flow
1. **Pairing (at the start line):** the grader scans the cadet's CAC barcode and the
   wristband tag. The Vault records "tag X belongs to cadet Y for this run."
2. **Start gate:** reads the tag and sends a raw read: `{tag, gate: START, timestamp}`.
3. **End gate:** same, with `gate: END`.
4. The cadet takes the wristband to the **score compiler** to sign their time.

## Rules
- Gates send **raw reads only**. They never compute or send an elapsed time.
  The Vault computes `end − start` itself.
- A tag proves *which wristband* passed the gate, not *which cadet*. Identity is
  proven by the pairing (grader watching) and then by the cadet's CAC signature.
- **Clock risk:** if the two gates have separate clocks, they must be synced, and the
  drift recorded. Preferred: one controller timestamps both gates.
