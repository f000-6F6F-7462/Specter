"""Rules of a camera: zone occupancy and line crossing, which raise alerts about any object."""

import asyncio
from dataclasses import replace
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.notifications import publish_configuration_change
from specter.api.ownership import get_owner_camera, get_owner_rule, get_owner_zone
from specter.api.schemas import PointBody, RequestModel
from specter.core.errors import InvalidEntityError
from specter.core.identifiers import new_identifier
from specter.entities.rules import (
    CrossingDirection,
    LineCrossingRule,
    Rule,
    RuleKind,
    ZoneOccupancyRule,
)
from specter.messaging.messages import ChangeKind, EntityKind
from specter.storage.rules import delete_rule, list_camera_rules, save_rule

router = APIRouter(prefix="/owners/{owner_id}/cameras/{camera_id}/rules", tags=["rules"])


class ZoneOccupancyRuleCreateBody(RequestModel):
    """A new rule that fires when an object stays in a zone long enough."""

    kind: Literal[RuleKind.ZONE_OCCUPANCY]
    zone_id: str
    # Empty means objects of every class.
    object_classes: list[str] = Field(default_factory=list)
    minimum_dwell_seconds: float = Field(default=0.0, ge=0)
    is_enabled: bool = True


class LineCrossingRuleCreateBody(RequestModel):
    """A new rule that fires when an object crosses a line in a given direction."""

    kind: Literal[RuleKind.LINE_CROSSING]
    line_start: PointBody
    line_end: PointBody
    direction: CrossingDirection = CrossingDirection.EITHER
    # Empty means objects of every class.
    object_classes: list[str] = Field(default_factory=list)
    is_enabled: bool = True


type RuleCreateBody = Annotated[
    ZoneOccupancyRuleCreateBody | LineCrossingRuleCreateBody, Field(discriminator="kind")
]


class RuleUpdateBody(RequestModel):
    """Fields of a rule to change; omitted fields keep their value.

    Dwell time applies only to zone occupancy rules, and the line only to line crossing rules.
    """

    object_classes: list[str] | None = None
    is_enabled: bool | None = None
    minimum_dwell_seconds: float | None = Field(default=None, ge=0)
    line_start: PointBody | None = None
    line_end: PointBody | None = None
    direction: CrossingDirection | None = None


class RuleResponse(BaseModel):
    """A rule of either kind; fields of the other kind are null."""

    id: str
    kind: RuleKind
    camera_id: str
    zone_id: str | None
    object_classes: list[str]
    is_enabled: bool
    minimum_dwell_seconds: float | None
    line_start: PointBody | None
    line_end: PointBody | None
    direction: CrossingDirection | None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rule(
    owner_id: str, camera_id: str, body: RuleCreateBody, services: ServicesDependency
) -> RuleResponse:
    """Adds a rule to the owner's camera."""
    rule: Rule
    match body:
        case ZoneOccupancyRuleCreateBody():
            await get_owner_zone(services, owner_id, camera_id, body.zone_id)
            rule = ZoneOccupancyRule(
                id=new_identifier("rule"),
                zone_id=body.zone_id,
                object_classes=frozenset(body.object_classes),
                minimum_dwell_seconds=body.minimum_dwell_seconds,
                is_enabled=body.is_enabled,
            )
        case LineCrossingRuleCreateBody():
            await get_owner_camera(services, owner_id, camera_id)
            rule = LineCrossingRule(
                id=new_identifier("rule"),
                camera_id=camera_id,
                line_start=body.line_start.to_point(),
                line_end=body.line_end.to_point(),
                direction=body.direction,
                object_classes=frozenset(body.object_classes),
                is_enabled=body.is_enabled,
            )
    await store_rule(services, owner_id, rule, ChangeKind.CREATED)
    return build_rule_response(rule, camera_id)


@router.get("")
async def list_rules(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> list[RuleResponse]:
    """Lists the rules of the owner's camera: zone occupancy rules first, then line rules."""
    await get_owner_camera(services, owner_id, camera_id)
    rules = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_camera_rules, camera_id)
    )
    return [build_rule_response(rule, camera_id) for rule in rules]


@router.get("/{rule_id}")
async def read_rule(
    owner_id: str, camera_id: str, rule_id: str, services: ServicesDependency
) -> RuleResponse:
    """Returns a rule of the owner's camera."""
    return build_rule_response(
        await get_owner_rule(services, owner_id, camera_id, rule_id), camera_id
    )


@router.patch("/{rule_id}")
async def update_rule(
    owner_id: str,
    camera_id: str,
    rule_id: str,
    body: RuleUpdateBody,
    services: ServicesDependency,
) -> RuleResponse:
    """Changes the given fields of the rule.

    Raises:
        InvalidEntityError: A field does not apply to the rule's kind.
    """
    rule = await get_owner_rule(services, owner_id, camera_id, rule_id)
    object_classes = (
        rule.object_classes if body.object_classes is None else frozenset(body.object_classes)
    )
    is_enabled = rule.is_enabled if body.is_enabled is None else body.is_enabled
    updated_rule: Rule
    match rule:
        case ZoneOccupancyRule():
            if body.line_start or body.line_end or body.direction:
                raise InvalidEntityError("a zone occupancy rule has no line or direction")
            updated_rule = replace(
                rule,
                object_classes=object_classes,
                is_enabled=is_enabled,
                minimum_dwell_seconds=(
                    rule.minimum_dwell_seconds
                    if body.minimum_dwell_seconds is None
                    else body.minimum_dwell_seconds
                ),
            )
        case LineCrossingRule():
            if body.minimum_dwell_seconds is not None:
                raise InvalidEntityError("a line crossing rule has no dwell time")
            updated_rule = replace(
                rule,
                object_classes=object_classes,
                is_enabled=is_enabled,
                line_start=rule.line_start
                if body.line_start is None
                else body.line_start.to_point(),
                line_end=rule.line_end if body.line_end is None else body.line_end.to_point(),
                direction=body.direction or rule.direction,
            )
    await store_rule(services, owner_id, updated_rule, ChangeKind.UPDATED)
    return build_rule_response(updated_rule, camera_id)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_rule(
    owner_id: str, camera_id: str, rule_id: str, services: ServicesDependency
) -> None:
    """Deletes the rule; its alerts stay."""
    await get_owner_rule(services, owner_id, camera_id, rule_id)
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_rule, rule_id)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.RULE, rule_id, ChangeKind.DELETED
    )


async def store_rule(
    services: ApiServices, owner_id: str, rule: Rule, change_kind: ChangeKind
) -> None:
    """Saves the rule and tells the camera's process about the change."""
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_rule, rule)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.RULE, rule.id, change_kind
    )


def build_rule_response(rule: Rule, camera_id: str) -> RuleResponse:
    """Returns the rule's response."""
    match rule:
        case ZoneOccupancyRule():
            return RuleResponse(
                id=rule.id,
                kind=rule.kind,
                camera_id=camera_id,
                zone_id=rule.zone_id,
                object_classes=sorted(rule.object_classes),
                is_enabled=rule.is_enabled,
                minimum_dwell_seconds=rule.minimum_dwell_seconds,
                line_start=None,
                line_end=None,
                direction=None,
            )
        case LineCrossingRule():
            return RuleResponse(
                id=rule.id,
                kind=rule.kind,
                camera_id=camera_id,
                zone_id=None,
                object_classes=sorted(rule.object_classes),
                is_enabled=rule.is_enabled,
                minimum_dwell_seconds=None,
                line_start=PointBody.from_point(rule.line_start),
                line_end=PointBody.from_point(rule.line_end),
                direction=rule.direction,
            )
