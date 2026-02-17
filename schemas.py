from pydantic import BaseModel, field_validator, model_validator, EmailStr, Field
from typing import Optional
from datetime import datetime, date


# ── Shared base ────────────────────────────────────────────────────────────────
class ProfileBase(BaseModel):
    zone:            Optional[str] = Field(None, max_length=200)
    party_district:  Optional[str] = Field(None, max_length=200)
    constituency:    Optional[str] = Field(None, max_length=200)
    designation:     Optional[str] = Field(None, max_length=200)
    name:            Optional[str] = Field(None, max_length=500)
    whatsapp_number: Optional[str] = Field(None, max_length=50)
    dob:             Optional[date] = None
    address:         Optional[str] = None
    email_id:        Optional[EmailStr] = None

    facebook_id:               Optional[str]  = Field(None, max_length=500)
    facebook_followers:        Optional[int]  = Field(None, ge=0)
    facebook_active_status:    Optional[bool] = None
    facebook_verified_status:  Optional[bool] = None

    twitter_id:                Optional[str]  = Field(None, max_length=500)
    twitter_followers:         Optional[int]  = Field(None, ge=0)
    twitter_active_status:     Optional[bool] = None
    twitter_verified_status:   Optional[bool] = None

    instagram_id:              Optional[str]  = Field(None, max_length=500)
    instagram_followers:       Optional[int]  = Field(None, ge=0)
    instagram_active_status:   Optional[bool] = None
    instagram_verified_status: Optional[bool] = None


# ── Create ─────────────────────────────────────────────────────────────────────
class ProfileCreate(ProfileBase):
    """At minimum, a profile must have a name. Social media IDs are optional."""
    name: str = Field(..., min_length=1, max_length=500)

    # REMOVED: must_have_at_least_one_platform validator — it was too strict
    # and caused 422 errors for legitimate profiles with no social IDs yet.


# ── Update ─────────────────────────────────────────────────────────────────────
class ProfileUpdate(ProfileBase):
    """All fields optional — only supplied fields are patched (PATCH semantics)."""

    @model_validator(mode="after")
    def at_least_one_field_set(self) -> "ProfileUpdate":
        if not any(v is not None for v in self.model_dump().values()):
            raise ValueError("At least one field must be provided for an update.")
        return self


# ── Response ───────────────────────────────────────────────────────────────────
class ProfileResponse(ProfileBase):
    id:         int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Cursor pagination ──────────────────────────────────────────────────────────
class ProfileListResponse(BaseModel):
    rows:        list[ProfileResponse]
    total:       int
    next_cursor: Optional[int] = None  # None means no more pages


# ── Bulk ops ───────────────────────────────────────────────────────────────────
class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=500)