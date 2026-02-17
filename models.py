from sqlalchemy import (
    Column, Integer, BigInteger, String, Text,
    DateTime, Boolean, Date, Index, func,
)
from database import Base


class SocialProfile(Base):
    __tablename__ = "social_profiles"

    # ── Composite indexes + GIN FTS index ─────────────────────────────────────
    # NOTE: The GIN index (ix_sp_fts) cannot be expressed as a simple column
    # index — it is created by the Alembic migration (see migrations/versions/).
    # All other indexes below are standard B-tree and are auto-created by Alembic.
    __table_args__ = (
        # Filter indexes — every column used in WHERE clauses gets an index.
        Index("ix_sp_zone",           "zone"),
        Index("ix_sp_party_district", "party_district"),
        Index("ix_sp_constituency",   "constituency"),
        Index("ix_sp_designation",    "designation"),
        # Composite index for the most common combined filter pattern.
        Index("ix_sp_zone_designation", "zone", "designation"),
        # Follower sort indexes — used by ORDER BY on listing endpoint.
        Index("ix_sp_fb_followers",  "facebook_followers"),
        Index("ix_sp_tw_followers",  "twitter_followers"),
        Index("ix_sp_ig_followers",  "instagram_followers"),
        {"schema": "public"},
    )

    id               = Column(Integer, primary_key=True, index=True, autoincrement=True)
    zone             = Column(String(200))
    party_district   = Column(String(200))
    constituency     = Column(String(200))
    designation      = Column(String(200))
    name             = Column(String(500))
    whatsapp_number  = Column(String(50))

    # ── dob: store as a real Date so you can do date arithmetic in SQL ─────────
    # MIGRATION NOTE: if you have existing String data, the Alembic migration
    # casts it with `ALTER COLUMN dob TYPE DATE USING dob::date`.
    dob              = Column(Date, nullable=True)

    address          = Column(Text)
    email_id         = Column(String(500))

    # ── Facebook ───────────────────────────────────────────────────────────────
    facebook_id              = Column(String(500))
    facebook_followers       = Column(BigInteger, nullable=True)
    # Boolean replaces the old "active"/"inactive" strings:
    #   True  = active,  False = inactive,  None = unknown
    facebook_active_status   = Column(Boolean, nullable=True)
    facebook_verified_status = Column(Boolean, nullable=True)

    # ── Twitter / X ────────────────────────────────────────────────────────────
    twitter_id               = Column(String(500))
    twitter_followers        = Column(BigInteger, nullable=True)
    twitter_active_status    = Column(Boolean, nullable=True)
    twitter_verified_status  = Column(Boolean, nullable=True)

    # ── Instagram ──────────────────────────────────────────────────────────────
    instagram_id               = Column(String(500))
    instagram_followers        = Column(BigInteger, nullable=True)
    instagram_active_status    = Column(Boolean, nullable=True)
    instagram_verified_status  = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)
