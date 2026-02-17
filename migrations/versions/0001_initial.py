"""Initial migration: indexes, boolean status columns, date dob

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision  = "0001_initial"
down_revision = None
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. Convert status columns from VARCHAR to BOOLEAN ─────────────────────
    # Map "active" -> TRUE, everything else -> FALSE (NULL stays NULL).
    for col in [
        "facebook_active_status",  "facebook_verified_status",
        "twitter_active_status",   "twitter_verified_status",
        "instagram_active_status", "instagram_verified_status",
    ]:
        # Add a temp boolean column
        op.add_column("social_profiles",
            sa.Column(f"{col}_bool", sa.Boolean(), nullable=True),
            schema="public",
        )
        # Populate it
        op.execute(
            f"""
            UPDATE public.social_profiles
            SET {col}_bool = CASE
                WHEN LOWER({col}) IN ('active', 'verified', 'true', '1', 'yes') THEN TRUE
                WHEN {col} IS NULL THEN NULL
                ELSE FALSE
            END
            """
        )
        # Drop old, rename new
        op.drop_column("social_profiles", col, schema="public")
        op.alter_column("social_profiles", f"{col}_bool",
                        new_column_name=col, schema="public")

    # ── 2. Convert dob from VARCHAR to DATE ───────────────────────────────────
    # Add a temp date column, cast, drop old, rename.
    op.add_column("social_profiles",
        sa.Column("dob_date", sa.Date(), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.social_profiles
        SET dob_date = CASE
            WHEN dob IS NULL OR TRIM(dob::text) = '' THEN NULL
            ELSE dob::text::date
        END
        """
    )
    op.drop_column("social_profiles", "dob", schema="public")
    op.alter_column("social_profiles", "dob_date",
                    new_column_name="dob", schema="public")

    # ── 3. B-tree indexes ─────────────────────────────────────────────────────
    btree_indexes = [
        ("ix_sp_zone",            "zone"),
        ("ix_sp_party_district",  "party_district"),
        ("ix_sp_constituency",    "constituency"),
        ("ix_sp_designation",     "designation"),
        ("ix_sp_fb_followers",    "facebook_followers"),
        ("ix_sp_tw_followers",    "twitter_followers"),
        ("ix_sp_ig_followers",    "instagram_followers"),
    ]
    for idx_name, col in btree_indexes:
        op.create_index(idx_name, "social_profiles", [col],
                        unique=False, schema="public")

    # Composite index for the most common filter combo
    op.create_index("ix_sp_zone_designation", "social_profiles",
                    ["zone", "designation"], unique=False, schema="public")

    # ── 4. GIN index for full-text search ─────────────────────────────────────
    # Cannot be expressed as a simple column index; use raw DDL.
    op.execute(
        """
        CREATE INDEX ix_sp_fts ON public.social_profiles
        USING GIN (
            to_tsvector(
                'english',
                coalesce(name,         '') || ' ' ||
                coalesce(constituency,  '') || ' ' ||
                coalesce(designation,   '') || ' ' ||
                coalesce(zone,          '') || ' ' ||
                coalesce(email_id,      '')
            )
        )
        """
    )


def downgrade() -> None:
    # Drop GIN index
    op.execute("DROP INDEX IF EXISTS public.ix_sp_fts")

    # Drop B-tree indexes
    for idx_name, _ in [
        ("ix_sp_zone",            "zone"),
        ("ix_sp_party_district",  "party_district"),
        ("ix_sp_constituency",    "constituency"),
        ("ix_sp_designation",     "designation"),
        ("ix_sp_fb_followers",    "facebook_followers"),
        ("ix_sp_tw_followers",    "twitter_followers"),
        ("ix_sp_ig_followers",    "instagram_followers"),
        ("ix_sp_zone_designation", None),
    ]:
        op.drop_index(idx_name, table_name="social_profiles", schema="public")

    # Revert boolean columns back to VARCHAR
    for col in [
        "facebook_active_status",  "facebook_verified_status",
        "twitter_active_status",   "twitter_verified_status",
        "instagram_active_status", "instagram_verified_status",
    ]:
        op.add_column("social_profiles",
            sa.Column(f"{col}_str", sa.String(50), nullable=True),
            schema="public",
        )
        op.execute(
            f"""
            UPDATE public.social_profiles
            SET {col}_str = CASE
                WHEN {col} = TRUE  THEN 'active'
                WHEN {col} = FALSE THEN 'inactive'
                ELSE NULL
            END
            """
        )
        op.drop_column("social_profiles", col, schema="public")
        op.alter_column("social_profiles", f"{col}_str",
                        new_column_name=col, schema="public")

    # Revert dob back to VARCHAR
    op.add_column("social_profiles",
        sa.Column("dob_str", sa.String(50), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.social_profiles
        SET dob_str = dob::text
        """
    )
    op.drop_column("social_profiles", "dob", schema="public")
    op.alter_column("social_profiles", "dob_str",
                    new_column_name="dob", schema="public")