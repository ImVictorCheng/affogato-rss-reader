from __future__ import annotations

from collections.abc import Generator

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, delete, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import BACKEND_DIR, Settings, get_settings
from .llm import migrate_legacy_secrets
from .models import AppSetting, Owner, Session as LoginSession, utcnow


def make_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = settings.effective_database_url
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute(
                f"PRAGMA wal_autocheckpoint={settings.sqlite_wal_autocheckpoint_pages}"
            )
            cursor.execute(
                f"PRAGMA journal_size_limit={settings.sqlite_journal_size_limit_bytes}"
            )
            cursor.close()
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def create_fts(bind: Engine | Connection) -> None:
    if bind.dialect.name != "sqlite":
        return
    ddl = [
        """CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            title, summary, authors, categories, arxiv_id, doi,
            content='entries', content_rowid='id'
        )""",
        """CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid,title,summary,authors,categories,arxiv_id,doi)
            VALUES(new.id,new.title,new.summary,new.authors,new.categories,new.arxiv_id,new.doi);
        END""",
        """CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts,rowid,title,summary,authors,categories,arxiv_id,doi)
            VALUES('delete',old.id,old.title,old.summary,old.authors,old.categories,old.arxiv_id,old.doi);
        END""",
        """CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts,rowid,title,summary,authors,categories,arxiv_id,doi)
            VALUES('delete',old.id,old.title,old.summary,old.authors,old.categories,old.arxiv_id,old.doi);
            INSERT INTO entries_fts(rowid,title,summary,authors,categories,arxiv_id,doi)
            VALUES(new.id,new.title,new.summary,new.authors,new.categories,new.arxiv_id,new.doi);
        END""",
    ]
    def apply(conn: Connection) -> None:
        for statement in ddl:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')"))
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            apply(conn)
    else:
        apply(bind)


def migrate_database(engine_: Engine | None = None) -> None:
    """Upgrade the database schema to the bundled Alembic head revision."""
    engine_ = engine_ or engine
    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine_.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")


def init_database(engine_: Engine | None = None, settings: Settings | None = None) -> None:
    engine_ = engine_ or engine
    settings = settings or get_settings()
    if engine_.dialect.name == "sqlite":
        # Journal mode is persistent database state. Setting it from every
        # connection races when the UI opens several requests at once,
        # especially on Docker Desktop bind mounts.
        with engine_.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    migrate_database(engine_)
    if engine_.dialect.name == "sqlite":
        with engine_.connect() as connection:
            # PASSIVE never waits for readers. The configured journal size
            # limit is applied when SQLite can safely reset the WAL.
            connection.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)")
    factory = sessionmaker(bind=engine_, expire_on_commit=False)
    with factory() as db:
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= utcnow()))
        if db.get(AppSetting, "translation_enabled") is None:
            db.add(AppSetting(key="translation_enabled", value="true" if settings.translation_enabled else "false"))
        if db.get(AppSetting, "translation_target") is None:
            db.add(AppSetting(key="translation_target", value=settings.translation_target))
        if settings.auth_mode == "none" and db.get(Owner, 1) is None:
            db.add(Owner(id=1, username="owner", password_hash=None))
        migrate_legacy_secrets(db, settings)
        db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
