"""Add company ownership for tender asset libraries."""

import sqlalchemy as sa
from alembic import op

revision = "0019_tender_companies"
down_revision = "0018_document_expiry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tender_companies",
        sa.Column("public_id", sa.String(32), primary_key=True),
        sa.Column(
            "base_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_bases.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("name", sa.String(110), nullable=False),
        sa.Column("credit_code", sa.String(18), nullable=False, unique=True),
        sa.Column("aliases", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("tender_companies")
