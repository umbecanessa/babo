"""NLS Domain Database — SQLite-backed knowledge graph for facts and blocks.

The domain DB (knowledge.db) is the agent's "hippocampus." It tracks:
- Every fact the agent has learned, indexed by hierarchical domain path.
- Every block in the Merkle chain, with hashes and metadata.
- Conflict detection via domain-scoped lookups.
- Fluidity tracking (ping-pong protection) via flip counters.
- Project-scoped fact isolation (v2 layered schema).
- Credential vault with encoded storage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nls.models import Block, BlockMetadata, BlockType, Fact

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Schema v2 — layered, project-scoped
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height       INTEGER PRIMARY KEY,
    block_hash   TEXT    NOT NULL,
    parent_hash  TEXT    NOT NULL,
    block_type   TEXT    NOT NULL CHECK(block_type IN ('delta', 'epoch')),
    delta_path   TEXT    NOT NULL,
    timestamp    TEXT    NOT NULL,
    aku_count    INTEGER NOT NULL DEFAULT 0,
    metadata     TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    last_active REAL,
    status      TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS facts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_path           TEXT    NOT NULL,
    scope                 TEXT    NOT NULL DEFAULT 'global',
    project_id            TEXT    NOT NULL DEFAULT '',
    current_value         TEXT    NOT NULL,
    canonical_question    TEXT    DEFAULT '',
    block_height          INTEGER DEFAULT 0,
    flip_count            INTEGER DEFAULT 0,
    is_fluid              INTEGER DEFAULT 0,
    meta_layer            TEXT    DEFAULT '',
    hormonal_fingerprint  TEXT    DEFAULT NULL,
    strength              REAL    DEFAULT 1.0,
    emotional_valence     REAL    DEFAULT 0.0,
    last_modified         TEXT    NOT NULL,
    created_at            TEXT    NOT NULL,
    UNIQUE(domain_path, project_id)
);

CREATE TABLE IF NOT EXISTS fact_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_path     TEXT    NOT NULL,
    project_id      TEXT    NOT NULL DEFAULT '',
    archived_value  TEXT    NOT NULL,
    block_height    INTEGER NOT NULL DEFAULT 0,
    archived_at     TEXT    NOT NULL,
    superseded_by   TEXT,
    meta_layer      TEXT
);

CREATE TABLE IF NOT EXISTS credentials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_path   TEXT NOT NULL,
    project_id    TEXT NOT NULL DEFAULT '',
    encoded_value TEXT NOT NULL,
    service_name  TEXT DEFAULT '',
    created_at    REAL,
    last_used     REAL,
    UNIQUE(domain_path, project_id)
);

CREATE INDEX IF NOT EXISTS idx_facts_scope    ON facts(scope);
CREATE INDEX IF NOT EXISTS idx_facts_project  ON facts(project_id, scope);
CREATE INDEX IF NOT EXISTS idx_facts_domain   ON facts(domain_path);
CREATE INDEX IF NOT EXISTS idx_facts_fluid    ON facts(is_fluid);
CREATE INDEX IF NOT EXISTS idx_facts_valence  ON facts(emotional_valence);
CREATE INDEX IF NOT EXISTS idx_blocks_type    ON blocks(block_type);
CREATE INDEX IF NOT EXISTS idx_history_domain ON fact_history(domain_path);
CREATE INDEX IF NOT EXISTS idx_creds_project  ON credentials(project_id);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class DomainDB:
    """SQLite-backed domain knowledge database (v2 layered schema).

    Manages the facts table (knowledge graph), blocks table (chain ledger),
    projects registry, and credential vault.  Thread-safe via
    ``check_same_thread=False`` for background mining.

    The v2 schema introduces:
    - Composite uniqueness ``UNIQUE(domain_path, project_id)``
    - ``scope`` column (``'global'``, ``'project'``, ``'domain'``)
    - ``projects`` registry table
    - ``credentials`` vault table (never touched by training)
    """

    def __init__(self, db_path: Path | str, agent_id: str = ""):
        self.db_path = Path(db_path)
        self._agent_id = agent_id
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazy connection initialization with WAL mode for concurrent reads."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

            if self._needs_v2_migration():
                self._migrate_v1_to_v2()
            else:
                self._conn.executescript(_SCHEMA)

            self._ensure_auxiliary_tables()
        return self._conn

    # -------------------------------------------------------------------
    # Schema migration v1 -> v2
    # -------------------------------------------------------------------

    def _needs_v2_migration(self) -> bool:
        """Detect whether this is a v1 database that needs migration."""
        try:
            tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "facts" not in tables:
                return False
            if "schema_version" in tables:
                row = self._conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                if row and row[0] >= _SCHEMA_VERSION:
                    return False
            cursor = self._conn.execute("PRAGMA table_info(facts)")
            columns = {row[1] for row in cursor.fetchall()}
            if "scope" not in columns:
                return True
            return False
        except Exception:
            return False

    def _migrate_v1_to_v2(self) -> None:
        """Migrate a v1 database to v2 layered schema in-place.

        SQLite doesn't support ``ALTER TABLE`` for changing constraints,
        so we rebuild the facts table with the new composite key.
        """
        from nls.bridge.aku import classify_fact_scope

        logger.info("DomainDB: migrating v1 -> v2 (layered schema)")
        c = self._conn

        existing_cols = {
            row[1] for row in c.execute("PRAGMA table_info(facts)").fetchall()
        }

        has_project_id = "project_id" in existing_cols
        has_emotional_valence = "emotional_valence" in existing_cols
        has_strength = "strength" in existing_cols

        c.execute("BEGIN TRANSACTION")
        try:
            project_col = "project_id" if has_project_id else "''"
            valence_col = "emotional_valence" if has_emotional_valence else "0.0"
            strength_col = "strength" if has_strength else "1.0"

            c.execute("""
                CREATE TABLE IF NOT EXISTS facts_v2 (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_path           TEXT    NOT NULL,
                    scope                 TEXT    NOT NULL DEFAULT 'global',
                    project_id            TEXT    NOT NULL DEFAULT '',
                    current_value         TEXT    NOT NULL,
                    canonical_question    TEXT    DEFAULT '',
                    block_height          INTEGER DEFAULT 0,
                    flip_count            INTEGER DEFAULT 0,
                    is_fluid              INTEGER DEFAULT 0,
                    meta_layer            TEXT    DEFAULT '',
                    hormonal_fingerprint  TEXT    DEFAULT NULL,
                    strength              REAL    DEFAULT 1.0,
                    emotional_valence     REAL    DEFAULT 0.0,
                    last_modified         TEXT    NOT NULL,
                    created_at            TEXT    NOT NULL,
                    UNIQUE(domain_path, project_id)
                )
            """)

            c.execute(f"""
                INSERT OR IGNORE INTO facts_v2
                    (id, domain_path, scope, project_id, current_value,
                     canonical_question, block_height, flip_count, is_fluid,
                     meta_layer, hormonal_fingerprint, strength,
                     emotional_valence, last_modified, created_at)
                SELECT id, domain_path, 'global', COALESCE({project_col}, ''),
                       current_value,
                       COALESCE(canonical_question, ''),
                       COALESCE(block_height, 0),
                       COALESCE(flip_count, 0),
                       COALESCE(is_fluid, 0),
                       COALESCE(meta_layer, ''),
                       hormonal_fingerprint,
                       COALESCE({strength_col}, 1.0),
                       COALESCE({valence_col}, 0.0),
                       last_modified, created_at
                FROM facts
            """)

            c.execute("DROP TABLE facts")
            c.execute("ALTER TABLE facts_v2 RENAME TO facts")

            rows = c.execute(
                "SELECT id, domain_path FROM facts"
            ).fetchall()
            for row in rows:
                scope = classify_fact_scope(row[1])
                c.execute(
                    "UPDATE facts SET scope = ? WHERE id = ?",
                    (scope, row[0]),
                )

            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_scope "
                "ON facts(scope)")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_project "
                "ON facts(project_id, scope)")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_domain "
                "ON facts(domain_path)")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_fluid "
                "ON facts(is_fluid)")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_valence "
                "ON facts(emotional_valence)")

            c.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at  REAL NOT NULL,
                    last_active REAL,
                    status      TEXT DEFAULT 'active'
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_path   TEXT NOT NULL,
                    project_id    TEXT NOT NULL DEFAULT '',
                    encoded_value TEXT NOT NULL,
                    service_name  TEXT DEFAULT '',
                    created_at    REAL,
                    last_used     REAL,
                    UNIQUE(domain_path, project_id)
                )
            """)
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_creds_project "
                "ON credentials(project_id)"
            )

            fh_cols = {
                row[1]
                for row in c.execute("PRAGMA table_info(fact_history)").fetchall()
            } if c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='fact_history'"
            ).fetchone() else set()
            if fh_cols and "project_id" not in fh_cols:
                c.execute(
                    "ALTER TABLE fact_history "
                    "ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
                )

            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL)"
            )
            c.execute("DELETE FROM schema_version")
            c.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )

            c.execute("COMMIT")
            logger.info("DomainDB: v1 -> v2 migration complete")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def _ensure_auxiliary_tables(self) -> None:
        """Create auxiliary tables that may be missing (reasoning, connections)."""
        c = self._conn
        tables = {
            row[0]
            for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "reasoning_schemas" not in tables:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS reasoning_schemas (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_path         TEXT    NOT NULL,
                    premises            TEXT    NOT NULL,
                    logic_steps         TEXT    NOT NULL,
                    conclusion          TEXT    NOT NULL,
                    confidence          REAL    DEFAULT 0.5,
                    coherence_score     REAL    DEFAULT 0.0,
                    source_turn         INTEGER DEFAULT 0,
                    thinking_words      INTEGER DEFAULT 0,
                    invalidated         INTEGER DEFAULT 0,
                    invalidation_reason TEXT    DEFAULT '',
                    block_height        INTEGER DEFAULT 0,
                    created_at          TEXT    NOT NULL,
                    last_used           TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_dependencies (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_id   INTEGER NOT NULL REFERENCES reasoning_schemas(id),
                    fact_domain TEXT    NOT NULL,
                    UNIQUE(schema_id, fact_domain)
                );
                CREATE INDEX IF NOT EXISTS idx_schemas_domain
                    ON reasoning_schemas(domain_path);
                CREATE INDEX IF NOT EXISTS idx_schemas_valid
                    ON reasoning_schemas(invalidated);
                CREATE INDEX IF NOT EXISTS idx_schema_deps_fact
                    ON schema_dependencies(fact_domain);
            """)

        if "fact_connections" not in tables:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS fact_connections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_fact_id  INTEGER REFERENCES facts(id),
                    target_fact_id  INTEGER REFERENCES facts(id),
                    relationship    TEXT NOT NULL,
                    strength        REAL DEFAULT 1.0,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_fact_id, target_fact_id, relationship)
                );
                CREATE INDEX IF NOT EXISTS idx_connections_source
                    ON fact_connections(source_fact_id);
                CREATE INDEX IF NOT EXISTS idx_connections_target
                    ON fact_connections(target_fact_id);
            """)

        if "schema_version" not in tables:
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL)"
            )
            c.execute("DELETE FROM schema_version")
            c.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            c.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------------------------
    # Block operations
    # -------------------------------------------------------------------

    def insert_block(self, block: Block) -> None:
        """Insert a new block into the chain ledger."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO blocks (height, block_hash, parent_hash, block_type,
                                delta_path, timestamp, aku_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                block.height,
                block.block_hash,
                block.parent_hash,
                block.block_type.value,
                block.delta_path,
                block.timestamp.isoformat(),
                block.aku_count,
                json.dumps(block.metadata.model_dump()),
            ),
        )
        self.conn.commit()

    def get_block(self, height: int) -> Block | None:
        """Retrieve a block by height."""
        row = self.conn.execute(
            "SELECT * FROM blocks WHERE height = ?", (height,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_block(row)

    def get_all_blocks(self) -> list[Block]:
        """Retrieve all blocks ordered by height."""
        rows = self.conn.execute(
            "SELECT * FROM blocks ORDER BY height"
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def get_latest_block(self) -> Block | None:
        """Retrieve the block at the highest chain height."""
        row = self.conn.execute(
            "SELECT * FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self._row_to_block(row)

    def delete_blocks_above(self, height: int) -> int:
        """Delete all blocks above a given height (for rollback). Returns count deleted."""
        cursor = self.conn.execute(
            "DELETE FROM blocks WHERE height > ?", (height,)
        )
        self.conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------
    # Project operations
    # -------------------------------------------------------------------

    def register_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
    ) -> None:
        """Register or update a project in the registry."""
        now = datetime.utcnow().timestamp()
        self.conn.execute(
            """
            INSERT INTO projects (id, name, description, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                last_active = excluded.last_active
            """,
            (project_id, name, description, now, now),
        )
        self.conn.commit()

    def get_project(self, project_id: str) -> dict | None:
        """Get a project by ID."""
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_projects(self) -> list[dict]:
        """Return all active projects ordered by last_active descending."""
        rows = self.conn.execute(
            "SELECT * FROM projects WHERE status = 'active' "
            "ORDER BY last_active DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def touch_project(self, project_id: str) -> None:
        """Update last_active timestamp for a project."""
        self.conn.execute(
            "UPDATE projects SET last_active = ? WHERE id = ?",
            (datetime.utcnow().timestamp(), project_id),
        )
        self.conn.commit()

    # -------------------------------------------------------------------
    # Fact operations (v2 — composite key)
    # -------------------------------------------------------------------

    def insert_fact(self, fact: Fact) -> Fact:
        """Insert a new fact into the domain ledger."""
        from nls.bridge.aku import classify_fact_scope

        now = datetime.utcnow().isoformat()
        valence = self._compute_valence(fact.hormonal_fingerprint)
        fact.emotional_valence = valence
        scope = classify_fact_scope(fact.domain_path)
        fact.scope = scope
        pid = getattr(fact, "project_id", "") or ""

        cursor = self.conn.execute(
            """
            INSERT INTO facts (domain_path, scope, project_id,
                               current_value, canonical_question,
                               block_height, flip_count, is_fluid,
                               meta_layer, hormonal_fingerprint,
                               strength, emotional_valence,
                               last_modified, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.domain_path,
                scope,
                pid,
                fact.current_value,
                fact.canonical_question,
                fact.block_height,
                fact.flip_count,
                int(fact.is_fluid),
                fact.meta_layer,
                fact.hormonal_fingerprint,
                fact.strength,
                valence,
                fact.last_modified.isoformat() if fact.last_modified else now,
                fact.created_at.isoformat() if fact.created_at else now,
            ),
        )
        self.conn.commit()
        fact.id = cursor.lastrowid
        return fact

    def get_fact(
        self, domain_path: str, project_id: str = "",
    ) -> Fact | None:
        """Retrieve a fact by composite key (domain_path, project_id)."""
        row = self.conn.execute(
            "SELECT * FROM facts "
            "WHERE domain_path = ? AND project_id = ?",
            (domain_path, project_id),
        ).fetchone()
        if row is None:
            if project_id:
                row = self.conn.execute(
                    "SELECT * FROM facts "
                    "WHERE domain_path = ? AND project_id = ''",
                    (domain_path,),
                ).fetchone()
            if row is None:
                return None
        return self._row_to_fact(row)

    def get_facts_in_context(self, project_id: str = "") -> list[Fact]:
        """Return global + domain + matching project facts.

        This is the primary read method for scoped consumers (training,
        DeltaNet, inference).  It returns all facts that are relevant
        to the given project context.
        """
        if project_id:
            rows = self.conn.execute(
                "SELECT * FROM facts "
                "WHERE scope IN ('global', 'domain') "
                "   OR (scope = 'project' AND project_id = ?) "
                "ORDER BY domain_path",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts ORDER BY domain_path"
            ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_global_facts(self) -> list[Fact]:
        """Return only scope='global' facts."""
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE scope = 'global' "
            "ORDER BY domain_path"
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_project_facts(self, project_id: str) -> list[Fact]:
        """Return only scope='project' facts for a given project_id."""
        rows = self.conn.execute(
            "SELECT * FROM facts "
            "WHERE scope = 'project' AND project_id = ? "
            "ORDER BY domain_path",
            (project_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_facts_by_prefix(
        self, prefix: str, project_id: str = "",
    ) -> list[Fact]:
        """Retrieve all facts under a domain prefix (e.g., 'User.Tech')."""
        if project_id:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE domain_path LIKE ? "
                "AND (project_id = ? OR project_id = '')",
                (f"{prefix}%", project_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE domain_path LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_fluid_domains(self) -> list[Fact]:
        """Retrieve all facts currently marked as fluid (unstable)."""
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE is_fluid = 1"
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_all_facts(self) -> list[Fact]:
        """Retrieve all facts in the domain ledger (unscoped)."""
        rows = self.conn.execute(
            "SELECT * FROM facts ORDER BY domain_path"
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_facts_for_project(self, project_id: str) -> list[Fact]:
        """Retrieve all facts tagged with a specific project_id."""
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE project_id = ? ORDER BY domain_path",
            (project_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def check_conflict(
        self,
        domain_path: str,
        new_value: str,
        project_id: str = "",
    ) -> Fact | None:
        """Check if a new value conflicts with an existing fact.

        Uses composite key lookup. Returns the existing Fact if a conflict
        is detected (different value), or None if new or unchanged.
        A fallback-returned global fact is not considered a conflict for
        a project-scoped write.
        """
        existing = self.get_fact(domain_path, project_id=project_id)
        if existing is None:
            return None
        if project_id and (existing.project_id or "") != project_id:
            return None
        if existing.current_value == new_value:
            return None
        return existing

    def update_fact(
        self,
        domain_path: str,
        new_value: str,
        block_height: int,
        canonical_question: str | None = None,
        meta_layer: str | None = None,
        hormonal_fingerprint: str | None = None,
        flip_threshold: int = 2,
        flip_window_days: int = 30,
        skip_flip: bool = False,
        project_id: str = "",
    ) -> Fact:
        """Update a fact's value with conflict resolution and fluidity tracking.

        Uses composite key ``(domain_path, project_id)`` for lookups.
        Automatically classifies ``scope`` from the domain prefix.
        """
        from nls.bridge.aku import classify_fact_scope

        existing = self.get_fact(domain_path, project_id=project_id)
        now = datetime.utcnow()

        # If get_fact fell back to a global row but we requested a specific
        # project, treat it as a new fact (don't overwrite the global one).
        if (
            existing is not None
            and project_id
            and (existing.project_id or "") != project_id
        ):
            existing = None

        if existing is None:
            scope = classify_fact_scope(domain_path)
            fact = Fact(
                domain_path=domain_path,
                current_value=new_value,
                canonical_question=canonical_question,
                block_height=block_height,
                flip_count=0,
                is_fluid=False,
                meta_layer=meta_layer,
                hormonal_fingerprint=hormonal_fingerprint,
                last_modified=now,
                created_at=now,
                scope=scope,
                project_id=project_id,
            )
            return self.insert_fact(fact)

        if existing.current_value == new_value:
            updates = ["strength = MIN(strength + 0.2, 3.0)"]
            params: list = []
            if canonical_question and not existing.canonical_question:
                updates.append("canonical_question = ?")
                params.append(canonical_question)
                existing.canonical_question = canonical_question
            if meta_layer and not existing.meta_layer:
                updates.append("meta_layer = ?")
                params.append(meta_layer)
                existing.meta_layer = meta_layer
            if hormonal_fingerprint and not existing.hormonal_fingerprint:
                updates.append("hormonal_fingerprint = ?")
                params.append(hormonal_fingerprint)
                existing.hormonal_fingerprint = hormonal_fingerprint
            params.extend([domain_path, existing.project_id])
            self.conn.execute(
                f"UPDATE facts SET {', '.join(updates)} "
                "WHERE domain_path = ? AND project_id = ?",
                params,
            )
            self.conn.commit()
            return existing

        if skip_flip:
            new_flip_count = existing.flip_count
            is_fluid = existing.is_fluid
        else:
            new_flip_count = existing.flip_count + 1
            window_start = now - timedelta(days=flip_window_days)
            last_mod = existing.last_modified
            if isinstance(last_mod, str):
                last_mod = datetime.fromisoformat(last_mod)
            if last_mod < window_start:
                new_flip_count = 1
            is_fluid = new_flip_count >= flip_threshold

        effective_question = canonical_question or existing.canonical_question
        effective_layer = meta_layer or existing.meta_layer
        effective_fingerprint = hormonal_fingerprint or existing.hormonal_fingerprint
        effective_valence = self._compute_valence(effective_fingerprint)

        self.conn.execute(
            """
            UPDATE facts
            SET current_value = ?, canonical_question = ?,
                block_height = ?, flip_count = ?,
                is_fluid = ?, meta_layer = ?,
                hormonal_fingerprint = ?, emotional_valence = ?,
                last_modified = ?
            WHERE domain_path = ? AND project_id = ?
            """,
            (
                new_value,
                effective_question,
                block_height,
                new_flip_count,
                int(is_fluid),
                effective_layer,
                effective_fingerprint,
                effective_valence,
                now.isoformat(),
                domain_path,
                existing.project_id,
            ),
        )
        self.conn.commit()

        return Fact(
            id=existing.id,
            domain_path=domain_path,
            current_value=new_value,
            canonical_question=effective_question,
            block_height=block_height,
            flip_count=new_flip_count,
            is_fluid=is_fluid,
            meta_layer=effective_layer,
            hormonal_fingerprint=effective_fingerprint,
            emotional_valence=effective_valence,
            last_modified=now,
            created_at=existing.created_at,
            scope=existing.scope,
            project_id=existing.project_id,
        )

    def upsert_fact(
        self,
        domain_path: str,
        value: str,
        project_id: str = "",
        block_height: int = 0,
        **kwargs: Any,
    ) -> Fact:
        """Convenience wrapper: insert-or-update by composite key."""
        return self.update_fact(
            domain_path=domain_path,
            new_value=value,
            block_height=block_height,
            project_id=project_id,
            **kwargs,
        )

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    def block_count(self) -> int:
        """Return the total number of blocks in the chain."""
        row = self.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()
        return row[0]

    def fact_count(self) -> int:
        """Return the total number of facts in the domain ledger."""
        row = self.conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return row[0]

    def fluid_count(self) -> int:
        """Return the number of currently fluid (unstable) facts."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE is_fluid = 1"
        ).fetchone()
        return row[0]

    # -------------------------------------------------------------------
    # Fact history (temporal archive)
    # -------------------------------------------------------------------

    def archive_fact(
        self,
        domain_path: str,
        archived_value: str,
        block_height: int,
        superseded_by: str | None = None,
        meta_layer: str | None = None,
        project_id: str = "",
    ) -> None:
        """Move an old fact value to the history archive.

        Called when a TEMPORAL verdict determines that a fact has been
        superseded by a newer version in time.
        """
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO fact_history
                (domain_path, project_id, archived_value, block_height,
                 archived_at, superseded_by, meta_layer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain_path,
                project_id,
                archived_value,
                block_height,
                now,
                superseded_by,
                meta_layer,
            ),
        )
        self.conn.commit()

    def get_fact_history(self, domain_path: str) -> list[dict]:
        """Retrieve the temporal history of a domain's past values.

        Returns a list of dicts with archived_value, block_height,
        archived_at, and superseded_by for the given domain path,
        ordered from oldest to newest.
        """
        rows = self.conn.execute(
            """
            SELECT archived_value, block_height, archived_at,
                   superseded_by, meta_layer
            FROM fact_history
            WHERE domain_path = ?
            ORDER BY archived_at ASC
            """,
            (domain_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Fact strength & decay (forgetting curve)
    # -------------------------------------------------------------------

    def reinforce_fact(
        self, domain_path: str, boost: float = 0.2, cap: float = 3.0,
        project_id: str = "",
    ) -> None:
        """Increase a fact's strength when it is re-encountered.

        Mirrors biological synaptic potentiation — repeated activation
        strengthens the connection.  Capped to prevent runaway values.
        Uses composite key ``(domain_path, project_id)``.
        """
        self.conn.execute(
            "UPDATE facts SET strength = MIN(strength + ?, ?) "
            "WHERE domain_path = ? AND project_id = ?",
            (boost, cap, domain_path, project_id),
        )
        self.conn.commit()

    def decay_all(self, factor: float = 0.95) -> int:
        """Apply exponential decay to all fact strengths and schema confidence.

        Called once per sleep cycle to simulate the forgetting curve.
        Facts that are never reinforced gradually fade.  Returns the
        number of facts affected.

        ``factor=0.95`` means each sleep cycle reduces unreinforced
        facts by 5%.  After ~14 cycles without reinforcement, strength
        drops below 0.5.  After ~45 cycles, below 0.1.

        Also decays reasoning schema confidence in parallel.
        """
        cursor = self.conn.execute(
            "UPDATE facts SET strength = strength * ? "
            "WHERE strength > 0.01",
            (factor,),
        )
        # Also decay schema confidence
        self.decay_schemas(factor)
        self.conn.commit()
        return cursor.rowcount

    def prune_weak(self, threshold: float = 0.1) -> int:
        """Archive and remove facts whose strength has decayed below threshold.

        Moves weak facts to fact_history before deletion, preserving
        the agent's episodic record.  Returns the number of facts pruned.
        """
        now = datetime.utcnow().isoformat()
        weak = self.conn.execute(
            "SELECT domain_path, project_id, current_value, "
            "block_height, meta_layer "
            "FROM facts WHERE strength < ?",
            (threshold,),
        ).fetchall()

        for row in weak:
            self.conn.execute(
                """
                INSERT INTO fact_history
                    (domain_path, project_id, archived_value, block_height,
                     archived_at, superseded_by, meta_layer)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["domain_path"],
                    row["project_id"],
                    row["current_value"],
                    row["block_height"],
                    now,
                    "strength_decay",
                    row["meta_layer"],
                ),
            )

        cursor = self.conn.execute(
            "DELETE FROM facts WHERE strength < ?",
            (threshold,),
        )
        self.conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------
    # Reasoning schema operations
    # -------------------------------------------------------------------

    def store_schema(self, schema) -> int:
        """Store a distilled reasoning schema and create dependency links.

        Parameters
        ----------
        schema : ReasoningSchema
            The distilled reasoning schema to store.

        Returns
        -------
        int
            The database ID of the stored schema.
        """
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """
            INSERT INTO reasoning_schemas
                (domain_path, premises, logic_steps, conclusion,
                 confidence, coherence_score, source_turn,
                 thinking_words, invalidated, invalidation_reason,
                 block_height, created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schema.domain,
                json.dumps(schema.premises),
                json.dumps(schema.logic_steps),
                schema.conclusion,
                schema.confidence,
                schema.coherence_score,
                schema.source_turn,
                schema.thinking_words,
                int(schema.invalidated),
                schema.invalidation_reason,
                0,  # block_height — updated during sleep
                now,
                now,
            ),
        )
        schema_id = cursor.lastrowid

        # Create dependency links from premises to fact domains
        # Each premise may reference a fact domain
        for premise in schema.premises:
            # Try to match premise to a known fact domain
            # Simple heuristic: look for domain-like patterns
            fact_domain = self._infer_fact_domain(premise)
            if fact_domain:
                try:
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO schema_dependencies
                            (schema_id, fact_domain)
                        VALUES (?, ?)
                        """,
                        (schema_id, fact_domain),
                    )
                except Exception:
                    pass  # Duplicate or invalid

        self.conn.commit()
        return schema_id

    def get_schemas_for_domain(
        self, domain: str, *, limit: int = 5,
    ) -> list[dict]:
        """Retrieve reasoning schemas relevant to a domain.

        Matches schemas whose domain_path starts with the given prefix
        or is an exact match.  Returns most recent first.
        """
        rows = self.conn.execute(
            """
            SELECT * FROM reasoning_schemas
            WHERE domain_path LIKE ? || '%'
            ORDER BY last_used DESC
            LIMIT ?
            """,
            (domain, limit),
        ).fetchall()
        return [self._row_to_schema_dict(r) for r in rows]

    def get_valid_schemas(
        self, domain: str, *, limit: int = 3,
    ) -> list[dict]:
        """Retrieve only non-invalidated schemas for a domain.

        Used by the frontal lobe for priming — only valid reasoning
        patterns should inform new responses.
        """
        rows = self.conn.execute(
            """
            SELECT * FROM reasoning_schemas
            WHERE domain_path LIKE ? || '%'
              AND invalidated = 0
            ORDER BY confidence DESC, last_used DESC
            LIMIT ?
            """,
            (domain, limit),
        ).fetchall()

        # Update last_used timestamps
        now = datetime.utcnow().isoformat()
        for row in rows:
            self.conn.execute(
                "UPDATE reasoning_schemas SET last_used = ? WHERE id = ?",
                (now, row["id"]),
            )
        if rows:
            self.conn.commit()

        return [self._row_to_schema_dict(r) for r in rows]

    def get_invalidated_schemas(
        self, domain: str, *, limit: int = 2,
    ) -> list[dict]:
        """Retrieve recently invalidated schemas for a domain.

        Used by the frontal lobe to inject re-evaluation prompts.
        """
        rows = self.conn.execute(
            """
            SELECT * FROM reasoning_schemas
            WHERE domain_path LIKE ? || '%'
              AND invalidated = 1
            ORDER BY last_used DESC
            LIMIT ?
            """,
            (domain, limit),
        ).fetchall()
        return [self._row_to_schema_dict(r) for r in rows]

    def invalidate_by_premise(self, fact_domain: str) -> int:
        """Cascade-invalidate all schemas that depend on a changed fact.

        Brain analog: when a premise changes, all conclusions built
        on it become suspect.  Schemas are flagged, not deleted —
        preserving the reasoning structure for re-evaluation.

        Returns the number of schemas invalidated.
        """
        # Find all schema IDs that depend on this fact domain
        schema_ids = self.conn.execute(
            "SELECT schema_id FROM schema_dependencies WHERE fact_domain = ?",
            (fact_domain,),
        ).fetchall()

        if not schema_ids:
            return 0

        ids = [row["schema_id"] for row in schema_ids]
        reason = f"Premise changed: {fact_domain}"

        placeholders = ",".join("?" * len(ids))
        cursor = self.conn.execute(
            f"""
            UPDATE reasoning_schemas
            SET invalidated = 1,
                invalidation_reason = ?
            WHERE id IN ({placeholders})
              AND invalidated = 0
            """,
            [reason] + ids,
        )
        self.conn.commit()
        return cursor.rowcount

    def decay_schemas(self, factor: float = 0.95) -> int:
        """Apply exponential decay to schema confidence.

        Called alongside ``decay_all()`` during sleep to simulate
        the forgetting curve for reasoning patterns.
        """
        cursor = self.conn.execute(
            "UPDATE reasoning_schemas SET confidence = confidence * ? "
            "WHERE confidence > 0.01 AND invalidated = 0",
            (factor,),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_all_valid_schemas(self, *, limit: int = 50) -> list[dict]:
        """Retrieve all non-invalidated schemas (for sleep training).

        Returns schemas ordered by confidence (highest first).
        """
        rows = self.conn.execute(
            """
            SELECT * FROM reasoning_schemas
            WHERE invalidated = 0
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_schema_dict(r) for r in rows]

    def get_random_valid_schema(self) -> dict | None:
        """Retrieve a random non-invalidated schema (for DMN replay)."""
        row = self.conn.execute(
            """
            SELECT * FROM reasoning_schemas
            WHERE invalidated = 0
            ORDER BY RANDOM()
            LIMIT 1
            """,
        ).fetchone()
        return self._row_to_schema_dict(row) if row else None

    def schema_count(self, *, valid_only: bool = False) -> int:
        """Count schemas in the database."""
        if valid_only:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM reasoning_schemas WHERE invalidated = 0",
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM reasoning_schemas",
            ).fetchone()
        return row["cnt"] if row else 0

    def update_schema_coherence(self, schema_id: int, score: float) -> None:
        """Update the coherence score on a stored schema."""
        self.conn.execute(
            "UPDATE reasoning_schemas SET coherence_score = ? WHERE id = ?",
            (score, schema_id),
        )
        self.conn.commit()

    def flag_schema_for_review(self, schema_id: int) -> bool:
        """Flag a schema for re-evaluation without full invalidation.

        Sets confidence to 50% of current value, signaling that the
        schema needs extra attention during next sleep consolidation.
        Used when PE is high for a domain where the schema was primed.
        """
        cursor = self.conn.execute(
            "UPDATE reasoning_schemas SET confidence = confidence * 0.5 "
            "WHERE id = ? AND invalidated = 0",
            (schema_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _infer_fact_domain(self, premise: str) -> str | None:
        """Try to match a premise string to a known fact domain.

        Simple heuristic: check if any stored fact's domain_path
        appears in the premise text (case-insensitive).
        """
        # Get a sample of existing fact domains for matching
        rows = self.conn.execute(
            "SELECT DISTINCT domain_path FROM facts LIMIT 200",
        ).fetchall()

        premise_lower = premise.lower()
        for row in rows:
            domain = row["domain_path"]
            # Check if the domain path (or its leaf) appears in the premise
            leaf = domain.split(".")[-1].lower().replace("_", " ")
            if len(leaf) > 3 and leaf in premise_lower:
                return domain

        return None

    @staticmethod
    def _row_to_schema_dict(row: sqlite3.Row) -> dict:
        """Convert a SQLite row to a schema dictionary."""
        return {
            "id": row["id"],
            "domain": row["domain_path"],
            "premises": json.loads(row["premises"]),
            "logic_steps": json.loads(row["logic_steps"]),
            "conclusion": row["conclusion"],
            "confidence": row["confidence"],
            "coherence_score": row["coherence_score"],
            "source_turn": row["source_turn"],
            "thinking_words": row["thinking_words"],
            "invalidated": bool(row["invalidated"]),
            "invalidation_reason": row["invalidation_reason"],
            "block_height": row["block_height"],
            "created_at": row["created_at"],
            "last_used": row["last_used"],
        }

    # -------------------------------------------------------------------
    # Fact connections (knowledge graph edges)
    # -------------------------------------------------------------------

    def connect_facts(
        self,
        source_domain: str,
        target_domain: str,
        relationship: str,
        strength: float = 1.0,
    ) -> bool:
        """Create a relational edge between two facts.

        ``relationship`` should be one of: "supports", "contradicts",
        "extends", "analogy".  Returns True if the edge was created,
        False if source/target not found or edge already exists.
        """
        src = self.get_fact(source_domain)
        tgt = self.get_fact(target_domain)
        if src is None or tgt is None:
            return False
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO fact_connections
                    (source_fact_id, target_fact_id, relationship, strength)
                VALUES (?, ?, ?, ?)
                """,
                (src.id, tgt.id, relationship, strength),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def get_connected_facts(
        self,
        domain_path: str,
        *,
        max_hops: int = 1,
        relationship: str | None = None,
    ) -> list[dict]:
        """Traverse the knowledge graph from a given fact.

        Returns connected facts up to ``max_hops`` edges away.
        Each result dict contains ``fact`` (Fact model), ``relationship``
        (edge type), ``hop`` (distance), and ``edge_strength``.
        """
        root = self.get_fact(domain_path)
        if root is None or root.id is None:
            return []

        visited: set[int] = {root.id}
        results: list[dict] = []
        frontier = [root.id]

        for hop in range(1, max_hops + 1):
            next_frontier: list[int] = []
            for fid in frontier:
                rel_clause = ""
                params: list = [fid, fid, fid]
                if relationship:
                    rel_clause = "AND fc.relationship = ?"
                    params.append(relationship)
                rows = self.conn.execute(
                    f"""
                    SELECT fc.relationship, fc.strength AS edge_strength,
                           CASE WHEN fc.source_fact_id = ? THEN fc.target_fact_id
                                ELSE fc.source_fact_id END AS linked_id
                    FROM fact_connections fc
                    WHERE (fc.source_fact_id = ? OR fc.target_fact_id = ?)
                    {rel_clause}
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    lid = row["linked_id"]
                    if lid in visited:
                        continue
                    visited.add(lid)
                    next_frontier.append(lid)
                    fact_row = self.conn.execute(
                        "SELECT * FROM facts WHERE id = ?", (lid,)
                    ).fetchone()
                    if fact_row:
                        results.append({
                            "fact": self._row_to_fact(fact_row),
                            "relationship": row["relationship"],
                            "hop": hop,
                            "edge_strength": row["edge_strength"],
                        })
            frontier = next_frontier
            if not frontier:
                break

        return results

    # -------------------------------------------------------------------
    # Mood-congruent recall
    # -------------------------------------------------------------------

    def recall(
        self,
        prefix: str,
        *,
        mood_valence: float = 0.0,
        project_id: str = "",
        limit: int = 20,
    ) -> list[Fact]:
        """Retrieve facts by prefix with mood-congruent salience bias.

        When ``project_id`` is set, only global/domain facts and the
        matching project's facts are considered.
        """
        sign = 1.0 if mood_valence >= 0 else -1.0
        if project_id:
            rows = self.conn.execute(
                """
                SELECT *,
                       strength * (1.0 + 0.2 * emotional_valence * ?) AS salience
                FROM facts
                WHERE domain_path LIKE ?
                  AND (scope IN ('global', 'domain')
                       OR (scope = 'project' AND project_id = ?))
                ORDER BY salience DESC
                LIMIT ?
                """,
                (sign, f"{prefix}%", project_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *,
                       strength * (1.0 + 0.2 * emotional_valence * ?) AS salience
                FROM facts
                WHERE domain_path LIKE ?
                ORDER BY salience DESC
                LIMIT ?
                """,
                (sign, f"{prefix}%", limit),
            ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    # -------------------------------------------------------------------
    # Credential vault (Phase 8)
    # -------------------------------------------------------------------

    def _credential_key(self) -> bytes:
        """Derive a per-agent HMAC key for credential encoding."""
        seed = (self._agent_id or "nls-default").encode()
        return hashlib.sha256(seed).digest()

    def _encode_credential(self, plaintext: str) -> str:
        raw = plaintext.encode("utf-8")
        key = self._credential_key()
        tag = hmac.new(key, raw, hashlib.sha256).digest()[:8]
        return base64.b64encode(tag + raw).decode("ascii")

    def _decode_credential(self, encoded: str) -> str:
        blob = base64.b64decode(encoded)
        return blob[8:].decode("utf-8")

    def store_credential(
        self,
        domain_path: str,
        plaintext_value: str,
        project_id: str = "",
        service_name: str = "",
    ) -> None:
        """Store a credential in the vault (encoded, never trained)."""
        encoded = self._encode_credential(plaintext_value)
        now = datetime.utcnow().timestamp()
        self.conn.execute(
            """
            INSERT INTO credentials
                (domain_path, project_id, encoded_value, service_name,
                 created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain_path, project_id) DO UPDATE SET
                encoded_value = excluded.encoded_value,
                last_used = excluded.last_used
            """,
            (domain_path, project_id, encoded, service_name, now, now),
        )
        self.conn.commit()

    def get_credential(
        self, domain_path: str, project_id: str = "",
    ) -> str | None:
        """Retrieve and decode a credential. Returns plaintext or None."""
        matched_pid = project_id
        row = self.conn.execute(
            "SELECT encoded_value FROM credentials "
            "WHERE domain_path = ? AND project_id = ?",
            (domain_path, project_id),
        ).fetchone()
        if row is None and project_id:
            row = self.conn.execute(
                "SELECT encoded_value FROM credentials "
                "WHERE domain_path = ? AND project_id = ''",
                (domain_path,),
            ).fetchone()
            matched_pid = ""
        if row is None:
            return None
        now = datetime.utcnow().timestamp()
        self.conn.execute(
            "UPDATE credentials SET last_used = ? "
            "WHERE domain_path = ? AND project_id = ?",
            (now, domain_path, matched_pid),
        )
        self.conn.commit()
        try:
            return self._decode_credential(row["encoded_value"])
        except Exception:
            return None

    def get_credentials(self, project_id: str = "") -> list[dict]:
        """Return all credentials for a project context (decoded).

        Returns global credentials plus project-specific ones.
        """
        if project_id:
            rows = self.conn.execute(
                "SELECT domain_path, project_id, encoded_value, service_name "
                "FROM credentials "
                "WHERE project_id = '' OR project_id = ?",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT domain_path, project_id, encoded_value, service_name "
                "FROM credentials"
            ).fetchall()
        result = []
        for r in rows:
            try:
                val = self._decode_credential(r["encoded_value"])
            except Exception:
                continue
            result.append({
                "domain_path": r["domain_path"],
                "project_id": r["project_id"],
                "value": val,
                "service_name": r["service_name"],
            })
        return result

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _compute_valence(hormonal_fingerprint: str | None) -> float:
        """Derive emotional valence from a hormonal fingerprint JSON blob.

        Formula: ``(serotonin - cortisol) * 2.0``, clamped to [-1, 1].
        Returns 0.0 if fingerprint is missing or unparseable.
        """
        if not hormonal_fingerprint:
            return 0.0
        try:
            hf = json.loads(hormonal_fingerprint)
            serotonin = float(hf.get("serotonin", 0.0))
            cortisol = float(hf.get("cortisol", 0.0))
            return max(-1.0, min(1.0, (serotonin - cortisol) * 2.0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _row_to_block(row: sqlite3.Row) -> Block:
        """Convert a SQLite row to a Block model."""
        metadata_raw = json.loads(row["metadata"]) if row["metadata"] else {}
        return Block(
            height=row["height"],
            block_hash=row["block_hash"],
            parent_hash=row["parent_hash"],
            block_type=BlockType(row["block_type"]),
            delta_path=row["delta_path"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            aku_count=row["aku_count"],
            metadata=BlockMetadata(**metadata_raw),
        )

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> Fact:
        """Convert a SQLite row to a Fact model."""
        try:
            strength = float(row["strength"])
        except (KeyError, IndexError, TypeError):
            strength = 1.0
        try:
            valence = float(row["emotional_valence"])
        except (KeyError, IndexError, TypeError):
            valence = 0.0

        try:
            _project_id = row["project_id"] or ""
        except (KeyError, IndexError):
            _project_id = ""

        try:
            _scope = row["scope"] or "global"
        except (KeyError, IndexError):
            _scope = "global"

        return Fact(
            id=row["id"],
            domain_path=row["domain_path"],
            current_value=row["current_value"],
            canonical_question=row["canonical_question"],
            block_height=row["block_height"],
            flip_count=row["flip_count"],
            is_fluid=bool(row["is_fluid"]),
            meta_layer=row["meta_layer"],
            hormonal_fingerprint=row["hormonal_fingerprint"],
            strength=strength,
            emotional_valence=valence,
            last_modified=datetime.fromisoformat(row["last_modified"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            scope=_scope,
            project_id=_project_id,
        )
