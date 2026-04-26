from __future__ import annotations

import os
from pathlib import Path


def get_config_home() -> Path:
    override = os.environ.get("ZOTERO_MCP_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()

    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        return Path(xdg_home).expanduser().resolve()

    return (Path.home() / ".config").resolve()


def get_config_dir() -> Path:
    override = os.environ.get("ZOTERO_MCP_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (get_config_home() / "zotero-mcp").resolve()


def get_config_path() -> Path:
    override = os.environ.get("ZOTERO_MCP_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return (get_config_dir() / "config.json").resolve()


def get_chroma_db_path() -> Path:
    override = os.environ.get("ZOTERO_MCP_CHROMA_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return (get_config_dir() / "chroma_db").resolve()
