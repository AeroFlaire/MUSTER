# Encryption at rest and retention (SR-4)

Card: *(3.5) PBI-05 L25 | Encrypt the database at rest and add the retention setting*. Owner: Arjo.

## Encryption at rest

**What:** the whole Vault database file is encrypted with **SQLCipher** (SQLite with
AES-256 on every page). The write-ahead log (`-wal`) and temp files are encrypted
too, and temp tables are kept in memory.

**Why the whole file and not single columns:** names sit in `cadet`, scores sit in
`fitness_component`, and the `audit_log` entries carry full scorecards. Encrypting
columns one by one would miss some of those and would break searching and sorting.
Whole-file encryption locks the entire filing cabinet in one step.

**Key:** 32 random bytes (64 hex characters) in the `MUSTER_DB_KEY` environment
variable. It's passed to SQLCipher as a raw key, so there's no slow passphrase step
on every connection.

```powershell
$env:MUSTER_DB_KEY = (python -m muster_vault new-key)   # once per database
```

- The key is **never committed**, never logged, and never echoed back in error messages.
- **Lose the key and the data is gone.** Store it like a password (team password
  manager, or the VM's secret store), not in the repo or Teams.
- A file database will not open without a key. `":memory:"` (unit tests) needs none,
  because nothing is written to disk.
- Old plaintext databases convert once: `python -m muster_vault encrypt-db --plain old.db --db new.db`.

**What it protects against:** a copied database file, a backup, a lost laptop or
disk, or someone browsing the VM's file system without the key. To them the file is
random bytes. There's no `SQLite format 3` header, no names, no scores and no hashes.

**What it does NOT protect against (stated honestly):**
- Someone who has the key, or who controls the running app (the key is in the
  process environment). That is the insider case. The **audit hash chain** and the
  **cadet's CAC signature** detect their edits; encryption doesn't stop them.
- **FIPS 140-3:** SQLCipher uses AES-256 (FIPS 197) through OpenSSL, but the
  community build used here is **not a FIPS 140-3 validated module**. A validated
  build (SQLCipher's commercial FIPS edition, or an OS-level validated module) is
  the named upgrade if a fielded system requires it.
- Data in transit is a separate requirement (TLS 1.3 at the Gatekeeper/VM), not built here.

**Evidence (acceptance criterion: "0 readable cadet identifiers or scores when
opened with a plain text search"):**
- `vault/tests/test_encryption_retention.py`: builds a file database with
  synthetic cadets and a signed score. It first confirms the names, scores, ID hashes
  and audit hashes really are in the database, then searches the raw file bytes
  (main + WAL) and finds **none** of them. It also checks that a plain `sqlite3`
  can't open the file, that a wrong key is refused, and that the chain still verifies
  after reopening.
- `scripts/vault_demo.py`: the "OUTSIDER" step does the same search live.
- Manual (PowerShell): `Select-String -Path muster_demo.db -Pattern "Alpha" -SimpleMatch` → no output.

**Dependency:** `sqlcipher3-wheels` (prebuilt for Windows, macOS, and Linux; no
compiler needed). It's a drop-in for Python's `sqlite3`, but it raises its own
exception classes, so code catches `db.IntegrityError`, not `sqlite3.IntegrityError`.

## Retention setting

**What:** each function (FITNESS, IOCT, ATTENDANCE, ARMS) has a retention period in
days, stored in `retention_setting`.

- **Default = PLACEHOLDER (1825 days / 5 years), NOT POLICY**, until the retention
  RFI is answered. Every report flags it as a placeholder.
- Only an **admin** can change it. Each change needs a **source** (the policy
  citation) and writes a `RETENTION_SET` audit entry with the old and new values, so
  "who shortened retention, and when" is always answerable.
- `retention.report()` / `python -m muster_vault retention --db …` lists, per
  function, the setting in force and how many records are past it.

```
python -m muster_vault set-retention --db muster.db --actor 1 --function FITNESS \
    --days 1825 --source "<policy and paragraph from the RFI answer>"
```

**Not built yet, on purpose: deleting expired records.** Two things have to be
decided first:

1. **Policy (RFI):** is an expired record destroyed, or transferred to AMS or an
   archive first? Is the audit trail kept longer than the records?
2. **Design conflict:** audit entries carry full scorecards, and the log is
   append-only and hash-chained. Deleting a score row would leave the score in the
   log, and deleting log entries would break the chain. Options:
   - **(a) Chain over a fingerprint of the data (recommended).** Each entry stores
     `data_hash = SHA-256(data)`, and the chain covers `data_hash` instead of `data`.
     At disposal, the data is erased and the hash stays, so the chain still verifies,
     the erasure itself is a new audit entry, and nothing readable is left. This
     changes the audit entry format, so do it before real data exists.
   - (b) Keep the audit log longer than the records, if policy allows. Simplest,
     but the scores stay in the log.
   - (c) Seal and archive old chain segments (signed checkpoint, then move them to an
     archive). This needs the signed-checkpoint upgrade.

Disposal is a Sprint 2 card once the RFI is answered.
