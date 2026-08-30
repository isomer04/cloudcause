"""FOCUS and export schema version guards.

Unknown future schema versions are rejected instead of being parsed with the
1.4 reader. Failing safely is a hard requirement of the MVP.
"""

from __future__ import annotations

from cloudcause_contracts import SUPPORTED_FOCUS_VERSION

SUPPORTED_FOCUS_VERSIONS = frozenset({SUPPORTED_FOCUS_VERSION})

#: Provider export schema versions this parser set understands.
SUPPORTED_EXPORT_SCHEMA_VERSIONS: dict[str, frozenset[str]] = {
    "aws": frozenset({"1", "1.0", "2.0"}),
    "azure": frozenset({"1", "1.0", "2023-11-01"}),
    "gcp": frozenset({"1", "1.0"}),
}


class UnsupportedSchemaVersionError(ValueError):
    """Raised when data declares a schema version CloudCause cannot interpret."""

    def __init__(self, kind: str, version: str, supported: frozenset[str]) -> None:
        self.kind = kind
        self.version = version
        self.supported = supported
        super().__init__(
            f"unsupported {kind} schema version {version!r}; "
            f"CloudCause supports {sorted(supported)}. Data was quarantined instead of parsed."
        )


def require_supported_focus_version(version: str) -> str:
    if version not in SUPPORTED_FOCUS_VERSIONS:
        raise UnsupportedSchemaVersionError("FOCUS", version, SUPPORTED_FOCUS_VERSIONS)
    return version


def require_supported_export_schema(provider: str, version: str) -> str:
    supported = SUPPORTED_EXPORT_SCHEMA_VERSIONS.get(provider, frozenset())
    if version not in supported:
        raise UnsupportedSchemaVersionError(f"{provider} export", version, supported)
    return version
