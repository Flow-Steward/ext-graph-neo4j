import json
from unittest.mock import patch
import pytest
import toolbox
from validation import Failure

CONFIG = {
    "host": "db.example.com",
    "port": 7687,
    "tls": True,
    "database": "neo4j",
    "request_timeout_seconds": 15,
}


def test_direct_uri_and_literal_password():
    source = json.loads(
        toolbox.managed_config(
            CONFIG,
            {"username": "$USER", "password": "p${TOKEN}"},
            {"type": "neo4j-cypher", "statement": "RETURN $x", "parameters": []},
            {},
        ).split("\n---\n")[0]
    )
    assert source["uri"] == "bolt+s://db.example.com:7687"
    assert source["user"] == "$USER" and source["password"] == "p${TOKEN}"
    assert "allowPrivate" in source
    assert "$" not in toolbox.managed_config(
        CONFIG, {"username": "u", "password": "$secret"}, {"type": "neo4j-cypher"}, {}
    )


def test_private_requires_admin_and_public_requires_tls():
    with (
        patch.object(toolbox, "resolve_pinned_ips", return_value=("127.0.0.1",)),
        patch.dict("os.environ", {}, clear=True),
    ):
        with pytest.raises(Failure):
            toolbox._endpoint(CONFIG)
    with (
        patch.object(toolbox, "resolve_pinned_ips", return_value=("8.8.8.8",)),
        patch.dict("os.environ", {}, clear=True),
    ):
        with pytest.raises(Failure):
            toolbox._endpoint({**CONFIG, "tls": False})


def test_sanitized_error_and_empty_array():
    with pytest.raises(Failure) as error:
        toolbox._result(
            {
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "authentication secret=password"}],
                }
            }
        )
    assert error.value.code == "authentication_failed"
    assert "password" not in str(error.value)
    assert toolbox._result({"result": {"content": [{"type": "text", "text": "[]"}]}}) == [[]]


@pytest.mark.parametrize("unexpected", [RecursionError, RuntimeError])
def test_unexpected_error_after_tools_call_preserves_dispatched(monkeypatch, unexpected):
    from unittest.mock import Mock

    process = Mock()
    monkeypatch.setattr(toolbox, "_binary", lambda: "/packaged/toolbox")
    monkeypatch.setattr(toolbox, "_endpoint", lambda config: ("127.0.0.1",))
    monkeypatch.setattr(toolbox.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(toolbox, "_send", lambda *a: None)
    responses = iter([{"result": {}}, unexpected("private diagnostic password=secret")])

    def read(*args):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(toolbox, "_read", read)
    stopped = []
    monkeypatch.setattr(toolbox, "_stop", lambda child: stopped.append(child))
    with pytest.raises(Failure) as error:
        toolbox.invoke(
            CONFIG,
            {"username": "u", "password": "p"},
            {
                "type": "neo4j-cypher",
                "statement": "CREATE (n:Doc)",
                "readOnly": False,
                "parameters": [],
            },
            {},
            timeout_seconds=1,
        )
    assert error.value.dispatched is True
    assert "secret" not in str(error.value)
    assert stopped == [process]


def child(code):
    import subprocess
    import sys

    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def test_stalled_stdin_obeys_deadline_and_process_is_reaped():
    import time

    process = child("import time; time.sleep(10)")
    started = time.monotonic()
    try:
        with pytest.raises(Failure, match="deadline"):
            toolbox._send(process, {"x": "z" * 200000}, started + 0.1)
    finally:
        toolbox._stop(process)
    assert time.monotonic() - started < 3
    assert process.poll() is not None
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


@pytest.mark.parametrize("fd", [1, 2])
def test_flooded_stream_obeys_shared_byte_cap(fd):
    import time

    process = child(f'import os; os.write({fd},b"x"*1100000)')
    try:
        with pytest.raises(Failure) as error:
            toolbox._read(process, 1, time.monotonic() + 3, {"bytes": 0, "buffer": bytearray()})
        assert error.value.code == "response_too_large"
    finally:
        toolbox._stop(process)
    assert process.poll() is not None


def test_stalled_stdout_obeys_read_deadline():
    import time

    process = child("import time; time.sleep(10)")
    started = time.monotonic()
    try:
        with pytest.raises(Failure, match="deadline"):
            toolbox._read(process, 1, started + 0.1, {"bytes": 0, "buffer": bytearray()})
    finally:
        toolbox._stop(process)
    assert time.monotonic() - started < 3
