"""Loads the entity a request names, and treats another owner's entity as missing.

Answering 404 for another owner's entity, rather than 403, does not reveal that the id exists.
"""

import asyncio
from functools import partial

from specter.api.dependencies import ApiServices
from specter.core.errors import NotFoundError
from specter.entities.alerts import Alert
from specter.entities.cameras import Camera
from specter.entities.rules import LineCrossingRule, Rule, ZoneOccupancyRule
from specter.entities.targets import Target
from specter.entities.watchlists import Watchlist
from specter.entities.zones import Zone
from specter.storage.alerts import find_alert
from specter.storage.cameras import find_camera
from specter.storage.rules import find_rule
from specter.storage.targets import find_target
from specter.storage.watchlists import find_watchlist
from specter.storage.zones import find_zone


async def get_owner_camera(services: ApiServices, owner_id: str, camera_id: str) -> Camera:
    """Returns the owner's camera, with its credentials.

    Raises:
        NotFoundError: The owner has no such camera.
    """
    camera = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_camera, camera_id, services.cipher)
    )
    if camera is None or camera.owner_id != owner_id:
        raise NotFoundError(f"camera {camera_id} does not exist")
    return camera


async def get_owner_zone(
    services: ApiServices, owner_id: str, camera_id: str, zone_id: str
) -> Zone:
    """Returns a zone of the owner's camera.

    Raises:
        NotFoundError: The owner's camera has no such zone.
    """
    await get_owner_camera(services, owner_id, camera_id)
    zone = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_zone, zone_id)
    )
    if zone is None or zone.camera_id != camera_id:
        raise NotFoundError(f"zone {zone_id} does not exist")
    return zone


async def get_owner_rule(
    services: ApiServices, owner_id: str, camera_id: str, rule_id: str
) -> Rule:
    """Returns a rule of the owner's camera, whether it watches a zone or a line.

    Raises:
        NotFoundError: The owner's camera has no such rule.
    """
    await get_owner_camera(services, owner_id, camera_id)
    rule = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_rule, rule_id)
    )
    match rule:
        case LineCrossingRule() if rule.camera_id == camera_id:
            return rule
        case ZoneOccupancyRule():
            await get_owner_zone(services, owner_id, camera_id, rule.zone_id)
            return rule
    raise NotFoundError(f"rule {rule_id} does not exist")


async def get_owner_watchlist(services: ApiServices, owner_id: str, watchlist_id: str) -> Watchlist:
    """Returns the owner's watchlist.

    Raises:
        NotFoundError: The owner has no such watchlist.
    """
    watchlist = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_watchlist, watchlist_id)
    )
    if watchlist is None or watchlist.owner_id != owner_id:
        raise NotFoundError(f"watchlist {watchlist_id} does not exist")
    return watchlist


async def get_owner_target(
    services: ApiServices, owner_id: str, watchlist_id: str, target_id: str
) -> Target:
    """Returns a target of the owner's watchlist, with its reference images.

    Raises:
        NotFoundError: The owner's watchlist has no such target.
    """
    await get_owner_watchlist(services, owner_id, watchlist_id)
    target = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_target, target_id)
    )
    if target is None or target.watchlist_id != watchlist_id:
        raise NotFoundError(f"target {target_id} does not exist")
    return target


async def get_owner_alert(services: ApiServices, owner_id: str, alert_id: str) -> Alert:
    """Returns the owner's alert of either kind.

    Raises:
        NotFoundError: The owner has no such alert.
    """
    alert = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(find_alert, alert_id)
    )
    if alert is None or alert.owner_id != owner_id:
        raise NotFoundError(f"alert {alert_id} does not exist")
    return alert
