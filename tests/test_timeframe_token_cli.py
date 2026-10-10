"""The batch transport ``python -m pineforge_codegen.timeframe_token_cli``.

The in-process cases call ``main()`` with its streams replaced and count the calls
to the shared ``canonical_wire_timeframe``; the subprocess cases check the exit
status and the bytes on stdout. No case reads a source, a strategy or a feed.
"""

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pineforge_codegen import request_feed_inventory, timeframe_token_cli

REPO_ROOT = Path(__file__).resolve().parents[1]
MALFORMED_TEXT = "malformed timeframe token request\n"
INTERNAL_TEXT = "internal failure in the timeframe token transport\n"


def _result(tokens: list) -> bytes:
    text = json.dumps({"version": 1, "tokens": tokens}, separators=(",", ":"))
    return (text + "\n").encode("ascii")


def _request(values: list) -> bytes:
    return json.dumps({"version": 1, "values": values}).encode("utf-8")


def _main(monkeypatch, raw: bytes):
    """``main()`` on ``raw`` with stdin, stdout and stderr replaced: returns
    (exit status, stdout bytes, stderr text)."""
    stdout = SimpleNamespace(buffer=io.BytesIO())
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    code = timeframe_token_cli.main()
    return code, stdout.buffer.getvalue(), stderr.getvalue()


def _spy(monkeypatch) -> list:
    """Route the module's ``canonical_wire_timeframe`` through a recorder of
    its arguments that calls the shared function; returns the record."""
    calls: list = []
    shared = timeframe_token_cli.canonical_wire_timeframe

    def recorder(text):
        calls.append(text)
        return shared(text)

    monkeypatch.setattr(timeframe_token_cli, "canonical_wire_timeframe", recorder)
    return calls


def _subprocess(raw: bytes):
    return subprocess.run(
        [sys.executable, "-m", "pineforge_codegen.timeframe_token_cli"],
        input=raw, capture_output=True, cwd=REPO_ROOT, check=False)


def test_the_batch_uses_the_shared_function_itself():
    assert (timeframe_token_cli.canonical_wire_timeframe
            is request_feed_inventory.canonical_wire_timeframe)


def test_a_result_is_one_compact_line(monkeypatch):
    code, out, err = _main(monkeypatch, b'{"version":1,"values":["240","1D",null]}')
    assert (code, err) == (0, "")
    assert out == b'{"version":1,"tokens":["240","D",null]}\n'


@pytest.mark.parametrize("value, token", [
    ("1", "1"), ("240", "240"), ("D", "D"), ("1D", "D"), ("2D", "2D"),
    ("W", "W"), ("1W", "W"), ("2W", "2W"), ("M", "1M"), ("1M", "1M"),
    ("12M", "12M"), ("S", "1S"), ("1S", "1S"), ("30S", "30S"),
])
def test_aliases_and_spellings_give_the_canonical_token(monkeypatch, value, token):
    code, out, err = _main(monkeypatch, _request([value]))
    assert (code, err) == (0, "")
    assert out == _result([token])


@pytest.mark.parametrize("value", [
    "", " 5", "5 ", "05", "0", "00", "0D", "-5", "1.5", "5.0", "1H", "4H", "H",
    "d", "2w", "60m", "D1", "1DD", "１５", "5\n",
    15, 1.5, True, False, None, [], {}, ["D"], {"timeframe": "D"},
])
def test_a_string_that_is_no_token_and_any_nonstring_give_null(monkeypatch, value):
    code, out, err = _main(monkeypatch, _request([value]))
    assert (code, err) == (0, "")
    assert out == _result([None])


def test_a_long_known_token_comes_back_unchanged(monkeypatch):
    token = "1" + "0" * 5000 + "D"
    code, out, err = _main(monkeypatch, _request([token]))
    assert (code, err) == (0, "")
    assert out == _result([token])


def test_an_empty_batch_gives_an_empty_list(monkeypatch):
    code, out, err = _main(monkeypatch, b'{"version":1,"values":[]}')
    assert (code, err) == (0, "")
    assert out == b'{"version":1,"tokens":[]}\n'


