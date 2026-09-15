"""Errors that Specter raises deliberately."""


class SpecterError(Exception):
    """Base class for errors that Specter raises deliberately."""


class ConfigurationError(SpecterError):
    """Static settings are missing, unreadable or invalid."""


class InvalidEntityError(SpecterError, ValueError):
    """An entity was given a value that breaks one of its rules."""


class NotFoundError(SpecterError, LookupError):
    """A requested entity does not exist."""
