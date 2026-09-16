"""Owners as a whole: removing everything a user of the application server had on the device."""

import asyncio
from functools import partial

from fastapi import APIRouter, status

from specter.api.dependencies import ServicesDependency
from specter.api.errors import change_vector_index
from specter.api.notifications import publish_configuration_change
from specter.messaging.messages import ChangeKind, EntityKind
from specter.storage.owners import delete_owner_data

router = APIRouter(prefix="/owners/{owner_id}", tags=["owners"])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_owner(owner_id: str, services: ServicesDependency) -> None:
    """Deletes all of the owner's data: cameras, watchlists, targets, images, alerts and evidence.

    Deleting an owner that has no data succeeds too, so the call can be repeated safely.
    """
    await change_vector_index(services.vector_index.delete_owner(owner_id))
    deleted_data = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_owner_data, owner_id)
    )
    await asyncio.to_thread(services.reference_image_store.delete_owner_images, owner_id)
    await asyncio.to_thread(services.evidence_store.delete_owner_evidence, owner_id)
    # The camera manager stops the deleted cameras and removes their streams from go2rtc.
    for camera_id in deleted_data.camera_ids:
        await publish_configuration_change(
            services.message_bus, owner_id, EntityKind.CAMERA, camera_id, ChangeKind.DELETED
        )
    for watchlist_id in deleted_data.watchlist_ids:
        await publish_configuration_change(
            services.message_bus, owner_id, EntityKind.WATCHLIST, watchlist_id, ChangeKind.DELETED
        )
