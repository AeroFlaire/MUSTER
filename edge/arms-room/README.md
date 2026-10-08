# Edge — Arms Room Station

**Zone 1 — untrusted.** Owner: Edge team. Priority: tertiary stakeholder.

## Flow
1. Arms-room NCO selects the weapon (serial) and action (sign-out / return).
2. Cadet inserts CAC into the contact reader and enters PIN into the card.
3. The card signs the canonical sign-out record (weapon serial + cadet + time + nonce).
4. Gatekeeper verifies against the enrolled key; Vault stores the event and signature.

Same signing mechanism as the score compiler.
