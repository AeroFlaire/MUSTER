# Requirements Traceability Matrix

Traces each security requirement from customer need → requirement → design impact →
measurable specification → verification test. The **Specification** column is where the
team still needs to pin down concrete, testable numbers/standards (marked TODO).

| ID | Requirement | Design impact | Specification (measurable) | Verification |
|----|-------------|---------------|-----------------------------|--------------|
| **SR-1** | A scan is tied to the right cadet. For high-stakes actions (signing out a weapon), a tap alone isn't enough. | PIN required for arms-room actions. | TODO: define which actions require PIN; PIN entry standard; lockout after N failed attempts. | Test 1 |
| **SR-2** | Mobile check-in must reject attempts from outside the classroom network. | Server confirms location; the app is never trusted to prove it. | TODO: define server-side location check; rejection criteria for spoofed/off-network attempts. | Test 2 |
| **SR-3** | Every SI event (who/what/when) recorded in a tamper-evident, on-demand-reportable log. | Tamper-evident log holding full history. | TODO: define tamper-evidence mechanism (e.g. hash chaining); report format; retention. | Test 1 |
| **SR-4** | Encrypt data in transit and minimize retained CAC data — no full DoD ID unless required. | AES-256 (or equivalent) encryption + retention policy. | TODO: cipher/standard (FIPS 197), TLS version in transit, exactly which fields are retained. | Test 3 |
| **SR-5** | Continue operating during a network outage. | Save scans locally, sync later. | TODO: local buffer size/duration; dedup + ordering guarantees on sync. | Test 4 |

---

## Verification tests (from the design proposal)

- **Test 1 (SR-1, SR-3):** Try to cheat — tap for a friend not in the class, try to fake a
  weapon record. **Pass** = system blocks it and every real weapon sign-out appears in the
  log. **Goal:** zero fakes through.
- **Test 2 (SR-2):** Attempt check-in from outside the classroom and by faking the Wi-Fi.
  **Pass** = attempts rejected. **Goal:** zero false check-ins accepted.
- **Test 3 (SR-4):** Inspect data in transit and at rest. **Pass** = encrypted, full DoD ID
  not stored, only needed info kept.
- **Test 4 (SR-5):** Cut the network mid-session, then restore. **Pass** = scans saved offline
  and synced back with no duplicates or missing records.

---

## Relevant standards (to cite in PDR)

- **NIST SP 800-73 / 800-78** — PIV; CHUID authentication mechanism withdrawn (basis for
  requiring PIN over the contactless path rather than trusting the CHUID).
- **FIPS 201** — PIV credential standard (CAC).
- **FIPS 197** — AES.
- TODO: standard governing AMS score submission; TLS guidance (e.g. NIST SP 800-52).
