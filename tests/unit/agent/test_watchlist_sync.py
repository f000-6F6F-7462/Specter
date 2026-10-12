"""Unit tests for the edge agent watchlist sync reconciler."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from specter.agent.watchlist_sync import WatchlistSyncReconciler


@pytest.mark.asyncio
async def test_fetch_local_watchlists() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": "wl-1", "name": "VIP Guests", "target_type": "person"}
    ]
    mock_response.raise_for_status = MagicMock()

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    reconciler = WatchlistSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        api_token="test-token",
        http_client=client,
    )

    watchlists = await reconciler.fetch_local_watchlists()
    assert len(watchlists) == 1
    assert watchlists[0]["name"] == "VIP Guests"
    client.get.assert_awaited_once_with(
        "http://127.0.0.1:8000/owners/test-owner/watchlists",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
    )


@pytest.mark.asyncio
async def test_reconcile_watchlists_creates_missing() -> None:
    # 1. Local watchlists are empty
    get_response = MagicMock()
    get_response.status_code = 200
    get_response.json.return_value = []
    get_response.raise_for_status = MagicMock()

    # 2. Create response returns the newly created watchlist
    post_response = MagicMock()
    post_response.status_code = 201
    post_response.json.return_value = {
        "id": "wl-new",
        "name": "Blacklist",
        "target_type": "person",
    }
    post_response.raise_for_status = MagicMock()

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = get_response
    client.post.return_value = post_response

    reconciler = WatchlistSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        api_token="test-token",
        http_client=client,
    )

    cloud_watchlists = [{"name": "Blacklist", "target_type": "person"}]
    stats = await reconciler.reconcile_watchlists(cloud_watchlists)

    assert stats == {"created": 1, "unchanged": 0, "failed": 0}
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_watchlists_skips_existing() -> None:
    get_response = MagicMock()
    get_response.status_code = 200
    get_response.json.return_value = [{"id": "wl-1", "name": "Staff", "target_type": "person"}]
    get_response.raise_for_status = MagicMock()

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = get_response

    reconciler = WatchlistSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        api_token="test-token",
        http_client=client,
    )

    cloud_watchlists = [{"name": "Staff", "target_type": "person"}]
    stats = await reconciler.reconcile_watchlists(cloud_watchlists)

    assert stats == {"created": 0, "unchanged": 1, "failed": 0}
    client.post.assert_not_called()
