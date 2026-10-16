# Edge — Score Compiler Station

**Zone 1 — untrusted.** Owner: Edge team (hardware: Physical).

The station where a cadet reviews and signs their own score. Shared by AFT and IOCT.

## Hardware
- USB contact CAC reader (CCID), used through OpenSC / PKCS#11.
- For IOCT: an RFID reader to read the cadet's wristband tag.

## Flow
1. The station pulls the cadet's pending score from the Gatekeeper:
   - **AFT:** the scorecard the grader submitted.
   - **IOCT:** the cadet taps their wristband; the station shows the run time computed from the gate reads.
2. The station shows the score **and the exact bytes to be signed** (from `canonical.py`).
3. The cadet inserts their CAC and enters their PIN **into the card**. The card's PIV
   Authentication key signs those bytes. MUSTER never sees the PIN.
4. The station sends the signature to the Gatekeeper, which verifies it against the
   cadet's **enrolled** public key and passes it to the Vault.
5. The Vault stores the signed bytes + signature and issues a receipt to the cadet.

If the cadet disagrees with the score, they choose **Dispute** instead of signing.
That is recorded too; nothing is silently dropped.

## Honesty note
The CAC signing path has **not yet been tested on real hardware.** It needs a
non-GFE laptop (OpenSC conflicts with ActivClient). First Sprint 1 task.
