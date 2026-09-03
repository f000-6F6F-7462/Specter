"""Catalog context — watchlists, targets, and reference-image enrollment.

Public API: the use-case functions and the DTOs re-exported below. Callers pass the
dependencies first, then the request; nothing else in this package is importable
surface.
"""

from specter.application.catalog.dto import (
    AddImagesRequest,
    CreateWatchlistRequest,
    EnrolledTargetView,
    EnrollmentBatchView,
    EnrollTargetsRequest,
    EnrollTargetsResult,
    ReferenceImageView,
    TargetSpec,
    TargetView,
    UpdateTargetRequest,
    UpdateWatchlistRequest,
    UploadedImage,
    WatchlistView,
)
from specter.application.catalog.use_cases import (
    add_target_images,
    batch_delete_targets,
    create_watchlist,
    delete_target,
    delete_target_image,
    delete_watchlist,
    enroll_targets,
    get_enrollment_batch,
    get_target,
    get_watchlist,
    list_targets,
    list_watchlists,
    update_target,
    update_watchlist,
)

__all__ = [
    "AddImagesRequest",
    "CreateWatchlistRequest",
    "EnrolledTargetView",
    "EnrollmentBatchView",
    "EnrollTargetsRequest",
    "EnrollTargetsResult",
    "ReferenceImageView",
    "TargetSpec",
    "TargetView",
    "UpdateTargetRequest",
    "UpdateWatchlistRequest",
    "UploadedImage",
    "WatchlistView",
    "add_target_images",
    "batch_delete_targets",
    "create_watchlist",
    "delete_target",
    "delete_target_image",
    "delete_watchlist",
    "enroll_targets",
    "get_enrollment_batch",
    "get_target",
    "get_watchlist",
    "list_targets",
    "list_watchlists",
    "update_target",
    "update_watchlist",
]
