"""Shared kernel: configuration, clock, ids, logging, errors, and the DI container.

Kept import-light on purpose — importing this package must not pull in pydantic or
any framework. Import the specific submodule you need.
"""
