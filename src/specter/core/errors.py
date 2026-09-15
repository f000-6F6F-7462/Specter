"""Errors that Specter raises deliberately."""


class SpecterError(Exception):
    """Base class for errors that Specter raises deliberately."""


class ConfigurationError(SpecterError):
    """Static settings are missing, unreadable or invalid."""