def test_order_and_cardinality_are_kept_with_no_deduplication(monkeypatch):
    values = ["240", "D", "bogus", "240", None, 5, "W", "D"]
    code, out, _ = _main(monkeypatch, _request(values))
    assert code == 0
    assert out == _result(["240", "D", None, "240", None, None, "W", "D"])


def test_each_value_gets_one_direct_call_in_order(monkeypatch):
    calls = _spy(monkeypatch)
    values = ["D", "240", "bogus", "D", None, ""]
    code, out, _ = _main(monkeypatch, _request(values))
    assert code == 0
    assert calls == values
    assert out == _result(["D", "240", None, "D", None, None])


def test_a_number_and_a_bool_are_one_call_each(monkeypatch):
    calls = _spy(monkeypatch)
    code, out, _ = _main(monkeypatch, b'{"version":1,"values":[5,true,"D"]}')
    assert code == 0
    assert len(calls) == 3
    assert out == _result([None, None, "D"])


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":1,"values":[]}',
    b'{"version":1,"values":[],"values":[]}',
    b'{"version":1,"values":[{"a":1,"a":2}]}',
    b'{"version":1,"values":[],"extra":0}',
    b'{"version":1}',
    b'{"values":[]}',
    b'{"version":1,"values":"D"}',
    b'{"version":1,"values":{}}',
    b'{"version":1,"values":null}',
    b"[]",
    b'"text"',
    b"7",
    b"null",
])
def test_a_repeated_extra_or_missing_key_or_a_non_object_exits_2(monkeypatch, raw):
    assert _main(monkeypatch, raw) == (2, b"", MALFORMED_TEXT)


@pytest.mark.parametrize("version", [
    b"true", b"false", b"null", b"1.0", b"1e0", b"1E0", b'"1"', b"[1]", b"{}",
    b"0", b"2", b"-1",
])
def test_a_version_that_is_not_the_integer_one_exits_2(monkeypatch, version):
    raw = b'{"version":' + version + b',"values":[]}'
    assert _main(monkeypatch, raw) == (2, b"", MALFORMED_TEXT)


@pytest.mark.parametrize("raw", [
    b'{"version":1,"values":["\xff"]}',
    b'{"version":1,"values":["\xed\xa0\x80"]}',
    b"\xef\xbb\xbf" + b'{"version":1,"values":[]}',
    b'{"version":1,"values":[',
    b"",
    b'{"version":1,"values":[]} x',
    b'{"version":1,"values":[NaN]}',
    b'{"version":1,"values":[-Infinity]}',
    b'{"version":1,"values":[1,]}',
    b'{"version":1,"values":["a\x01b"]}',
])
def test_bytes_that_are_no_utf8_or_no_strict_json_exit_2(monkeypatch, raw):
    assert _main(monkeypatch, raw) == (2, b"", MALFORMED_TEXT)


def test_a_document_nested_past_the_parser_limit_exits_2(monkeypatch):
    assert _main(monkeypatch, b"[" * 100000) == (2, b"", MALFORMED_TEXT)


def test_an_unexpected_failure_exits_1_with_no_stdout(monkeypatch):
    def broken(text):
        raise RuntimeError("broken on purpose")

    monkeypatch.setattr(timeframe_token_cli, "canonical_wire_timeframe", broken)
    assert _main(monkeypatch, _request(["D"])) == (1, b"", INTERNAL_TEXT)


def test_subprocess_exits_0_with_the_result_line():
    proc = _subprocess(b'{"version":1,"values":["240","1D",null]}')
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        0, b'{"version":1,"tokens":["240","D",null]}\n', b"")


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":1,"values":[]}',
    b"\xff",
    b'{"version":true,"values":[]}',
])
def test_subprocess_refuses_a_malformed_request_with_exit_2_and_no_stdout(raw):
    proc = _subprocess(raw)
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        2, b"", MALFORMED_TEXT.encode("ascii"))
