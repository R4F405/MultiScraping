from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDescriptor:
    code: str
    retryable: bool
    action: str


ERROR_CATALOG: dict[str, ErrorDescriptor] = {
    "NETWORK_TIMEOUT": ErrorDescriptor("NETWORK_TIMEOUT", True, "retry_with_backoff"),
    "PROXY_CONNECT_FAIL": ErrorDescriptor("PROXY_CONNECT_FAIL", True, "rotate_proxy"),
    "IG_CHALLENGE": ErrorDescriptor("IG_CHALLENGE", True, "rotate_identity"),
    "IG_RATE_LIMIT": ErrorDescriptor("IG_RATE_LIMIT", True, "wait"),
    "EMPTY_RESULT": ErrorDescriptor("EMPTY_RESULT", False, "escalate_to_login"),
    "PARSER_SCHEMA_DRIFT": ErrorDescriptor("PARSER_SCHEMA_DRIFT", False, "fallback_strategy"),
    "UNCLASSIFIED": ErrorDescriptor("UNCLASSIFIED", False, "inspect"),
}


def classify_exception(exc: Exception) -> ErrorDescriptor:
    msg = str(exc).lower()
    if "timeout" in msg:
        return ERROR_CATALOG["NETWORK_TIMEOUT"]
    if "proxy" in msg:
        return ERROR_CATALOG["PROXY_CONNECT_FAIL"]
    if "challenge" in msg or "checkpoint" in msg or "captcha" in msg:
        return ERROR_CATALOG["IG_CHALLENGE"]
    if "429" in msg or "rate limit" in msg:
        return ERROR_CATALOG["IG_RATE_LIMIT"]
    if "schema" in msg or "parser" in msg:
        return ERROR_CATALOG["PARSER_SCHEMA_DRIFT"]
    return ERROR_CATALOG["UNCLASSIFIED"]
