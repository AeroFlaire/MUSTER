-- =============================================================================
-- MUSTER Vault schema (SQLite)
--
-- Two kinds of tables:
--   * Working tables  — the current state (a score's status, who holds a weapon).
--                       These CAN be updated, but only through muster_vault code,
--                       and every change also writes an audit_log entry.
--   * audit_log       — append-only, hash-chained history. Never updated/deleted.
--
-- Raw DoD IDs (EDIPI) are never stored. Only an HMAC-SHA256 of the EDIPI.
-- Times are UTC ISO-8601 strings ("2026-10-08T14:52:03.120Z") unless the column
-- name ends in _ms, which is integer milliseconds since the Unix epoch.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- CORE
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE            -- e.g. "E-2"
);

CREATE TABLE IF NOT EXISTS cadet (
    id           INTEGER PRIMARY KEY,
    edipi_hash   TEXT NOT NULL UNIQUE,    -- HMAC-SHA256(EDIPI), hex. Never the raw ID.
    last_name    TEXT NOT NULL,
    first_name   TEXT NOT NULL,
    company_id   INTEGER REFERENCES company(id),
    class_year   INTEGER,
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at   TEXT NOT NULL
);

-- Staff who operate MUSTER. A compiler/gate station is also an app_user so
-- every write has an accountable actor.
CREATE TABLE IF NOT EXISTS app_user (
    id            INTEGER PRIMARY KEY,
    edipi_hash    TEXT UNIQUE,            -- NULL for station/device accounts
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN (
                      'admin', 'oic', 'grader', 'compiler_station', 'gate_station',
                      'instructor', 'arms_nco')),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT NOT NULL
);

-- Enrollment-bound signing keys. At enrollment we record the public key from the
-- person's CAC (PIV Authentication). Later signatures are checked against THIS key,
-- so no live DoD PKI lookup is needed.
CREATE TABLE IF NOT EXISTS enrolled_key (
    id               INTEGER PRIMARY KEY,
    edipi_hash       TEXT NOT NULL,       -- cadet or staff member
    public_key_pem   TEXT NOT NULL,
    key_fingerprint  TEXT NOT NULL UNIQUE,-- SHA-256 of the DER public key, hex
    algorithm        TEXT NOT NULL CHECK (algorithm IN (
                         'RSA-PKCS1v15-SHA256', 'ECDSA-SHA256', 'ECDSA-SHA384')),
    enrolled_by      INTEGER NOT NULL REFERENCES app_user(id),
    enrolled_at      TEXT NOT NULL,
    revoked_at       TEXT
);
-- One live key per person at a time (a re-issued CAC means revoke + re-enroll).
CREATE UNIQUE INDEX IF NOT EXISTS ux_enrolled_key_active
    ON enrolled_key(edipi_hash) WHERE revoked_at IS NULL;

-- One-time challenges. The Vault issues a nonce; the signed bytes include it;
-- once used it can never be used again (stops replaying an old signature).
CREATE TABLE IF NOT EXISTS signing_challenge (
    id           INTEGER PRIMARY KEY,
    purpose      TEXT NOT NULL,           -- 'FITNESS_ACCEPT', 'SI_OUT', 'SI_IN', 'GRADER_SUBMIT'
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER,                 -- NULL for SI (record doesn't exist until signed)
    nonce        TEXT NOT NULL UNIQUE,
    payload      BLOB NOT NULL,           -- exact bytes to be signed
    issued_at    TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT
);

-- A verified signature, stored with the EXACT bytes that were signed, so anyone
-- can re-check it later without trusting MUSTER.
CREATE TABLE IF NOT EXISTS attestation (
    id               INTEGER PRIMARY KEY,
    purpose          TEXT NOT NULL,
    signer_edipi_hash TEXT NOT NULL,
    signer_role      TEXT NOT NULL CHECK (signer_role IN ('cadet', 'grader', 'arms_nco')),
    key_id           INTEGER NOT NULL REFERENCES enrolled_key(id),
    challenge_id     INTEGER NOT NULL UNIQUE REFERENCES signing_challenge(id),
    payload          BLOB NOT NULL,
    payload_sha256   TEXT NOT NULL,
    signature        BLOB NOT NULL,
    verified_at      TEXT NOT NULL
);

-- -----------------------------------------------------------------------------
-- AUDIT LOG — append-only, hash-chained (SR-3)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    seq          INTEGER PRIMARY KEY,     -- 1, 2, 3 ... no gaps
    ts           TEXT NOT NULL,
    function     TEXT NOT NULL CHECK (function IN (
                     'CORE', 'FITNESS', 'IOCT', 'ATTENDANCE', 'ARMS')),
    action       TEXT NOT NULL,           -- e.g. 'FITNESS_SIGNED'
    actor_id     INTEGER,                 -- app_user.id (NULL = system)
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER,
    data         TEXT NOT NULL,           -- canonical JSON of what happened
    prev_hash    TEXT NOT NULL,           -- entry_hash of seq-1 (64 zeros for seq 1)
    entry_hash   TEXT NOT NULL            -- SHA-256 over all fields above
);

