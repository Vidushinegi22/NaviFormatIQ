"""add compliance tables (guidelines, requirements, findings, reports)

The pharma compliance feature audits a document against a pre-loaded guideline
(e.g. ICH-E3). It needs four new tables. On a fresh DB the app's lifespan
``create_all()`` already creates them, so this migration is idempotent — it
inspects existing tables/indexes and only creates what is missing.

Revision ID: c3d9a7b2e1f4
Revises: b1f7c0a9d2e3
Create Date: 2026-05-31 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d9a7b2e1f4"
down_revision: Union[str, None] = "b1f7c0a9d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    tables = _tables()

    if "guidelines" not in tables:
        op.create_table(
            "guidelines",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("domain", sa.String(length=64), nullable=False, server_default="pharma"),
            sa.Column("version", sa.String(length=64), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_artifact_id", sa.Uuid(), nullable=True),
            sa.Column("qdrant_collection", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ingesting"),
            sa.Column("meta", _JSON, nullable=True),
            sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["source_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_guidelines_code", "guidelines", ["code"], unique=True)
        op.create_index("ix_guidelines_domain", "guidelines", ["domain"])
        op.create_index("ix_guidelines_status", "guidelines", ["status"])

    if "guideline_requirements" not in tables:
        op.create_table(
            "guideline_requirements",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("guideline_id", sa.Uuid(), nullable=False),
            sa.Column("parent_id", sa.Uuid(), nullable=True),
            sa.Column("section_no", sa.String(length=32), nullable=True),
            sa.Column("sort_key", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("requirement_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("dimension", sa.String(length=16), nullable=False, server_default="content"),
            sa.Column("severity_default", sa.String(length=16), nullable=False, server_default="major"),
            sa.Column("requirement_kind", sa.String(length=16), nullable=False, server_default="content"),
            sa.Column("constraint_spec", _JSON, nullable=True),
            sa.Column("qdrant_point_ids", _JSON, nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.ForeignKeyConstraint(["guideline_id"], ["guidelines.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["parent_id"], ["guideline_requirements.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_guideline_requirements_guideline_id", "guideline_requirements", ["guideline_id"])
        op.create_index(
            "ix_guideline_requirements_guideline_sort",
            "guideline_requirements",
            ["guideline_id", "sort_key"],
        )

    if "compliance_findings" not in tables:
        op.create_table(
            "compliance_findings",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("requirement_id", sa.Uuid(), nullable=True),
            sa.Column("section_no", sa.String(length=32), nullable=True),
            sa.Column("section_title", sa.String(length=512), nullable=True),
            sa.Column("requirement_title", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("dimension", sa.String(length=16), nullable=False, server_default="content"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="non_compliant"),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="major"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("evidence", sa.Text(), nullable=True),
            sa.Column("doc_location", sa.String(length=256), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("citation", _JSON, nullable=True),
            sa.Column("suggested_fix", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["requirement_id"], ["guideline_requirements.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_compliance_findings_run_id", "compliance_findings", ["run_id"])
        op.create_index("ix_compliance_findings_dimension", "compliance_findings", ["dimension"])
        op.create_index("ix_compliance_findings_run_dim", "compliance_findings", ["run_id", "dimension"])
        op.create_index("ix_compliance_findings_run_sev", "compliance_findings", ["run_id", "severity"])

    if "compliance_reports" not in tables:
        op.create_table(
            "compliance_reports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("guideline_id", sa.Uuid(), nullable=True),
            sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status_label", sa.String(length=32), nullable=True),
            sa.Column("per_dimension", _JSON, nullable=True),
            sa.Column("per_section", _JSON, nullable=True),
            sa.Column("severity_counts", _JSON, nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("docx_artifact_id", sa.Uuid(), nullable=True),
            sa.Column("pdf_artifact_id", sa.Uuid(), nullable=True),
            sa.Column("json_artifact_id", sa.Uuid(), nullable=True),
            sa.Column("csv_artifact_id", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["guideline_id"], ["guidelines.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["docx_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["pdf_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["json_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["csv_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_compliance_reports_run_id"),
        )
        op.create_index("ix_compliance_reports_run_id", "compliance_reports", ["run_id"])


def downgrade() -> None:
    tables = _tables()
    for tbl in ("compliance_reports", "compliance_findings", "guideline_requirements", "guidelines"):
        if tbl in tables:
            op.drop_table(tbl)
