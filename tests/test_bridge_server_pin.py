import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bridge_server_pin.py"
_spec = importlib.util.spec_from_file_location("bridge_server_pin", _SCRIPT)
pin_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin_tool)

SERVER_SOURCE = b"export function startBridgeServer() {}\n"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _client_source(version="2.0.0"):
    return f'BRIDGE_CONTRACT_VERSION = "{version}"\nBRIDGE_SERVICE = "pkb-browser-bridge"\n'


def _server_contract(version="2.0.0"):
    return f'export const BRIDGE_CONTRACT_VERSION = "{version}" as const\n'


@pytest.fixture
def repos(tmp_path, monkeypatch):
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[user]\n\tname = Pin Test\n\temail = pin@example.invalid\n[commit]\n\tgpgsign = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    client = tmp_path / "client"
    server = tmp_path / "server"
    for repo in (client, server):
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")

    contract = client / pin_tool.CLIENT_CONTRACT_FILE
    contract.parent.mkdir(parents=True)
    contract.write_text(_client_source())
    _commit_all(client, "client")

    server_file = server / pin_tool.SERVER_FILE
    server_file.parent.mkdir(parents=True)
    server_file.write_bytes(SERVER_SOURCE)
    (server / pin_tool.SERVER_CONTRACT_FILE).write_text(_server_contract())
    _commit_all(server, "feat: bridge server")
    (server / "README.md").write_text("later unrelated commit\n")
    _commit_all(server, "docs: readme")
    return client, server


def test_pin_records_exact_client_and_server_provenance(repos):
    client, server = repos

    pin = pin_tool.build_pin(client, server, stage="z1-hash")

    assert pin["schema"] == "zotero-server-pin/2"
    assert pin["source_commit"] == _git(client, "rev-parse", "HEAD")
    assert pin["source_tree_hash"] == _git(client, "rev-parse", "HEAD^{tree}")
    assert pin["source_worktree_clean"] is True
    assert pin["client"]["contract_version_targeted"] == "2.0.0"
    assert pin["client"]["service_targeted"] == "pkb-browser-bridge"
    assert pin["server"]["owner_repo"] == str(server.resolve())
    assert pin["server"]["repo_head"] == _git(server, "rev-parse", "HEAD")
    # The last commit touching the server file, not the repository head.
    assert pin["server"]["file_last_commit"] == _git(server, "rev-parse", "HEAD~1")
    assert pin["server"]["file_last_commit"] != pin["server"]["repo_head"]
    assert pin["server"]["file_sha256"] == hashlib.sha256(SERVER_SOURCE).hexdigest()
    assert pin["server"]["file_size_bytes"] == len(SERVER_SOURCE)
    assert pin["server"]["server_contract_version"] == "2.0.0"
    assert pin["contract_version_match"] is True
    assert pin_tool.validate_pin(pin, client, server) == []


def test_pin_survives_json_round_trip_and_sidecar(repos, tmp_path):
    client, server = repos
    out = tmp_path / "pin.json"

    assert pin_tool.main(["write", "--client-repo", str(client), "--server-repo", str(server), "--out", str(out)]) == 0

    data = out.read_bytes()
    assert Path(f"{out}.sha256").read_text().split()[0] == hashlib.sha256(data).hexdigest()
    assert pin_tool.validate_pin(json.loads(data), client, server) == []
    assert pin_tool.main(["check", "--client-repo", str(client), "--server-repo", str(server), "--pin", str(out)]) == 0


@pytest.mark.parametrize(
    ("path", "value", "problem"),
    [
        (("source_commit",), "0" * 40, "source_commit does not match"),
        (("source_tree_hash",), "0" * 40, "source_tree_hash does not match"),
        (("server", "repo_head"), "0" * 40, "server.repo_head does not match"),
        (("server", "file_last_commit"), "0" * 40, "server.file_last_commit does not match"),
        (("server", "file_sha256"), "0" * 64, "server.file_sha256 does not match"),
        (("server", "server_contract_version"), "9.9.9", "server.server_contract_version does not match"),
        (("client", "contract_version_targeted"), "9.9.9", "client.contract_version_targeted does not match"),
        (("source_commit",), "b354f2a", "source_commit must be a full git object id"),
        (("schema",), "zotero-server-pin/1", "schema must be"),
    ],
)
def test_validate_rejects_tampered_fields(repos, path, value, problem):
    client, server = repos
    pin = pin_tool.build_pin(client, server)
    target = pin
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    problems = pin_tool.validate_pin(pin, client, server)

    assert any(problem in item for item in problems), problems


@pytest.mark.parametrize(
    "path",
    [
        ("source_commit",),
        ("source_tree_hash",),
        ("server", "file_last_commit"),
        ("client", "contract_version_targeted"),
    ],
)
def test_validate_rejects_missing_required_keys(repos, path):
    client, server = repos
    pin = pin_tool.build_pin(client, server)
    target = pin
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]

    assert f"missing {'.'.join(path)}" in pin_tool.validate_pin(pin, client, server)


def test_validate_rejects_pin_for_superseded_client_commit(repos):
    client, server = repos
    pin = pin_tool.build_pin(client, server)
    (client / "NEW.md").write_text("new commit\n")
    _commit_all(client, "next")

    problems = pin_tool.validate_pin(pin, client, server)

    assert any("source_commit does not match" in item for item in problems)
    assert any("source_tree_hash does not match" in item for item in problems)


def test_validate_rejects_dirty_client_or_server_file(repos):
    client, server = repos
    (client / pin_tool.CLIENT_CONTRACT_FILE).write_text(_client_source() + "# edit\n")
    (server / pin_tool.SERVER_FILE).write_bytes(SERVER_SOURCE + b"// uncommitted\n")

    pin = pin_tool.build_pin(client, server)
    problems = pin_tool.validate_pin(pin, client, server)

    assert pin["source_worktree_clean"] is False
    assert pin["server"]["file_worktree_clean"] is False
    assert "source_worktree_clean must be true" in problems
    assert "server.file_worktree_clean must be true" in problems
    assert "server.file_sha256_matches_head_blob must be true" in problems


def test_validate_rejects_contract_version_mismatch(repos, tmp_path):
    client, server = repos
    (server / pin_tool.SERVER_CONTRACT_FILE).write_text(_server_contract("3.0.0"))
    _commit_all(server, "feat: bump contract")

    pin = pin_tool.build_pin(client, server)

    assert pin["contract_version_match"] is False
    assert "contract_version_match must be true" in pin_tool.validate_pin(pin, client, server)
    out = tmp_path / "pin.json"
    assert pin_tool.main(["write", "--client-repo", str(client), "--server-repo", str(server), "--out", str(out)]) == 1
    assert not out.exists()
