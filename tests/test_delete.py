import pytest
from fastapi import HTTPException

from main import delete_profile


class FakeProfile:
    def __init__(self, id, name=None):
        self.id = id
        self.name = name


class FakeSession:
    def __init__(self, store=None):
        # store is a dict id->FakeProfile
        self.store = store or {}

    async def get(self, model, id):
        return self.store.get(id)

    async def delete(self, obj):
        # emulate SQLAlchemy delete semantics by removing from store
        if obj.id in self.store:
            del self.store[obj.id]

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_delete_null_id_returns_400():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        await delete_profile("null", db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_nonint_id_returns_400():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        await delete_profile("abc", db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_not_found_returns_404():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        await delete_profile("9999", db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_success_removes_profile():
    store = {123: FakeProfile(123, name="Bob")}
    db = FakeSession(store)
    res = await delete_profile("123", db)
    assert isinstance(res, dict)
    assert res.get("message") == "Deleted"
    assert 123 not in store
