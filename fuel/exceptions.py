"""Domain exceptions and stable API error codes."""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from typing import Any

from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def sanitize_retry_after(value: Any) -> str | None:
    """Return a valid Retry-After value (seconds or HTTP-date), else None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    return text if parsed is not None else None


class DomainError(Exception):
    """Base class for errors translated to API responses."""

    code = "internal_error"
    message = "Internal server error."
    status_code = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        retry_after: str | int | None = None,
    ) -> None:
        self.detail = detail
        self.details = details
        self.retry_after = retry_after
        super().__init__(detail or self.message)


class BadRequestError(DomainError):
    code = "bad_request"
    message = "Bad request."
    status_code = 400


class InvalidLocationError(BadRequestError):
    code = "invalid_location"
    message = "Invalid location."
    status_code = 400


class LocationNotFoundError(DomainError):
    code = "location_not_found"
    message = "Location not found."
    status_code = 404


class RouteNotFeasibleError(DomainError):
    code = "route_not_feasible"
    message = "Route is not feasible."
    status_code = 422


class ExternalServiceError(DomainError):
    code = "external_service_error"
    message = "External service error."
    status_code = 502


class ExternalServiceBadResponseError(ExternalServiceError):
    code = "external_service_bad_response"
    message = "External service returned an invalid response."
    status_code = 502


class ExternalServiceUnavailableError(ExternalServiceError):
    code = "external_service_unavailable"
    message = "External service is unavailable."
    status_code = 503


class ExternalServiceTimeoutError(ExternalServiceError):
    code = "external_service_timeout"
    message = "External service timed out."
    status_code = 504


class ExternalServiceRateLimitedError(ExternalServiceError):
    code = "external_rate_limited"
    message = "External service rate limit exceeded."
    status_code = 503


class ServerMisconfiguredError(DomainError):
    code = "server_misconfigured"
    message = "Server misconfiguration."
    status_code = 500


ERROR_CATALOG = {
    "bad_request": (400, "Bad request."),
    "invalid_location": (400, "Invalid location."),
    "location_not_found": (404, "Location not found."),
    "route_not_feasible": (422, "Route is not feasible."),
    "external_service_bad_response": (502, "External service returned an invalid response."),
    "external_service_unavailable": (503, "External service is unavailable."),
    "external_rate_limited": (503, "External service rate limit exceeded."),
    "external_service_timeout": (504, "External service timed out."),
    "server_misconfigured": (500, "Server misconfiguration."),
    "internal_error": (500, "Internal server error."),
}


def _error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _log_domain_error(exc: DomainError) -> None:
    detail = exc.detail or str(exc)
    if exc.status_code >= 500:
        logger.error("domain_error code=%s detail=%s", exc.code, detail, exc_info=True)
    else:
        logger.warning("domain_error code=%s detail=%s", exc.code, detail)


def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Translate exceptions to a uniform API error response."""
    if isinstance(exc, DomainError):
        _log_domain_error(exc)
        response = Response(
            _error_payload(exc.code, exc.message, exc.details),
            status=exc.status_code,
        )
        retry_after = sanitize_retry_after(exc.retry_after)
        if retry_after is not None:
            response["Retry-After"] = retry_after
        return response

    response = exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, (drf_exceptions.ParseError, drf_exceptions.ValidationError)):
            code, message = "bad_request", "Bad request."
            details = response.data
        elif isinstance(exc, (drf_exceptions.NotFound, Http404)):
            code, message = "location_not_found", "Location not found."
            details = {}
        else:
            code, message = "bad_request", "Bad request."
            details = {}
        logger.warning(
            "request_error code=%s status=%s detail=%s",
            code,
            response.status_code,
            response.data,
        )
        response.data = _error_payload(code, message, details)
        return response

    logger.exception("unexpected_error detail=%s", str(exc))
    return Response(_error_payload("internal_error", "Internal server error."), status=500)
