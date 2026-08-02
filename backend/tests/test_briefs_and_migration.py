from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

from backend.app.briefs import (
    _lossless_fragments,
    _make_stream_progress_callback,
    _summarize_brief_batches,
    build_brief_batch_prompts,
    build_brief_prompt,
    build_brief_reduce_prompt,
    brief_markdown,
    create_manual_brief,
    manual_window,
    read_brief_rule,
    reset_brief_rule,
    run_due_schedules,
    save_brief_rule,
    schedule_window,
)
from backend.app.config import get_settings
from backend.app.llm import LLMConnectionError
from backend.app.models import (
    Base,
    Brief,
    BriefItem,
    BriefSchedule,
    Domain,
    Entry,
    EntryFeed,
    Feed,
    FeedDomain,
    Work,
)


def add_seen_entry(db, first_seen_at: datetime) -> Entry:
    feed = Feed(title="Research feed", url="https://brief.test/feed")
    work = Work(
        dedup_key="url:https://brief.test/article",
        canonical_url="https://brief.test/article",
    )
    db.add_all([feed, work])
    db.flush()
    entry = Entry(
        work_id=work.id,
        version_key="default",
        title="Brief article",
        summary="A deterministic summary source.",
        url="https://brief.test/article",
        authors=["Alice"],
        source_hash="d" * 64,
    )
    db.add(entry)
    db.flush()
    db.add(
        EntryFeed(
            entry_id=entry.id,
            feed_id=feed.id,
            first_seen_at=first_seen_at,
        )
    )
    db.commit()
    return entry


def test_manual_brief_is_idempotent_and_exports_only_llm_summary(
    db_factory, settings, monkeypatch
):
    configured = settings.model_copy(
        update={"llm_summary_timeout_seconds": 180}
    )

    def summarize(*args, **kwargs):
        assert kwargs["timeout_seconds"] == 30
        return (
            "## 今日概览\n\n研究主题出现集中趋势。\n\n"
            "## 优先阅读建议\n\n- 优先关注方法比较。"
        )

    monkeypatch.setattr(
        "backend.app.briefs.complete_feature_chat",
        summarize,
    )
    progress = []
    with db_factory() as db:
        entry = add_seen_entry(db, datetime(2026, 7, 25, 10))
        at = datetime(2026, 7, 26, 10, tzinfo=ZoneInfo("UTC"))
        first = create_manual_brief(
            db,
            "daily",
            at=at,
            start_at=datetime(2026, 7, 25, 9, tzinfo=ZoneInfo("UTC")),
            end_at=datetime(2026, 7, 26, 10, tzinfo=ZoneInfo("UTC")),
            idempotency_key="manual-brief-test",
            settings=configured,
            progress_callback=lambda stage, completed, total, _message: progress.append(
                (stage, completed, total)
            ),
        )
        second = create_manual_brief(
            db,
            "daily",
            at=at,
            start_at=datetime(2026, 7, 25, 9, tzinfo=ZoneInfo("UTC")),
            end_at=datetime(2026, 7, 26, 10, tzinfo=ZoneInfo("UTC")),
            idempotency_key="manual-brief-test",
            settings=settings,
        )
        assert first.id == second.id
        assert db.scalar(select(BriefItem).where(BriefItem.entry_id == entry.id))
        exported = brief_markdown(db, first)
        assert "研究主题出现集中趋势" in exported
        assert "Brief article" not in exported
        assert "## Entries" not in exported
        assert first.notes.startswith("## 今日概览")
    assert progress == [
        ("preparing", 1, 1),
        ("finalizing", 0, 1),
        ("finalizing", 1, 1),
    ]


