"""Domain exceptions mapped to HTTP responses in api/v1/errors.py."""
from __future__ import annotations


class AppError(Exception):
    """Base application error."""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class BadRequestError(AppError):
    status_code = 400


class PayloadTooLargeError(AppError):
    status_code = 413


class ServiceError(AppError):
    status_code = 502
