# MUSTER

**Modern Unified System for Tracking, Evaluation, and Reporting**

MUSTER replaces three manual workflows — classroom attendance, ACFT/IOCT fitness
scoring, and arms-room Sensitive Item (SI) accountability — with a single automated
system. A cadet authenticates with their CAC over NFC, and the system produces
trustworthy, tamper-evident records for instructors, AMS, and arms-room personnel.

The core design principle is **trust nothing at the tap.** A convenient record that
is easy to forge would be worse than paper, because it would let false entries through.
Every scan is treated as a claim to be verified, not as proof.

---

## Architecture at a glance

MUSTER is organized into three zones. The repo folders mirror these zones so work
can be split cleanly across the team.

| Zone | Name | Trust | What lives here |
|------|------|-------|-----------------|
| 1 | **Edge** | Not trusted | NFC reader, arms-room station, phone check-in app. Where cadets tap in. Every tap is treated as a claim to be checked. |
| 2 | **Gatekeeper** | Verifies | Checks every request: is the cadet enrolled? does this action need a PIN? is the phone really on the classroom network (server-verified)? Buffers records if the network drops. |
| 3 | **Vault** | Trusted | Fully encrypted records, role-based access, tamper-evident audit log, and score push to AMS. |

See `docs/architecture/` for the block diagrams.

---

## Repository structure

```
muster/
├── docs/
│   ├── design-proposals/   Individual design proposals (team members')
│   ├── requirements/       Requirements traceability (SR-1…SR-5 → spec → test)
│   ├── architecture/       System block diagrams (source + exports)
│   ├── interfaces/         Interface definitions (ICDs)
│   └── logo/               Project logo for the PDR title slide
├── edge/                   ZONE 1 — untrusted edge devices
│   ├── nfc-reader/         CAC-over-NFC read path
│   ├── arms-room/          Arms-room station (weapon sign-out, PIN-gated)
│   └── phone-app/          Mobile check-in (backup attendance path)
├── gatekeeper/             ZONE 2 — verification layer
│   ├── auth/               Enrollment check, PIN handling for high-stakes actions
│   ├── policy/             Request rules, server-side location verification
│   └── offline-sync/       Local buffering + sync on network restore
├── vault/                  ZONE 3 — trusted records
│   ├── db/                 Encrypted database, retention policy
│   ├── audit-log/          Tamper-evident SI event log
│   └── ams-integration/    Score push to AMS
├── tests/                  Evaluation plan (Tests 1–4)
└── scripts/                Setup / build / utility scripts
```

---

## Getting started

### Clone

```bash
git clone https://github.com/AeroFlaire/muster.git
cd muster
```

### Prerequisites


- git
- Runtime for the Gatekeeper/Vault backend — TBD (e.g. Python 3.x or Node.x)
- Runtime for the phone check-in app — TBD (e.g. Node.x / Expo)
- A database for the Vault — TBD

### Build & run by zone

Each zone runs independently so the team can work in parallel. Commands are placeholders
until each subsystem lands its code — replace the TBDs as you build.

**Zone 2 — Gatekeeper** (verification server)
```bash
cd gatekeeper
# TODO: install deps + start the server
```

**Zone 3 — Vault** (encrypted records + audit log)
```bash
cd vault
# TODO: init the database, apply schema/migrations, start the service
```

**Zone 1 — Edge** (NFC reader / arms-room station / phone app)
```bash
cd edge/phone-app
# TODO: install deps + run the app
```

### Tests

```bash
cd tests
# TODO: run the evaluation-plan tests (Tests 1–4). Synthetic data only — no real PII.
```

---

## Team & ownership

| Zone / Module | Primary | Alternate |
|---------------|---------|-----------|
| Edge          | TBD     | TBD       |
| Gatekeeper    | TBD     | TBD       |
| Vault         | TBD     | TBD       |
| Physical      | TBD     | TBD       |


---

## Course

XE401: Integrative System Design I — United States Military Academy
Advisor: COL Malcolm Haynes