def test_brief_batches_retry_transient_failures_and_resume_checkpoints(
    db_factory, settings, monkeypatch
):
    configured = settings.model_copy(
        update={
            "brief_batch_concurrency": 1,
            "brief_llm_max_attempts": 2,
            "brief_llm_retry_base_seconds": 0,
        }
    )
    calls: dict[str, int] = {}

    def transient_completion(*args, **kwargs):
        prompt = kwargs["user_prompt"]
        calls[prompt] = calls.get(prompt, 0) + 1
        if prompt == "batch-3" and calls[prompt] == 1:
            raise LLMConnectionError("temporary 503", retryable=True)
        return f"observation:{prompt}"

    monkeypatch.setattr(
        "backend.app.briefs.complete_feature_chat",
        transient_completion,
    )
    checkpoints: dict[tuple[str, str], str] = {}
    prompts = [
        ("system", "batch-1", 1),
        ("system", "batch-2", 1),
        ("system", "batch-3", 1),
    ]
    with db_factory() as db:
        observations = _summarize_brief_batches(
            db,
            prompts=prompts,
            settings=configured,
            timeout_seconds=10,
            checkpoint_loader=lambda stage, key: checkpoints.get((stage, key)),
            checkpoint_saver=lambda stage, key, value: checkpoints.__setitem__(
                (stage, key), value
            ),
        )
    assert observations == [
        "observation:batch-1",
        "observation:batch-2",
        "observation:batch-3",
    ]
    assert calls == {"batch-1": 1, "batch-2": 1, "batch-3": 2}

    calls.clear()
    with db_factory() as db:
        resumed = _summarize_brief_batches(
            db,
            prompts=prompts,
            settings=configured,
            timeout_seconds=10,
            checkpoint_loader=lambda stage, key: checkpoints.get((stage, key)),
            checkpoint_saver=lambda stage, key, value: checkpoints.__setitem__(
                (stage, key), value
            ),
        )
    assert resumed == observations
    assert calls == {}


def test_stream_progress_is_throttled_to_fifteen_seconds(monkeypatch):
    times = iter([0.0, 14.0, 15.0, 29.0, 30.0])
    monkeypatch.setattr(
        "backend.app.briefs.monotonic",
        lambda: next(times),
    )
    updates: list[tuple[str, int, int, str | None]] = []
    callback = _make_stream_progress_callback(
        lambda stage, completed, total, message: updates.append(
            (stage, completed, total, message)
        )
    )
    assert callback is not None
    callback("summarizing_batches", 1, 8, "批次 2/8", 100)
    callback("summarizing_batches", 1, 8, "批次 2/8", 200)
    callback("summarizing_batches", 1, 8, "批次 2/8", 300)
    callback("summarizing_batches", 1, 8, "批次 2/8", 400)

    assert updates == [
        (
            "summarizing_batches",
            1,
            8,
            "批次 2/8：正在持续生成，已接收 200 字符",
        ),
        (
            "summarizing_batches",
            1,
            8,
            "批次 2/8：正在持续生成，已接收 400 字符",
        ),
    ]


def test_domain_filtered_schedule_and_catchup_are_idempotent(
    db_factory, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.briefs.complete_feature_chat",
        lambda *args, **kwargs: "## 概览\n\n本周期综合总结。",
    )
    with db_factory() as db:
        entry = add_seen_entry(db, datetime(2026, 7, 25, 10))
        domain = Domain(name="Science")
        db.add(domain)
        db.flush()
        link = db.scalar(select(EntryFeed).where(EntryFeed.entry_id == entry.id))
        db.add(FeedDomain(feed_id=link.feed_id, domain_id=domain.id))
        schedule = BriefSchedule(
            name="Science brief",
            period="daily",
            timezone="UTC",
            cutoff_time="09:00",
            domain_ids=[domain.id],
            domain_match="all",
        )
        db.add(schedule)
        db.commit()
        at = datetime(2026, 7, 26, 10, tzinfo=ZoneInfo("UTC"))
        rows = run_due_schedules(db, at=at)
        assert len(rows) == 1
        assert rows[0].stats["entries"] == 1
        repeated = run_due_schedules(db, at=at)
        assert repeated == []
        later = run_due_schedules(
            db, at=datetime(2026, 7, 28, 10, tzinfo=ZoneInfo("UTC"))
        )
        assert len(later) == 2


def test_brief_prompt_requires_synthesis_instead_of_article_list(db_factory):
    with db_factory() as db:
        entry = add_seen_entry(db, datetime(2026, 7, 25, 10))
        system_prompt, user_prompt, included = build_brief_prompt(
            db,
            period="weekly",
            start_at=datetime(2026, 7, 19, 9),
            end_at=datetime(2026, 7, 26, 9),
            entries=[entry],
            rule="# 周报规则\n\n## 主题演化\n\n不要生成文章列表。",
        )
        assert included == 1
        assert "只使用输入条目中出现的信息" in system_prompt
        assert "不要生成文章列表" in user_prompt
        assert "## 主题演化" in user_prompt
        assert "Brief article" in user_prompt


def test_lossless_fragments_preserve_every_character():
    value = ("第一段。\n\n" + "中间内容 " * 40 + "\n最后一段不可丢失。")
    fragments = _lossless_fragments(value, 37)

    assert len(fragments) > 1
    assert all(len(fragment) <= 37 for fragment in fragments)
    assert "".join(fragments) == value


