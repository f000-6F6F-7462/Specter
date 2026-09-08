"""Enrollment context — turn a queued reference image into a stored embedding.

Public API: ``run_enrollment`` and the ``EnrollmentDeps`` bundle it takes.
"""

from specter.application.enrollment.use_cases import EnrollmentDeps, run_enrollment

__all__ = ["EnrollmentDeps", "run_enrollment"]
