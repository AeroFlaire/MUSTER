# MUSTER

**Modern Unified System for Tracking, Evaluation, and Reporting**

MUSTER replaces manual paper workflows with trustworthy, tamper-evident digital records.
In priority order of stakeholder:

1. **Fitness scoring — AFT and IOCT** (primary: DPE)
2. **Classroom attendance** (secondary: Dean)
3. **Arms-room Sensitive Item accountability** (tertiary: arms room)

The core design principle is **trust nothing at the tap.** A convenient record that
is easy to forge is worse than paper. Every scan is a *claim* to be verified, and
anything that matters is signed by the cadet's own CAC.

---

## How it works

| Function | Identify the cadet | Prove it / agree to it |
|---|---|---|
| **AFT** | Grader scans CAC barcode | Cadet signs scorecard at the score compiler (CAC + PIN) |
| **IOCT** | Grader pairs RFID wristband to cadet (CAC barcode) | Gates record raw times; cadet signs their time at the score compiler |
| **Attendance** | Instructor scans CAC barcode | Instructor's own eyes (low assurance, by design) |
| **Arms room** | — | Cadet signs the sign-out record (CAC + PIN) |

The CAC signature uses the card's PIV Authentication key through a **contact** reader.
MUSTER never sees the PIN. Signatures are checked against the public key recorded
when the cadet **enrolled**, so no live DoD PKI connection is needed.

> **Status:** the signing path is tested with software keys. It has **not** yet been
> tested on a real CAC (needs a non-GFE laptop for OpenSC). First Sprint 1 task.

---

## Architecture — one app, three trust zones

| Zone | Name | Trust | What lives here |
|------|------|-------|-----------------|
| 1 | **Edge** | Not trusted | Grader app, score compiler station, IOCT gates, attendance scanner, arms-room station. Sends claims. |
| 2 | **Gatekeeper** | Verifies | API (TLS 1.3). Checks roles, parses barcodes, hashes EDIPIs, verifies CAC signatures, buffers during outages. The only caller of the Vault. |
| 3 | **Vault** | Trusted | SQLite records + append-only hash-chained audit log. Exports signed scores to DPE/AMS. |

See `docs/architecture/overview.md`.

---

## Repository structure

```
muster/
├── docs/
│   ├── architecture/        System overview and block diagrams
│   ├── design/              Authentication, audit log, fitness flow
│   ├── requirements/        Traceability (SR-1…SR-6 → spec → test)
│   ├── interfaces/          Interface definitions (ICDs)
│   ├── design-proposals/    Individual design proposals
│   └── logo/
├── edge/                    ZONE 1 — untrusted
│   ├── fitness-grader/      AFT grader web app
│   ├── score-compiler/      Cadet review + CAC signing station (AFT and IOCT)
│   ├── ioct-gates/          RFID start/end gates + wristbands
│   ├── attendance/          Classroom barcode scanning
│   └── arms-room/           Weapon sign-out station
├── gatekeeper/              ZONE 2 — verification layer
│   ├── api/
│   ├── auth/
│   └── offline-sync/
├── vault/                   ZONE 3 — trusted records (Python package: muster_vault)
│   ├── muster_vault/
│   └── tests/
└── scripts/
    └── vault_demo.py        End-to-end demo with tamper attacks
```

---

## Getting started

```bash
git clone https://github.com/AeroFlaire/MUSTER.git
cd MUSTER/vault
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
python ../scripts/vault_demo.py
```

**Prerequisites:** Python 3.10+, git. Synthetic data only — never commit real PII
or card data (`.gitignore` blocks common patterns).

---

## Team & ownership

| Zone / Module | Primary | Alternate |
|---------------|---------|-----------|
| Edge          | Rhemmy  | Alena     |
| Gatekeeper    | Alena   | Rhemmy    |
| Vault         | Arjo    | Q         |
| Physical      | Q       | Arjo      |

---

## Course

XE401: Integrative System Design I — United States Military Academy
Advisor: COL Malcolm Haynes
