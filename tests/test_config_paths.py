from pathlib import Path

from zotero_mcp.config_paths import (
    get_chroma_db_path,
    get_config_dir,
    get_config_home,
    get_config_path,
)


def test_config_paths_default_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ZOTERO_MCP_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZOTERO_MCP_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ZOTERO_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ZOTERO_MCP_CHROMA_DB_PATH", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert get_config_home() == (tmp_path / ".config").resolve()
    assert get_config_dir() == (tmp_path / ".config" / "zotero-mcp").resolve()
    assert get_config_path() == (tmp_path / ".config" / "zotero-mcp" / "config.json").resolve()
    assert get_chroma_db_path() == (tmp_path / ".config" / "zotero-mcp" / "chroma_db").resolve()


def test_config_paths_respect_explicit_overrides(monkeypatch, tmp_path):
    config_home = tmp_path / "cfg-home"
    config_dir = tmp_path / "cfg-dir"
    config_path = tmp_path / "cfg-dir" / "custom.json"
    chroma_db_path = tmp_path / "state" / "chroma"

    monkeypatch.setenv("ZOTERO_MCP_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("ZOTERO_MCP_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ZOTERO_MCP_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ZOTERO_MCP_CHROMA_DB_PATH", str(chroma_db_path))

    assert get_config_home() == config_home.resolve()
    assert get_config_dir() == config_dir.resolve()
    assert get_config_path() == config_path.resolve()
    assert get_chroma_db_path() == chroma_db_path.resolve()