-- The "lock on the app": SQLite has no user roles, so triggers refuse edits.
-- Someone with direct file access can drop these — which is why the chain
-- exists: you can't prevent that, but you will detect it.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: corrections must be appended');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: entries cannot be deleted');
END;

-- -----------------------------------------------------------------------------
-- FITNESS (primary stakeholder: DPE) — AFT + IOCT share the score lifecycle
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS test_session (
    id            INTEGER PRIMARY KEY,
    test_type     TEXT NOT NULL CHECK (test_type IN ('AFT', 'IOCT')),
    session_date  TEXT NOT NULL,          -- YYYY-MM-DD
    location      TEXT NOT NULL,
    oic_id        INTEGER NOT NULL REFERENCES app_user(id),
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
);

-- One cadet's attempt at one test.
-- Status:  ENTERED -> SUBMITTED -> SIGNED -> EXPORTED
--                         |  ^
--                         v  | (OIC reopens)
--                       DISPUTED
--          SIGNED -> SUPERSEDED  (a correction event replaces it; both kept)
CREATE TABLE IF NOT EXISTS fitness_event (
    id                   INTEGER PRIMARY KEY,
    session_id           INTEGER NOT NULL REFERENCES test_session(id),
    cadet_id             INTEGER NOT NULL REFERENCES cadet(id),
    test_type            TEXT NOT NULL CHECK (test_type IN ('AFT', 'IOCT')),
    status               TEXT NOT NULL CHECK (status IN (
                             'ENTERED', 'SUBMITTED', 'DISPUTED', 'SIGNED',
                             'SUPERSEDED', 'EXPORTED')),
    grader_id            INTEGER NOT NULL REFERENCES app_user(id),
    supersedes_event_id  INTEGER REFERENCES fitness_event(id),
    client_ref           TEXT UNIQUE,     -- idempotency key from the Edge (SR-5)
    created_at           TEXT NOT NULL,
    submitted_at         TEXT,
    signed_at            TEXT,
    exported_at          TEXT,
    cadet_attestation_id INTEGER REFERENCES attestation(id),
    grader_attestation_id INTEGER REFERENCES attestation(id),
    dispute_reason       TEXT
);
-- At most one live (non-superseded) event per cadet per session.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fitness_event_live
    ON fitness_event(session_id, cadet_id) WHERE status <> 'SUPERSEDED';

-- One scored AFT event (deadlift, push-ups, ...) — the only true child table.
-- Component count is NOT hard-coded; the allowed codes live in fitness.py.
CREATE TABLE IF NOT EXISTS fitness_component (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES fitness_event(id),
    component   TEXT NOT NULL,            -- e.g. 'MDL', 'HRP', 'SDC', 'PLK', '2MR'
    raw_value   TEXT NOT NULL,            -- as measured: '200', '01:45', '15:30'
    points      INTEGER,                  -- as scored (from DPE scoring tables)
    entered_by  INTEGER NOT NULL REFERENCES app_user(id),
    entered_at  TEXT NOT NULL,
    UNIQUE (event_id, component)
);

-- ---- IOCT RFID timing ------------------------------------------------------

