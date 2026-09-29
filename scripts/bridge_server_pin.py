"""Pin the browser-bridge server this client targets, and re-check a pin.

The bridge server lives in a separate repository, so the client records a
provenance receipt tying its own commit and tree to the exact server commit,
file hashes and contract version it was verified against. Every value is
derived from read-only git commands and file reads; nothing is written to
either repository.

    python scripts/bridge_server_pin.py write --server-repo PATH --out FILE
    python scripts/bridge_server_pin.py check --server-repo PATH --pin FILE
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "zotero-server-pin/2"
CLIENT_CONTRACT_FILE = "src/zotero_mcp/acquisition/bridge_client.py"
SERVER_FILE = "packages/opencode-deep-research/lib/bridge-server.ts"
SERVER_CONTRACT_FILE = "packages/opencode-deep-research/lib/bridge-v2-contract.ts"
_CLIENT_VERSION_RE = re.compile(r'^BRIDGE_CONTRACT_VERSION = "([^"]+)"$', re.MULTILINE)
_CLIENT_SERVICE_RE = re.compile(r'^BRIDGE_SERVICE = "([^"]+)"$', re.MULTILINE)
_SERVER_VERSION_RE = re.compile(r'^export const BRIDGE_CONTRACT_VERSION = "([^"]+)"', re.MULTILINE)
_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# Fields that must be present with these types; values are re-derived by check.
REQUIRED = {
    "schema": str,
    "source_commit": str,
    "source_tree_hash": str,
    "source_worktree_clean": bool,
    "client": dict,
    "server": dict,
    "contract_version_match": bool,
}
REQUIRED_CLIENT = {
    "branch": str,
    "contract_version_targeted": str,
    "contract_constant": str,
    "service_targeted": str,
}
REQUIRED_SERVER = {
    "owner_repo": str,
    "repo_head": str,
    "file": str,
    "file_worktree_clean": bool,
    "file_last_commit": str,
    "file_sha256": str,
    "file_git_blob": str,
    "file_sha256_matches_head_blob": bool,
    "file_size_bytes": int,
    "contract_file": str,
    "contract_file_sha256": str,
    "contract_file_last_commit": str,
    "server_contract_version": str,
}
# Informational fields that are not pinned identity (they may legitimately
# change without invalidating the pin).
_UNPINNED = {"generated_at", "stage", "notes"}
_UNPINNED_SERVER = {"file_last_commit_on_remote_branches", "repo_head_on_remote_branches"}


class PinError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PinError(f"git {' '.join(args)} failed in {repo}") from exc
    return completed.stdout.strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PinError(f"git {' '.join(args)} failed in {repo}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _match(pattern: re.Pattern[str], text: str, what: str) -> str:
    found = pattern.search(text)
    if not found:
        raise PinError(f"cannot find {what}")
    return found.group(1)


def _remote_branches_containing(repo: Path, commit: str) -> list[str]:
    output = _git(repo, "branch", "-r", "--contains", commit)
    return sorted(line.strip() for line in output.splitlines() if line.strip() and "->" not in line)


def build_pin(client_repo: Path, server_repo: Path, stage: str | None = None, notes: dict | None = None) -> dict:
    client_repo = Path(client_repo).resolve()
    server_repo = Path(server_repo).resolve()

    source_commit = _git(client_repo, "rev-parse", "HEAD")
    source_tree_hash = _git(client_repo, "rev-parse", "HEAD^{tree}")
    client_worktree_clean = _git(client_repo, "status", "--porcelain", "--untracked-files=no") == ""
    # Read the contract constant from the pinned commit, not the worktree.
    client_source = _git_bytes(client_repo, "show", f"{source_commit}:{CLIENT_CONTRACT_FILE}").decode()
    client_version = _match(_CLIENT_VERSION_RE, client_source, "client BRIDGE_CONTRACT_VERSION")
    client_service = _match(_CLIENT_SERVICE_RE, client_source, "client BRIDGE_SERVICE")

    repo_head = _git(server_repo, "rev-parse", "HEAD")
    server_bytes = (server_repo / SERVER_FILE).read_bytes()
    server_head_bytes = _git_bytes(server_repo, "show", f"{repo_head}:{SERVER_FILE}")
    contract_bytes = (server_repo / SERVER_CONTRACT_FILE).read_bytes()
    contract_head_bytes = _git_bytes(server_repo, "show", f"{repo_head}:{SERVER_CONTRACT_FILE}")
    file_last_commit = _git(server_repo, "log", "-1", "--format=%H", repo_head, "--", SERVER_FILE)
    contract_last_commit = _git(server_repo, "log", "-1", "--format=%H", repo_head, "--", SERVER_CONTRACT_FILE)
    if not file_last_commit or not contract_last_commit:
        raise PinError("server files have no commit history")
    server_version = _match(_SERVER_VERSION_RE, contract_head_bytes.decode(), "server BRIDGE_CONTRACT_VERSION")

    pin = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": source_commit,
        "source_tree_hash": source_tree_hash,
        "source_worktree_clean": client_worktree_clean,
        "client": {
            "repo_worktree": str(client_repo),
            "branch": _git(client_repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "contract_version_targeted": client_version,
            "contract_constant": f"{CLIENT_CONTRACT_FILE} BRIDGE_CONTRACT_VERSION",
            "service_targeted": client_service,
        },
        "server": {
            "owner_repo": str(server_repo),
            "checked_out_branch": _git(server_repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "repo_head": repo_head,
            "repo_head_on_remote_branches": _remote_branches_containing(server_repo, repo_head),
            "file": SERVER_FILE,
            "file_worktree_clean": server_bytes == server_head_bytes,
            "file_last_commit": file_last_commit,
            "file_last_commit_date": _git(server_repo, "log", "-1", "--format=%cI", file_last_commit),
            "file_last_commit_subject": _git(server_repo, "log", "-1", "--format=%s", file_last_commit),
            "file_last_commit_on_remote_branches": _remote_branches_containing(server_repo, file_last_commit),
            "file_sha256": _sha256(server_bytes),
            "file_git_blob": _git(server_repo, "rev-parse", f"{repo_head}:{SERVER_FILE}"),
            "file_sha256_matches_head_blob": _sha256(server_bytes) == _sha256(server_head_bytes),
            "file_size_bytes": len(server_bytes),
            "contract_file": SERVER_CONTRACT_FILE,
            "contract_file_sha256": _sha256(contract_bytes),
            "contract_file_worktree_clean": contract_bytes == contract_head_bytes,
            "contract_file_last_commit": contract_last_commit,
            "server_contract_version": server_version,
        },
        "contract_version_match": client_version == server_version,
        "access": "read-only git and file reads; no files, refs or config in either repository were modified",
    }
    if stage is not None:
        pin["stage"] = stage
    if notes:
        pin["notes"] = dict(notes)
    return pin


def _check_types(section: dict, required: dict, prefix: str) -> list[str]:
    problems = []
    for key, expected in required.items():
        if key not in section:
            problems.append(f"missing {prefix}{key}")
        elif type(section[key]) is not expected:
            problems.append(f"{prefix}{key} must be {expected.__name__}")
    return problems


def validate_pin(pin: object, client_repo: Path, server_repo: Path) -> list[str]:
    """Return every way ``pin`` fails to match the two repositories (empty if valid)."""
    if not isinstance(pin, dict):
        return ["pin must be a JSON object"]
    problems = _check_types(pin, REQUIRED, "")
    if isinstance(pin.get("client"), dict):
        problems += _check_types(pin["client"], REQUIRED_CLIENT, "client.")
    if isinstance(pin.get("server"), dict):
        problems += _check_types(pin["server"], REQUIRED_SERVER, "server.")
    if problems:
        return problems
    if pin["schema"] != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    for key in ("source_commit", "source_tree_hash"):
        if not _SHA1_RE.fullmatch(pin[key]):
            problems.append(f"{key} must be a full git object id")
    for key in ("repo_head", "file_last_commit", "file_git_blob", "contract_file_last_commit"):
        if not _SHA1_RE.fullmatch(pin["server"][key]):
            problems.append(f"server.{key} must be a full git object id")
    for key in ("file_sha256", "contract_file_sha256"):
        if not _SHA256_RE.fullmatch(pin["server"][key]):
            problems.append(f"server.{key} must be a lowercase sha256")
    for key in ("source_worktree_clean", "contract_version_match"):
        if pin[key] is not True:
            problems.append(f"{key} must be true")
    for key in ("file_worktree_clean", "file_sha256_matches_head_blob"):
        if pin["server"][key] is not True:
            problems.append(f"server.{key} must be true")

    try:
        current = build_pin(client_repo, server_repo)
    except (OSError, PinError) as exc:
        return [*problems, f"cannot re-derive pin: {exc}"]
    for key, value in current.items():
        if key in _UNPINNED or isinstance(value, dict):
            continue
        if pin.get(key) != value:
            problems.append(f"{key} does not match the repository ({pin.get(key)!r} != {value!r})")
    for section in ("client", "server"):
        for key, value in current[section].items():
            if section == "server" and key in _UNPINNED_SERVER:
                continue
            if pin[section].get(key) != value:
                problems.append(
                    f"{section}.{key} does not match the repository ({pin[section].get(key)!r} != {value!r})"
                )
    return problems


def _write(pin: dict, out: Path) -> None:
    data = (json.dumps(pin, indent=2, sort_keys=False) + "\n").encode()
    out.write_bytes(data)
    Path(f"{out}.sha256").write_text(f"{_sha256(data)}  {out.name}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("write", "check"):
        command = sub.add_parser(name)
        command.add_argument("--client-repo", type=Path, default=Path(__file__).resolve().parent.parent)
        command.add_argument("--server-repo", type=Path, required=True)
    sub.choices["write"].add_argument("--out", type=Path, required=True)
    sub.choices["write"].add_argument("--stage")
    sub.choices["write"].add_argument("--note", action="append", default=[], metavar="KEY=VALUE")
    sub.choices["check"].add_argument("--pin", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "write":
        notes = dict(note.split("=", 1) for note in args.note)
        try:
            pin = build_pin(args.client_repo, args.server_repo, stage=args.stage, notes=notes)
        except (OSError, PinError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        problems = validate_pin(pin, args.client_repo, args.server_repo)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        _write(pin, args.out)
        return 0

    try:
        pin = json.loads(args.pin.read_text())
    except (OSError, ValueError) as exc:
        print(f"error: cannot read pin: {exc}", file=sys.stderr)
        return 2
    problems = validate_pin(pin, args.client_repo, args.server_repo)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"pin ok: source_commit={pin['source_commit']} server repo_head={pin['server']['repo_head']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
