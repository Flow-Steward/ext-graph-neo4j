"""Closed, bounded Neo4j inputs and connection values."""

import ipaddress
import json
import math
import re


class Failure(Exception):
    def __init__(self, code, message, *, dispatched=False):
        super().__init__(message)
        self.code = code
        self.dispatched = dispatched


def require(condition, message, *, code="invalid_payload"):
    if not condition:
        raise Failure(code, message)


def text(value, name, maximum=65536, *, nonempty=False):
    require(isinstance(value, str), f"{name} must be text.")
    require(len(value.encode("utf-8")) <= maximum and "\x00" not in value, f"Invalid {name}.")
    require(not nonempty or bool(value.strip()), f"{name} must be nonempty.")
    return value


def identifier(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value),
        "Invalid graph identifier.",
    )
    return value


def integer(value, name, low, high):
    require(
        type(value) is int and low <= value <= high,
        f"{name} must be an integer from {low} to {high}.",
    )
    return value


def boolean(value, name):
    require(type(value) is bool, f"{name} must be boolean.")
    return value


def bounded_json(value, limit=256 * 1024):
    try:
        require(
            len(json.dumps(value, allow_nan=False).encode()) <= limit, "JSON exceeds byte limit."
        )
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise Failure("invalid_payload", "Bounded valid JSON is required.") from exc


def primitive(value):
    if value is None or type(value) is bool or isinstance(value, str):
        if isinstance(value, str):
            text(value, "property")
        return
    require(
        (type(value) is int and abs(value) <= 9007199254740991)
        or (type(value) is float and math.isfinite(value)),
        "Properties require finite safe numbers.",
    )


def properties(value, id_property=None):
    require(isinstance(value, dict), "Properties must be an object.")
    for key, child in value.items():
        identifier(key)
        require(key != id_property, "Properties cannot modify the stable ID.")
        if isinstance(child, list):
            require(len(child) <= 4096, "Property array is too long.")
            for entry in child:
                primitive(entry)
                require(entry is not None, "Property arrays cannot contain null.")
            kinds = {("number" if type(v) in (int, float) else type(v).__name__) for v in child}
            require(len(kinds) <= 1, "Property arrays must be homogeneous.")
        else:
            primitive(child)
    return value


def vector(value):
    require(
        isinstance(value, list) and 1 <= len(value) <= 4096, "Vector requires 1 to 4096 values."
    )
    require(
        all(
            (type(v) is int and abs(v) <= 9007199254740991)
            or (type(v) is float and math.isfinite(v))
            for v in value
        ),
        "Vector values must be finite safe numbers.",
    )
    return [float(v) for v in value]


def connection_values(connection):
    try:
        config, secrets = connection.get("config") or {}, connection.get("secrets") or {}
        require(
            isinstance(config, dict) and isinstance(secrets, dict), "Invalid connection objects."
        )
        require(
            set(config)
            <= {"host", "port", "tls", "database", "id_property", "request_timeout_seconds"},
            "Unknown connection setting.",
        )
        require(set(secrets) <= {"username", "password"}, "Unknown credential setting.")
        host = text(config.get("host"), "host", 253, nonempty=True)
        require(
            not any(c.isspace() or c in "/\\@?#[]$%" for c in host),
            "Use a host without URL components.",
        )
        if ":" in host:
            ipaddress.IPv6Address(host)
        database = text(config.get("database", "neo4j"), "database", 63, nonempty=True)
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", database), "Invalid database.")
        result = {
            "host": host,
            "port": integer(config.get("port", 7687), "port", 1, 65535),
            "tls": boolean(config.get("tls", True), "tls"),
            "database": database,
            "id_property": identifier(config.get("id_property", "id")),
            "request_timeout_seconds": integer(
                config.get("request_timeout_seconds", 15), "request_timeout_seconds", 1, 30
            ),
        }
        for key in ("username", "password"):
            text(secrets.get(key), key, 8192, nonempty=True)
        return result, secrets
    except (Failure, ValueError, TypeError, UnicodeError) as exc:
        raise Failure(
            "invalid_connection", "Invalid Neo4j connection configuration or credentials."
        ) from exc
