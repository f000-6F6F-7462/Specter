from fastapi import APIRouter, status

from specter.application import catalog
from specter.entrypoints.http.deps import OwnerDep, UowDep
from specter.entrypoints.http.schemas import WatchlistCreate, WatchlistOut, WatchlistUpdate

router = APIRouter(tags=["watchlists"])


@router.post("/watchlists", status_code=status.HTTP_201_CREATED)
async def create_watchlist(body: WatchlistCreate, owner: OwnerDep, uow: UowDep) -> WatchlistOut:
    view = await catalog.create_watchlist(
        uow,
        catalog.CreateWatchlistRequest(
            owner_id=owner,
            name=body.name,
            type=body.type,
            kind=body.kind,
            match_threshold=body.match_threshold,
        ),
    )
    return WatchlistOut.of(view)


@router.get("/watchlists")
async def list_watchlists(owner: OwnerDep, uow: UowDep) -> list[WatchlistOut]:
    views = await catalog.list_watchlists(uow, owner)
    return [WatchlistOut.of(v) for v in views]


@router.get("/watchlists/{watchlist_id}")
async def get_watchlist(watchlist_id: str, owner: OwnerDep, uow: UowDep) -> WatchlistOut:
    return WatchlistOut.of(await catalog.get_watchlist(uow, owner, watchlist_id))


@router.patch("/watchlists/{watchlist_id}")
async def update_watchlist(
    watchlist_id: str, body: WatchlistUpdate, owner: OwnerDep, uow: UowDep
) -> WatchlistOut:
    view = await catalog.update_watchlist(
        uow,
        owner,
        watchlist_id,
        catalog.UpdateWatchlistRequest(name=body.name, match_threshold=body.match_threshold),
    )
    return WatchlistOut.of(view)


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(watchlist_id: str, owner: OwnerDep, uow: UowDep) -> None:
    await catalog.delete_watchlist(uow, owner, watchlist_id)
