"""Redact known environment credentials, sensitive mapping fields, and common tokens."""
import os
import re

SENSITIVE = re.compile(r"authorization|api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token", re.I)


def redact(value, secrets=()):
    known = tuple(s for s in secrets if s)
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(str(k)) else redact(v, known)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, known) for v in value]
    if isinstance(value, str):
        for secret in sorted(known, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"(?i)(api[_-]?key|password|secret|access_token)\s*[=:]\s*[^\s,;]+",
                       r"\1=[REDACTED]", value)
    return value


def environment_secrets():
    # Read only credential-shaped variable names, never log the environment itself.
    return tuple(v for k, v in os.environ.items() if SENSITIVE.search(k) and v)
