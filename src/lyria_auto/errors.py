class LyriaAutoError(Exception):
    """Base application error."""


class ConfigurationError(LyriaAutoError):
    pass


class SafetyBlockedError(LyriaAutoError):
    pass


class GenerationError(LyriaAutoError):
    pass


class MediaError(LyriaAutoError):
    pass


class UploadError(LyriaAutoError):
    pass


from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredError:
    code: str
    problem: str
    cause: str
    fix: str
    next_command: str
    job_id: int | None = None
    attempt_id: int | None = None
    log_reference: str | None = None


class VisualGenerationError(LyriaAutoError):
    code = "VISUAL_GENERATION_FAILED"


class PaidStartUncertainError(VisualGenerationError):
    """遠端可能已接受非冪等付費 start；禁止透明重試。"""
    code = "VISUAL_START_UNCERTAIN"


class VisualPollTransientError(VisualGenerationError):
    """operation ID 已保存，poll／download 可安全續跑。"""
    code = "VISUAL_POLL_TRANSIENT"


class VisualPreflightError(LyriaAutoError):
    code = "VISUAL_PREFLIGHT_FAILED"


class VisualApprovalError(LyriaAutoError):
    code = "VISUAL_APPROVAL_FAILED"


class MigrationInvariantError(LyriaAutoError):
    code = "MIGRATION_INVARIANT_FAILED"


class StaleStateError(LyriaAutoError):
    code = "STALE_STATE"


def structured_error(**kwargs) -> StructuredError:
    return StructuredError(**kwargs)


def sanitize_exception(exc: Exception, **kwargs) -> StructuredError:
    problem = str(exc)
    for secret in ("API_KEY", "Bearer ", "OAuth ", "key="):
        problem = problem.replace(secret, "[REDACTED]")
    code = getattr(exc, "code", "UNKNOWN_ERROR")
    if "code" in kwargs:
        code = kwargs.pop("code")
    return StructuredError(
        code=code,
        problem=problem,
        cause="Unknown",
        fix="Check logs",
        next_command="",
        **kwargs
    )
