"""Typed failures that map cleanly onto HTTP responses."""

from __future__ import annotations


class ScraperError(Exception):
    """Base class. `status` is the HTTP code, `code` the stable machine string."""

    status = 500
    code = "INTERNAL_ERROR"
    message = "Unexpected error."

    def __init__(self, message: str | None = None, detail: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.detail = detail

    def payload(self) -> dict:
        body: dict = {"code": self.code, "message": self.message}
        if self.detail:
            body["detail"] = self.detail
        return {"error": body}


class InvalidProfileURL(ScraperError):
    status = 400
    code = "INVALID_URL"
    message = "Not a valid LinkedIn profile URL or public identifier."


class ProfileNotFound(ScraperError):
    status = 404
    code = "PROFILE_NOT_FOUND"
    message = "No LinkedIn profile exists at that identifier."


class RateLimited(ScraperError):
    status = 429
    code = "RATE_LIMITED"
    message = "Rate limit exceeded."

    def __init__(self, retry_after: float, message: str | None = None):
        super().__init__(message)
        self.retry_after = max(1, int(round(retry_after)))


class Blocked(ScraperError):
    status = 502
    code = "BLOCKED"
    message = "LinkedIn served an authentication wall for every strategy tried."


class UpstreamTimeout(ScraperError):
    status = 504
    code = "UPSTREAM_TIMEOUT"
    message = "LinkedIn did not respond in time."


class UpstreamError(ScraperError):
    status = 502
    code = "UPSTREAM_ERROR"
    message = "Could not reach LinkedIn."


class Unauthorized(ScraperError):
    status = 401
    code = "UNAUTHORIZED"
    message = "Missing or invalid API key."


class SessionNotConfigured(ScraperError):
    status = 503
    code = "SESSION_NOT_CONFIGURED"
    message = (
        "Contact lookup needs an authenticated LinkedIn session, which this "
        "instance has not been given. Set LINKEDIN_LI_AT and LINKEDIN_JSESSIONID."
    )


class SessionInvalid(ScraperError):
    status = 502
    code = "SESSION_INVALID"
    message = "The configured LinkedIn session was rejected — it is expired or invalid."


class EndpointRetired(ScraperError):
    status = 502
    code = "ENDPOINT_RETIRED"
    message = (
        "LinkedIn returned 410 Gone for the contact endpoint. The dash query path "
        "has moved — update CONTACT_ENDPOINT to the current one."
    )
