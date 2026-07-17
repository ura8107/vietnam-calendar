from vietnam_calendar.models import Base
from sqlalchemy import Enum

def test_phase1_schema_has_required_constraints_and_indexes():
    feeds=Base.metadata.tables["feeds"]; assert {"normalized_url","next_fetch_at","last_success_at","last_failure_at"} <= set(feeds.c.keys()); assert "ck_feeds_interval" in {c.name for c in feeds.constraints}
    articles=Base.metadata.tables["articles"]; assert {"normalized_url","title_normalized","summary_text","date_source","processing_status"} <= set(articles.c.keys())
    events=Base.metadata.tables["events"]; assert {"importance_score","importance_reason","certainty","current_revision_id","rule_version","prompt_version"} <= set(events.c.keys())
    jobs=Base.metadata.tables["jobs"]; assert {"dedupe_key","locked_by","lease_expires_at","last_error_code"} <= set(jobs.c.keys()); assert "uq_jobs_active_dedupe" in {i.name for i in jobs.indexes}
    links=Base.metadata.tables["event_articles"]; assert "uq_event_primary_source" in {i.name for i in links.indexes}
    assert {"ck_events_version","ck_events_must_reason","ck_events_outscope_importance"} <= {c.name for c in events.constraints}
    revisions=Base.metadata.tables["event_revisions"]; assert "ck_event_revisions_version" in {c.name for c in revisions.constraints}
    ai=Base.metadata.tables["ai_runs"]; assert {"ck_ai_input_tokens","ck_ai_output_tokens"} <= {c.name for c in ai.constraints}

def test_all_design_tables_exist():
    assert {"feeds","fetch_runs","articles","events","event_articles","reviews","event_revisions","ai_runs","jobs","users","sessions","audit_logs"} == set(Base.metadata.tables)

def test_initial_migration_downgrade_cleans_every_named_enum():
    from pathlib import Path
    migration=next((Path(__file__).parents[2]/"migrations"/"versions").glob("*_initial_schema_phase1.py")).read_text()
    enum_names={column.type.name for table in Base.metadata.tables.values() for column in table.columns if isinstance(column.type,Enum)}
    assert enum_names
    for enum_name in enum_names:
        assert f"'{enum_name}'" in migration.split("def downgrade()",1)[1]
