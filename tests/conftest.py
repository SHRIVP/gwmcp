import pytest

import mock
import state_store


@pytest.fixture(autouse=True)
def no_real_property_lookups(monkeypatch):
    """Tests must never call Census or FEMA: any test reaching the real lookup fails."""
    async def forbidden(address, *a, **kw):
        raise AssertionError(f"real property lookup attempted for {address!r}; inject a fake")
    monkeypatch.setattr(mock, "lookup_property", forbidden)
    monkeypatch.setattr(state_store, "lookup_property", forbidden)
