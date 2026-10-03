"""Verdicts of the real browser-bridge 2.0.0 server, recorded for the tests.

``tests/bridge_oracle/server_oracle.ts`` imports ``bridge-server.ts`` and
``bridge-v2-contract.ts`` from a deep-research checkout and runs them under
Bun. This module builds the cases (every request row of
``test_bridge_policy_parity.py`` plus the envelope, token and schema-default
cases below), sends each request as the exact bytes httpx puts on the wire,
and records the server's full response in
``tests/fixtures/bridge_server_oracle.json``, together with the server commit
and file hashes it came from.

The default suite checks the client against that recording. The
``bridge_server`` tests re-run the server and require the recording to equal
what it returns now. Regenerate the recording with:

    python tests/bridge_server_oracle.py --server-repo PATH [--out FILE]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import test_bridge_policy_parity as parity  # noqa: E402

FIXTURE = TESTS_DIR / "fixtures" / "bridge_server_oracle.json"
ORACLE_SCRIPT = TESTS_DIR / "bridge_oracle" / "server_oracle.ts"
SCHEMA = "bridge-server-oracle/1"
SERVER_LIB = "packages/opencode-deep-research/lib"
SERVER_FILES = ("bridge-server.ts", "bridge-v2-contract.ts", "bridge-v2-policy.ts")
# The server answers a request that passes every policy check with one of
# these (the oracle's registry and readiness are fixed): the client sends it.
ACCEPTED_CODES = frozenset({"CAPABILITY_UNAVAILABLE", "SESSION_UNKNOWN"})
VALID_TOKEN = "OracleToken_0123456789abcdefghijklmnopqrstuvwxyz"
UPPER_REQUEST_ID = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
NIL_REQUEST_ID = "00000000-0000-0000-0000-000000000000"


def _envelope_row(rule: str, **overrides) -> tuple[str, dict]:
    return rule, parity._payload(**overrides)


def _oversized(**overrides) -> dict:
    payload = parity._payload(**overrides)
    payload["session_name"] = "a" * (parity._wire_size(parity._payload()) + 16384)
    return payload


# Request cases beyond the parity table: the request_id the server echoes in
# each kind of error envelope, and payloads httpx cannot encode (their rows
# carry the bytes httpx would have written).
ENVELOPE_ROWS: list[tuple[str, dict]] = [
    _envelope_row("id-echoed-on-legacy-version", contract_version="1.0.0"),
    _envelope_row("id-echoed-on-unsupported-version", contract_version="3.0.0"),
    _envelope_row("id-null-when-invalid-on-legacy-version", contract_version="1.0.0", request_id="not-a-uuid"),
    _envelope_row("id-null-when-not-a-string", request_id=12345),
    _envelope_row("id-null-when-trailing-newline", request_id=f"{parity.REQUEST_ID}\n"),
    _envelope_row("id-echoed-on-schema-error", session_name="Campus"),
    _envelope_row("id-echoed-on-bad-timeout", timeout_ms=999),
    _envelope_row("id-echoed-on-doi-whitespace", doi=" 10.1000/example"),
    _envelope_row("id-echoed-on-scheme-blocked", candidate_url="http://publisher.example/paper.pdf"),
    _envelope_row("id-echoed-on-domain-blocked", candidate_url="https://other.example/paper.pdf"),
    _envelope_row(
        "id-uppercase-echoed-as-sent",
        request_id=UPPER_REQUEST_ID,
        candidate_url="https://other.example/paper.pdf",
    ),
    _envelope_row("id-nil-uuid-echoed", request_id=NIL_REQUEST_ID, candidate_url="https://127.0.0.1/paper.pdf"),
    _envelope_row("id-echoed-on-accept", request_id=UPPER_REQUEST_ID),
    ("id-null-when-too-large", _oversized()),
    _envelope_row("wire-lone-surrogate-doi", doi="10.1000/\ud800"),
    _envelope_row("wire-lone-surrogate-before-version", doi="10.1000/\udfff", contract_version="1.0.0"),
    _envelope_row("wire-nan-timeout", timeout_ms=float("nan")),
    _envelope_row("wire-infinity-timeout", timeout_ms=float("inf")),
    _envelope_row("wire-negative-infinity-timeout", timeout_ms=float("-inf")),
    ("wire-lone-surrogate-too-large", _oversized(doi="10.1000/\ud800")),
]

# (id, configured token) for validateBridgeToken. None of them is a secret.
TOKEN_CASES: list[tuple[str, str]] = [
    ("exact", VALID_TOKEN),
    ("min-32", "a" * 32),
    ("max-256", "b" * 256),
    ("too-short-31", "a" * 31),
    ("too-long-257", "b" * 257),
    ("empty", ""),
    ("only-spaces", "    "),
    ("leading-space", f" {VALID_TOKEN}"),
    ("trailing-space", f"{VALID_TOKEN} "),
    ("surrounding-tabs", f"\t{VALID_TOKEN}\t"),
    ("vertical-tab-form-feed", f"\v\f{VALID_TOKEN}\f\v"),
    ("nbsp", f" {VALID_TOKEN} "),
    ("bom", f"﻿{VALID_TOKEN}"),
    ("ideographic-space", f"{VALID_TOKEN}　"),
    ("line-separator", f"{VALID_TOKEN} "),
    ("trim-to-31", f"  {'a' * 31}  "),
    ("trim-to-256", f" {'b' * 256} "),
    ("trailing-lf", f"{VALID_TOKEN}\n"),
    ("trailing-crlf", f"{VALID_TOKEN}\r\n"),
    ("leading-cr", f"\r{VALID_TOKEN}"),
    ("inner-space", f"{VALID_TOKEN[:20]} {VALID_TOKEN[20:]}"),
    ("inner-tab", f"{VALID_TOKEN[:20]}\t{VALID_TOKEN[20:]}"),
    ("next-line-not-trimmed", f"{VALID_TOKEN}\u0085"),
    ("unit-separator-not-trimmed", f"{VALID_TOKEN}\u001f"),
    ("zero-width-space-not-trimmed", f"{VALID_TOKEN}​"),
    ("dot", f"{VALID_TOKEN}."),
    ("non-ascii-letter", f"{VALID_TOKEN}é"),
]


def _without(payload: dict, *keys: str) -> dict:
    return {key: value for key, value in payload.items() if key not in keys}


# bridgeDownloadRequestSchema defaults: the parsed form of the client's
# request and of the same request without the defaulted keys.
SCHEMA_CASES: list[tuple[str, dict]] = [
    ("client-request", parity._payload()),
    ("without-expected-artifact-and-timeout", _without(parity._payload(), "expected_artifact", "timeout_ms")),
]


def request_rows() -> list[tuple[str, dict]]:
    rows = [(row.id, row.values[0]) for row in parity.REQUEST_ROWS]
    rows.extend(ENVELOPE_ROWS)
    ids = [row_id for row_id, _ in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate request row id")
    return rows


def wire_bytes(payload: dict) -> bytes:
    """The body httpx writes for ``json=payload``.

    httpx refuses a lone surrogate or a non-finite float; for those the bytes
    are what the same encoding produces without the refusal.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeEncodeError):
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=True)
        return text.encode("utf-8", "surrogatepass")


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def run_oracle(server_repo: Path, cases: list[dict]) -> list:
    completed = subprocess.run(
        ["bun", str(ORACLE_SCRIPT), str(server_repo / SERVER_LIB)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return json.loads(completed.stdout)


def server_provenance(server_repo: Path) -> dict:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=server_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    files = {name: sha256((server_repo / SERVER_LIB / name).read_bytes()) for name in SERVER_FILES}
    return {"repo_head": head, "lib": SERVER_LIB, "file_sha256": files}


def build_fixture(server_repo: Path) -> dict:
    rows = request_rows()
    cases: list[dict] = [
        {"kind": "request", "raw_b64": base64.b64encode(wire_bytes(payload)).decode("ascii")} for _, payload in rows
    ]
    cases += [{"kind": "token", "raw": raw} for _, raw in TOKEN_CASES]
    cases += [{"kind": "request_schema", "body": body} for _, body in SCHEMA_CASES]
    verdicts = run_oracle(server_repo, cases)
    if len(verdicts) != len(cases):
        raise RuntimeError("oracle returned the wrong number of verdicts")
    request_verdicts = verdicts[: len(rows)]
    token_verdicts = verdicts[len(rows) : len(rows) + len(TOKEN_CASES)]
    schema_verdicts = verdicts[len(rows) + len(TOKEN_CASES) :]
    bun_version = subprocess.run(["bun", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    return {
        "schema": SCHEMA,
        "server": {**server_provenance(server_repo), "runtime": f"bun {bun_version}"},
        "requests": {
            row_id: {"wire_sha256": sha256(wire_bytes(payload)), **verdict}
            for (row_id, payload), verdict in zip(rows, request_verdicts)
        },
        "tokens": {
            case_id: {"raw_sha256": sha256(raw), "server": verdict}
            for (case_id, raw), verdict in zip(TOKEN_CASES, token_verdicts)
        },
        "request_schema": {case_id: verdict for (case_id, _), verdict in zip(SCHEMA_CASES, schema_verdicts)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=FIXTURE)
    args = parser.parse_args(argv)
    fixture = build_fixture(args.server_repo.resolve())
    args.out.write_text(json.dumps(fixture, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(fixture['requests'])} requests, {len(fixture['tokens'])} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
