"""Exception hierarchy for the whole engine.

`code` is a stable machine string used later to build responses at
the HTTP edge and to tag structured logs.
"""


class SpecterError(Exception):
    """Root of every error this codebase raises deliberately."""

    code: str = "specter_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__class__.__doc__ or self.code)
        self.message = message


class ConfigurationError(SpecterError):
    """Settings are missing or inconsistent."""

    code = "configuration_error"


class RuleViolation(SpecterError):
    """A domain invariant was violated (bad value object, illegal state transition)."""

    code = "rule_violation"


class NotFoundError(SpecterError):
    """A requested resource does not exist for this owner."""

    code = "not_found"


class ConflictError(SpecterError):
    """The request conflicts with current state (duplicate, concurrent edit)."""

    code = "conflict"


class DependencyFailure(SpecterError):
    """An external dependency (db, redis, qdrant, minio, a model) failed."""

    code = "dependency_failure"
