# Authentication Design

## Identify vs. prove
- **Identify (low assurance):** CAC barcode, RFID wristband. Says who someone *claims*
  to be. Copyable. Used only to look up a record. (NIST SP 800-116: a static identifier
  is not authentication.)
- **Prove (high assurance):** CAC + PIN signature through a **contact** reader.
  Shows the real card is present, the holder knows the PIN, and — because the cadet
  signed the actual record — they can't later deny it (non-repudiation).

## Barcode handling (Gatekeeper)
1. Scan the **back (Code39)** barcode — less PII than the front PDF417.
2. Parse with `jkusner/CACBarcode` (fixed-width, version-dependent, Base32 EDIPI — don't hand-roll).
3. `identity.hash_edipi(edipi, key)` → HMAC-SHA256 with `MUSTER_EDIPI_KEY`.
4. Discard the raw string. Only the hash goes to the Vault.

Why HMAC, not plain SHA-256: there are only ~10 billion possible EDIPIs; plain hashes
could all be precomputed. HMAC needs the secret key too.

## Signing (score compiler and arms room)
1. Vault issues a challenge: `canonical.signing_payload(purpose, nonce, issued_at, record)`.
2. Station shows the record; cadet inserts CAC and enters PIN **into the card**.
3. Card signs the bytes with its PIV Authentication key
   (`pkcs11-tool --sign --mechanism SHA256-RSA-PKCS`).
4. Gatekeeper verifies; Vault re-verifies with `signatures.verify_signature` against the
   key in `enrolled_key`, checks the nonce is unused and unexpired, and that the record
   hasn't changed. Then stores bytes + signature.

What the signed bytes include, and why:
| Field | Stops |
|---|---|
| `purpose` | Using a score signature to sign out a weapon |
| `nonce` (single-use) | Replaying a captured signature |
| `issued_at` + 5-min expiry | Signing stale screens |
| full `record` | Moving a signature to a different score |

## Enrollment-bound keys
At enrollment, an admin records each person's PIV public key (`identity.enroll_key`).
Signatures are checked against **that** key. No live DoD PKI is needed. A re-issued or
lost CAC → `revoke_key`, then enroll the new one. Full PKI validation is a possible
future upgrade (scope with COL Haynes).

## Validation status
Tested with software RSA/ECDSA keys standing in for a CAC. **Not yet tested on a real
card.** Plan (first Sprint 1 task, needs a non-GFE laptop):
`pcsc_scan` → `pkcs11-tool --list-objects` → `pkcs11-tool --sign --id 01` →
`openssl dgst -sha256 -verify` → expect `Verified OK`. Then feed that signature into
`signatures.verify_signature` to confirm the Vault accepts real-card output.