CREATE TABLE IF NOT EXISTS rfid_tag (
    id       INTEGER PRIMARY KEY,
    tag_uid  TEXT NOT NULL UNIQUE,        -- the ID the wristband reports
    label    TEXT,                        -- printed number on the band
    active   INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

-- "Tag X belongs to cadet Y for this run" — created by a grader who is watching.
CREATE TABLE IF NOT EXISTS tag_assignment (
    id           INTEGER PRIMARY KEY,
    tag_id       INTEGER NOT NULL REFERENCES rfid_tag(id),
    event_id     INTEGER NOT NULL UNIQUE REFERENCES fitness_event(id),
    paired_by    INTEGER NOT NULL REFERENCES app_user(id),
    paired_at    TEXT NOT NULL,
    paired_at_ms INTEGER NOT NULL,
    released_at  TEXT
);
-- A wristband can only be on one cadet at a time.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_assignment_active
    ON tag_assignment(tag_id) WHERE released_at IS NULL;

-- Raw gate reads. Evidence — stored even if they don't match any pairing.
CREATE TABLE IF NOT EXISTS gate_read (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES test_session(id),
    tag_uid        TEXT NOT NULL,
    gate           TEXT NOT NULL CHECK (gate IN ('START', 'END')),
    read_at_ms     INTEGER NOT NULL,      -- gate's clock, epoch ms
    gate_device    TEXT NOT NULL,
    received_at    TEXT NOT NULL,         -- Vault's clock
    recorded_by    INTEGER NOT NULL REFERENCES app_user(id),
    client_ref     TEXT UNIQUE            -- idempotency key (SR-5)
);
CREATE INDEX IF NOT EXISTS ix_gate_read_tag ON gate_read(session_id, tag_uid, read_at_ms);

-- The computed time for one IOCT attempt. Elapsed is computed HERE, never by a gate.
CREATE TABLE IF NOT EXISTS ioct_run (
    id             INTEGER PRIMARY KEY,
    event_id       INTEGER NOT NULL UNIQUE REFERENCES fitness_event(id),
    assignment_id  INTEGER NOT NULL REFERENCES tag_assignment(id),
    start_read_id  INTEGER NOT NULL REFERENCES gate_read(id),
    end_read_id    INTEGER NOT NULL REFERENCES gate_read(id),
    elapsed_ms     INTEGER NOT NULL CHECK (elapsed_ms > 0),
    computed_at    TEXT NOT NULL
);

-- -----------------------------------------------------------------------------
-- ATTENDANCE (secondary stakeholder: Dean)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS class_section (
    id             INTEGER PRIMARY KEY,
    course         TEXT NOT NULL,         -- 'XE401'
    section        TEXT NOT NULL,         -- 'G1'
    period         TEXT NOT NULL,         -- 'B hour'
    term           TEXT NOT NULL,         -- '2027-1'
    instructor_id  INTEGER NOT NULL REFERENCES app_user(id),
    UNIQUE (course, section, term)
);

CREATE TABLE IF NOT EXISTS enrollment (
    section_id  INTEGER NOT NULL REFERENCES class_section(id),
    cadet_id    INTEGER NOT NULL REFERENCES cadet(id),
    PRIMARY KEY (section_id, cadet_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id           INTEGER PRIMARY KEY,
    section_id   INTEGER NOT NULL REFERENCES class_section(id),
    cadet_id     INTEGER NOT NULL REFERENCES cadet(id),
    class_date   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('PRESENT', 'LATE', 'ABSENT', 'EXCUSED')),
    recorded_by  INTEGER NOT NULL REFERENCES app_user(id),
    recorded_at  TEXT NOT NULL,
    client_ref   TEXT UNIQUE,
    UNIQUE (section_id, cadet_id, class_date)
);

-- -----------------------------------------------------------------------------
-- ARMS ROOM (tertiary stakeholder)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS weapon (
    id          INTEGER PRIMARY KEY,
    serial      TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,            -- 'M4', 'M17', ...
    company_id  INTEGER REFERENCES company(id)
);

CREATE TABLE IF NOT EXISTS si_event (
    id              INTEGER PRIMARY KEY,
    weapon_id       INTEGER NOT NULL REFERENCES weapon(id),
    cadet_id        INTEGER NOT NULL REFERENCES cadet(id),
    action          TEXT NOT NULL CHECK (action IN ('OUT', 'IN')),
    nco_id          INTEGER NOT NULL REFERENCES app_user(id),
    occurred_at     TEXT NOT NULL,
    attestation_id  INTEGER NOT NULL UNIQUE REFERENCES attestation(id),
    client_ref      TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS weapon_status (
    weapon_id        INTEGER PRIMARY KEY REFERENCES weapon(id),
    state            TEXT NOT NULL CHECK (state IN ('IN', 'OUT')),
    holder_cadet_id  INTEGER REFERENCES cadet(id),
    last_event_id    INTEGER REFERENCES si_event(id),
    CHECK ((state = 'OUT') = (holder_cadet_id IS NOT NULL))
);