def test_direct_brief_prompt_keeps_the_complete_summary(db_factory, monkeypatch):
    complete_summary = "完整摘要内容。" * 350 + "原始摘要尾部不可丢失"
    monkeypatch.setattr(
        "backend.app.briefs._entry_source",
        lambda _db, _entry: ("研究标题", complete_summary, ["研究来源"]),
    )
    with db_factory() as db:
        entry = add_seen_entry(db, datetime(2026, 7, 25, 10))
        _system, user_prompt, included = build_brief_prompt(
            db,
            period="daily",
            start_at=datetime(2026, 7, 25, 9),
            end_at=datetime(2026, 7, 26, 9),
            entries=[entry],
            rule="# 简报规则\n\n只做综合分析。",
        )

    assert included == 1
    assert complete_summary in user_prompt
    assert "原始摘要尾部不可丢失" in user_prompt


def test_large_brief_input_is_batched_without_dropping_entries(
    db_factory, monkeypatch
):
    monkeypatch.setattr("backend.app.briefs.MAX_BRIEF_INPUT_CHARS", 2_500)
    complete_summary = "方法与结果。" * 500 + "批次摘要尾部不可丢失"
    monkeypatch.setattr(
        "backend.app.briefs._entry_source",
        lambda _db, _entry: ("研究标题", complete_summary, ["研究来源"]),
    )
    with db_factory() as db:
        entry = add_seen_entry(db, datetime(2026, 7, 25, 10))
        prompts = build_brief_batch_prompts(
            db,
            period="daily",
            start_at=datetime(2026, 7, 25, 9),
            end_at=datetime(2026, 7, 26, 9),
            entries=[entry] * 6,
            rule="# 简报规则\n\n只做综合分析。",
        )

    assert len(prompts) > 1
    combined_prompts = "\n".join(user for _system, user, _count in prompts)
    assert combined_prompts.count("批次摘要尾部不可丢失") == 6
    assert sum(count for _system, _user, count in prompts) == combined_prompts.count(
        "<entry_fragment"
    )
    assert all(
        len(user) <= 2_500 for _system, user, _count in prompts
    )
    assert all("不要生成文章清单" in user for _system, user, _count in prompts)

    complete_observation = "完整批次观察。" * 500 + "观察尾部不可丢失"
    system_prompt, user_prompt = build_brief_reduce_prompt(
        period="daily",
        start_at=datetime(2026, 7, 25, 9),
        end_at=datetime(2026, 7, 26, 9),
        rule="# 简报规则\n\n只做综合分析。",
        observations=[complete_observation, "第二批观察"],
    )
    assert "最终简报正文" in system_prompt
    assert complete_observation in user_prompt
    assert "观察尾部不可丢失" in user_prompt
    assert "不要提及批次" in user_prompt


def test_manual_window_uses_current_natural_period():
    start, end = manual_window(
        "daily",
        at=datetime(2026, 7, 28, 15, 30, tzinfo=ZoneInfo("UTC")),
        timezone="Asia/Shanghai",
    )
    assert start == datetime(2026, 7, 27, 16)
    assert end == datetime(2026, 7, 28, 15, 30)

    week_start, week_end = manual_window(
        "weekly",
        at=datetime(2026, 7, 28, 15, 30, tzinfo=ZoneInfo("UTC")),
        timezone="Asia/Shanghai",
    )
    assert week_start == datetime(2026, 7, 26, 16)
    assert week_end == end


def test_brief_rule_can_be_customized_and_reset(settings):
    content, is_custom = read_brief_rule(settings)
    assert "# 简报生成规则" in content
    assert is_custom is False

    saved = save_brief_rule("# 自定义规则\n\n只总结方法趋势。", settings)
    assert saved.startswith("# 自定义规则")
    assert read_brief_rule(settings) == (saved, True)

    restored = reset_brief_rule(settings)
    assert "# 简报生成规则" in restored
    assert read_brief_rule(settings) == (restored, False)


def test_schedule_window_handles_dst():
    schedule = BriefSchedule(
        name="DST",
        period="daily",
        timezone="Europe/Berlin",
        cutoff_time="09:00",
    )
    start, end = schedule_window(
        schedule, datetime(2026, 3, 30, 12, tzinfo=ZoneInfo("UTC"))
    )
    assert start < end
    assert (end - start).total_seconds() in {23 * 3600, 24 * 3600, 25 * 3600}


def test_initial_alembic_migration_matches_models(tmp_path: Path, monkeypatch):
    database = tmp_path / "migration.db"
    monkeypatch.setenv("AFFOGATO_RSS_READER_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("AFFOGATO_RSS_READER_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    assert set(Base.metadata.tables) <= set(inspector.get_table_names())
    for table_name, model_table in Base.metadata.tables.items():
        migrated = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(migrated) == {column.name for column in model_table.columns}
        for column in model_table.columns:
            assert migrated[column.name]["nullable"] is column.nullable
    engine.dispose()

    with closing(sqlite3.connect(database)) as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {"entries_ai", "entries_ad", "entries_au"} <= triggers
        connection.execute(
            "INSERT INTO works(id,dedup_key,created_at) VALUES(1,'url:https://migration.test','2026-07-26')"
        )
        connection.execute(
            """INSERT INTO entries(
                id,work_id,version_key,title,summary,url,authors,categories,source_hash,created_at,updated_at
            ) VALUES(
                1,1,'default','Migration article','Indexed abstract','https://migration.test',
                '["Alice"]','["science"]','hash','2026-07-26','2026-07-26'
            )"""
        )
        assert connection.execute(
            "SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'Migration'"
        ).fetchone()[0] == 1

    command.downgrade(config, "base")
    with closing(sqlite3.connect(database)) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type IN ('table','view','trigger')
                     AND name NOT LIKE 'sqlite_%'
                     AND name != 'alembic_version'"""
            )
        }
    assert remaining == set()
    get_settings.cache_clear()


def test_proxy_migrations_preserve_links_and_repair_arxiv_orphans(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "upgrade-with-links.db"
    monkeypatch.setenv(
        "AFFOGATO_RSS_READER_DATABASE_URL", f"sqlite:///{database.as_posix()}"
    )
    monkeypatch.setenv("AFFOGATO_RSS_READER_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(config, "0005")

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            INSERT INTO feeds(
                id,title,url,position,enabled,poll_interval_minutes,etag,
                error_count,created_at,updated_at
            ) VALUES(
                1,'arXiv: quant-ph','https://export.arxiv.org/rss/quant-ph',
                0,1,45,'old-etag',0,'2026-07-28','2026-07-28'
            );
            INSERT INTO works(
                id,dedup_key,arxiv_base_id,canonical_url,created_at
            ) VALUES
                (1,'arxiv:2607.00001','2607.00001',
                 'https://arxiv.org/abs/2607.00001','2026-07-28'),
                (2,'arxiv:2607.00002','2607.00002',
                 'https://arxiv.org/abs/2607.00002','2026-07-28');
            INSERT INTO entries(
                id,work_id,version_key,guid,title,summary,url,authors,categories,
                arxiv_id,arxiv_version,source_hash,created_at,updated_at
            ) VALUES
                (1,1,'v1','2607.00001v1','Linked article','Summary',
                 'https://arxiv.org/abs/2607.00001','[]','["quant-ph"]',
                 '2607.00001v1',1,'hash-1','2026-07-28','2026-07-28'),
                (2,2,'v1','2607.00002v1','Orphan article','Summary',
                 'https://arxiv.org/abs/2607.00002','[]','["quant-ph"]',
                 '2607.00002v1',1,'hash-2','2026-07-28','2026-07-28');
            INSERT INTO entry_feeds(
                id,entry_id,feed_id,source_guid,first_seen_at
            ) VALUES(1,1,1,'2607.00001v1','2026-07-28');
            INSERT INTO llm_connections(
                id,name,base_url,model,created_at,updated_at
            ) VALUES(
                1,'Shared LLM','https://llm.example/v1','model',
                '2026-07-28','2026-07-28'
            );
            INSERT INTO llm_feature_bindings(
                feature_key,connection_id,created_at,updated_at
            ) VALUES('translation',1,'2026-07-28','2026-07-28');
            """
        )
        connection.commit()

    command.upgrade(config, "head")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM entry_feeds WHERE feed_id = 1"
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT count(*)
            FROM entry_feeds
            WHERE entry_id = 1
              AND source_guid = '2607.00001v1'
              AND first_seen_at = '2026-07-28'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT count(*)
            FROM llm_feature_bindings
            WHERE feature_key = 'translation' AND connection_id = 1
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT proxy_mode FROM feeds WHERE id = 1"
        ).fetchone()[0] == "direct"
        assert connection.execute(
            "SELECT proxy_mode FROM llm_connections WHERE id = 1"
        ).fetchone()[0] == "direct"
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "0012"
    get_settings.cache_clear()
