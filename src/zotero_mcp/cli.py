"""
Command-line interface for Zotero MCP server.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from zotero_mcp.config_paths import get_chroma_db_path, get_config_home, get_config_path

ZOTERO_SOURCE_ENV = (
    "ZOTERO_LOCAL",
    "ZOTERO_LOCAL_PORT",
    "ZOTERO_LIBRARY_ID",
    "ZOTERO_LIBRARY_TYPE",
    "ZOTERO_API_KEY",
)

# NOTE: Do NOT import zotero_mcp.server at module level.
# That triggers heavy imports (FastMCP, ChromaDB, sentence-transformers, torch)
# which take several seconds. Import lazily only when needed (serve command).
# This allows CLI commands like update-db to print "Starting up..." instantly.


def obfuscate_sensitive_value(value, keep_chars=4):
    """Obfuscate sensitive values by showing only the first few characters."""
    if not value or not isinstance(value, str):
        return value
    if len(value) <= keep_chars:
        return "*" * len(value)
    return value[:keep_chars] + "*" * (len(value) - keep_chars)


def obfuscate_config_for_display(config):
    """Create a copy of config with sensitive values obfuscated."""
    if not isinstance(config, dict):
        return config

    obfuscated = config.copy()
    sensitive_keys = ["ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID", "API_KEY", "LIBRARY_ID"]

    for key in sensitive_keys:
        if key in obfuscated:
            obfuscated[key] = obfuscate_sensitive_value(obfuscated[key])

    return obfuscated


def load_claude_desktop_env_vars():
    """Load Zotero environment variables from Claude Desktop config unless globally disabled."""
    # Global guard to skip Claude detection entirely
    if str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes"):
        return {}
    from zotero_mcp.setup_helper import find_claude_config

    try:
        config_path = find_claude_config()
        if not config_path or not config_path.exists():
            return {}

        with open(config_path) as f:
            config = json.load(f)

        # Extract Zotero MCP server environment variables
        mcp_servers = config.get("mcpServers", {})
        zotero_config = mcp_servers.get("zotero", {})
        env_vars = zotero_config.get("env", {})

        return env_vars

    except Exception:
        return {}


def load_standalone_env_vars(config_path: Path | None = None):
    """Load environment variables from standalone config (~/.config/zotero-mcp/config.json)."""
    try:
        cfg_path = config_path or get_config_path()
        if not cfg_path.exists():
            return {}
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get("client_env", {}) or {}
    except Exception:
        return {}


def apply_environment_variables(env_vars):
    """Apply environment variables to current process."""
    for key, value in env_vars.items():
        if key not in os.environ:  # Don't override existing env vars
            os.environ[key] = str(value)


def _save_zotero_db_path_to_config(config_path: Path, db_path: str) -> None:
    """
    Save the Zotero database path to the configuration file.

    This allows users to specify --db-path once and have it remembered
    for subsequent runs without needing to specify it again.

    Args:
        config_path: Path to the configuration file
        db_path: Path to the Zotero database file
    """
    try:
        # Ensure config directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new one
        full_config = {}
        if config_path.exists():
            try:
                with open(config_path) as f:
                    full_config = json.load(f)
            except Exception:
                pass

        # Ensure semantic_search section exists
        if "semantic_search" not in full_config:
            full_config["semantic_search"] = {}

        # Save the db_path
        full_config["semantic_search"]["zotero_db_path"] = db_path

        # Write back to file
        with open(config_path, "w") as f:
            json.dump(full_config, f, indent=2)

        print(f"Saved Zotero database path to config: {config_path}")

    except Exception as e:
        print(f"Warning: Could not save db_path to config: {e}")


def setup_zotero_environment(config_path: Path | None = None, *, strict_source: bool = False):
    """Setup Zotero environment for CLI commands."""
    if strict_source:
        if config_path is None:
            raise ValueError("an explicit config path is required for isolated source selection")
        source_env = load_standalone_env_vars(config_path)
        if not isinstance(source_env, dict):
            raise ValueError("explicit config client_env must be an object")
        required = ZOTERO_SOURCE_ENV[:4]
        missing = [key for key in required if key not in source_env or not str(source_env[key]).strip()]
        if missing:
            raise ValueError("explicit config must pin Zotero source variables: " + ", ".join(missing))
        local_mode = str(source_env["ZOTERO_LOCAL"]).strip().lower()
        if local_mode not in {"true", "false"}:
            raise ValueError("explicit config ZOTERO_LOCAL must be exactly true or false")
        library_type = str(source_env["ZOTERO_LIBRARY_TYPE"]).strip().lower()
        if library_type not in {"user", "group"}:
            raise ValueError("explicit config ZOTERO_LIBRARY_TYPE must be user or group")
        try:
            local_port = int(str(source_env["ZOTERO_LOCAL_PORT"]).strip())
        except ValueError as exc:
            raise ValueError("explicit config ZOTERO_LOCAL_PORT must be an integer") from exc
        if not 1 <= local_port <= 65535:
            raise ValueError("explicit config ZOTERO_LOCAL_PORT must be between 1 and 65535")
        if local_mode == "false" and not str(source_env.get("ZOTERO_API_KEY", "")).strip():
            raise ValueError("explicit web API config must include ZOTERO_API_KEY")
        source_env = dict(source_env)
        source_env["ZOTERO_LOCAL"] = local_mode
        source_env["ZOTERO_LOCAL_PORT"] = str(local_port)
        source_env["ZOTERO_LIBRARY_ID"] = str(source_env["ZOTERO_LIBRARY_ID"]).strip()
        source_env["ZOTERO_LIBRARY_TYPE"] = library_type
        for key in ZOTERO_SOURCE_ENV:
            os.environ.pop(key, None)
        apply_environment_variables({key: value for key, value in source_env.items() if key not in ZOTERO_SOURCE_ENV})
        for key in ZOTERO_SOURCE_ENV:
            if key in source_env and str(source_env[key]).strip():
                os.environ[key] = str(source_env[key])
        from zotero_mcp.client import clear_active_library

        clear_active_library()
        return
    # Load standalone env first so global flags (e.g., ZOTERO_NO_CLAUDE) take effect
    standalone_env_vars = load_standalone_env_vars()
    apply_environment_variables(standalone_env_vars)

    # Respect global switch to disable Claude detection
    no_claude = str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes")

    # Load and apply Claude Desktop env unless disabled
    if not no_claude:
        claude_env_vars = load_claude_desktop_env_vars()
        apply_environment_variables(claude_env_vars)

    # Apply fallback defaults for local Zotero if no config found.
    # Only apply when no API key is configured — if an API key exists,
    # the user intends web API mode and we should not force local mode.
    if not os.environ.get("ZOTERO_API_KEY"):
        fallback_env_vars = {
            "ZOTERO_LOCAL": "true",
            "ZOTERO_LIBRARY_ID": "0",
        }
        apply_environment_variables(fallback_env_vars)


def _recovery_protected_paths(config_path: Path) -> tuple[Path, ...]:
    """Discover canonical real, development, explicit, and environment-selected paths."""

    explicit = config_path.expanduser().resolve(strict=False)
    config_home = get_config_home()
    candidates = {
        explicit,
        explicit.parent / "chroma_db",
        get_config_path().resolve(strict=False),
        get_chroma_db_path().resolve(strict=False),
        config_home / "zotero-mcp" / "config.json",
        config_home / "zotero-mcp" / "chroma_db",
        config_home / "zotero-mcp-dev" / "config.json",
        config_home / "zotero-mcp-dev" / "chroma_db",
    }
    source_env = load_standalone_env_vars(explicit)
    if isinstance(source_env, dict):
        if source_env.get("ZOTERO_MCP_CONFIG_PATH"):
            candidates.add(Path(str(source_env["ZOTERO_MCP_CONFIG_PATH"])).expanduser().resolve(strict=False))
        if source_env.get("ZOTERO_MCP_CONFIG_DIR"):
            directory = Path(str(source_env["ZOTERO_MCP_CONFIG_DIR"])).expanduser().resolve(strict=False)
            candidates.update({directory / "config.json", directory / "chroma_db"})
        if source_env.get("ZOTERO_MCP_CHROMA_DB_PATH"):
            candidates.add(Path(str(source_env["ZOTERO_MCP_CHROMA_DB_PATH"])).expanduser().resolve(strict=False))
    return tuple(sorted((path.resolve(strict=False) for path in candidates), key=str))


def _validate_export_output(
    output_path: str | Path,
    config_path: Path,
    index_path: str | Path,
    protected_paths: tuple[Path, ...],
) -> Path:
    output = Path(output_path).expanduser().resolve(strict=False)
    sealed_index = Path(index_path).expanduser().resolve(strict=False)
    guarded = (*protected_paths, config_path.resolve(strict=False), sealed_index)
    for protected in guarded:
        if output == protected or output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError(f"snapshot output overlaps protected path: {protected}")
    return output


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(description="Zotero Model Context Protocol server")

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Server command (default behavior)
    server_parser = subparsers.add_parser("serve", help="Run the MCP server")
    server_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    server_parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to for SSE transport (default: localhost)",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to for SSE transport (default: 8000)",
    )

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Configure zotero-mcp (Claude Desktop or standalone)")
    setup_parser.add_argument(
        "--no-local", action="store_true", help="Configure for Zotero Web API instead of local API"
    )
    setup_parser.add_argument("--api-key", help="Zotero API key (only needed with --no-local)")
    setup_parser.add_argument("--library-id", help="Zotero library ID (only needed with --no-local)")
    setup_parser.add_argument(
        "--library-type",
        choices=["user", "group"],
        default="user",
        help="Zotero library type (only needed with --no-local)",
    )
    setup_parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Skip Claude Desktop config; write standalone config for web-based clients",
    )
    setup_parser.add_argument("--config-path", help="Path to Claude Desktop config file")
    setup_parser.add_argument("--skip-semantic-search", action="store_true", help="Skip semantic search configuration")
    setup_parser.add_argument(
        "--semantic-config-only", action="store_true", help="Only configure semantic search, skip Zotero setup"
    )

    # Update database command
    update_db_parser = subparsers.add_parser("update-db", help="Update semantic search database")
    update_db_parser.add_argument("--force-rebuild", action="store_true", help="Force complete rebuild of the database")
    update_db_parser.add_argument("--limit", type=int, help="Limit number of items to process (for testing)")
    update_db_parser.add_argument(
        "--fulltext",
        action="store_true",
        help="Extract fulltext content from local Zotero database (slower but more comprehensive)",
    )
    update_db_parser.add_argument("--config-path", help="Path to semantic search configuration file")
    update_db_parser.add_argument("--db-path", help="Path to Zotero database file (zotero.sqlite), overrides config")

    # Database status command
    db_status_parser = subparsers.add_parser("db-status", help="Show semantic search database status")
    db_status_parser.add_argument("--config-path", help="Path to semantic search configuration file")

    # DB inspect command (sample and filter indexed docs; also supports stats)
    inspect_parser = subparsers.add_parser(
        "db-inspect", help="Inspect indexed documents or show aggregate stats for the semantic DB"
    )
    inspect_parser.add_argument("--limit", type=int, default=20, help="How many records to show (default: 20)")
    inspect_parser.add_argument("--filter", dest="filter_text", help="Substring to match in title or creators")
    inspect_parser.add_argument("--show-documents", action="store_true", help="Show beginning of stored document text")
    inspect_parser.add_argument("--stats", action="store_true", help="Show aggregate stats (formerly db-stats)")
    inspect_parser.add_argument("--config-path", help="Path to semantic search configuration file")

    snapshot_parser = subparsers.add_parser(
        "export-snapshot",
        help="Export a read-only snapshot from a sealed isolated index",
    )
    snapshot_parser.add_argument("--output", required=True, help="Atomic JSON output path")
    snapshot_parser.add_argument("--config-path", help="Path to semantic search configuration file")
    snapshot_parser.add_argument("--index-path", required=True, help="Sealed isolated Chroma index directory")

    build_index_parser = subparsers.add_parser(
        "build-index",
        help="Build a fresh metadata-only 3072-d Chroma index without replacing an existing index",
    )
    build_index_parser.add_argument("--output", required=True, help="New isolated Chroma index directory")
    build_index_parser.add_argument("--config-path", help="Path to semantic search configuration file")
    build_index_parser.add_argument("--collection", default="zotero_library", help="New Chroma collection name")
    build_index_parser.add_argument(
        "--text-mode",
        choices=["metadata-only"],
        default="metadata-only",
        help="Frozen text mode (only metadata-only is currently safe)",
    )

    # Update command
    update_parser = subparsers.add_parser("update", help="Update zotero-mcp to the latest version")
    update_parser.add_argument("--check-only", action="store_true", help="Only check for updates without installing")
    update_parser.add_argument("--force", action="store_true", help="Force update even if already up to date")
    update_parser.add_argument(
        "--method", choices=["pip", "uv", "conda", "pipx"], help="Override auto-detected installation method"
    )

    # Version command
    subparsers.add_parser("version", help="Print version information")

    # Setup info command
    subparsers.add_parser("setup-info", help="Show installation path and configuration info for MCP clients")

    args = parser.parse_args()

    # If no command is provided, default to 'serve'
    if not args.command:
        args.command = "serve"
        # Also set default transport since we're defaulting to serve
        args.transport = "stdio"

    if args.command == "version":
        from zotero_mcp._version import __version__

        print(f"Zotero MCP v{__version__}")
        sys.exit(0)

    elif args.command == "setup-info":
        # Setup Zotero environment variables
        setup_zotero_environment()

        # Get the installation path
        executable_path = shutil.which("zotero-mcp")
        if not executable_path:
            executable_path = sys.executable + " -m zotero_mcp"

        # Determine whether Claude is disabled globally
        no_claude = str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes")

        # Load current environment configurations
        standalone_env_vars = load_standalone_env_vars()
        claude_env_vars = {} if no_claude else load_claude_desktop_env_vars()

        # Choose which env to display: prefer standalone if present or if Claude disabled
        display_env = (
            standalone_env_vars if (no_claude or standalone_env_vars) else (claude_env_vars or {"ZOTERO_LOCAL": "true"})
        )

        print("=== Zotero MCP Setup Information ===")
        print()
        print("🔧 Installation Details:")
        print(f"  Command path: {executable_path}")
        print(f"  Python path: {sys.executable}")

        # Detect installation method
        try:
            # Check if installed via uv
            result = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=5)
            if "zotero-mcp-server" in result.stdout or "zotero-mcp" in result.stdout:
                print("  Installation method: uv tool")
            else:
                # Check pip
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "show", "zotero-mcp-server"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    print("  Installation method: pip")
                else:
                    print("  Installation method: unknown")
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            print("  Installation method: unknown")

        print()
        print("⚙️  MCP Client Configuration:")
        print(f"  Command: {executable_path}")
        print("  Arguments: [] (empty)")

        # Show environment variables with obfuscated sensitive values
        obfuscated_env_vars = obfuscate_config_for_display(display_env)
        print(f"  Environment (single-line): {json.dumps(obfuscated_env_vars, separators=(',', ':'))}")
        print("  💡 Note: This shows client config. Shell variables may override for CLI use.")
        print(f"  Claude integration: {'disabled' if no_claude else 'enabled'}")

        # Only show Claude Desktop config if not globally disabled
        if not no_claude:
            print()
            print("For Claude Desktop (claude_desktop_config.json):")
            config_snippet = {"mcpServers": {"zotero": {"command": executable_path, "env": obfuscated_env_vars}}}
            print(json.dumps(config_snippet, indent=2))

        # Show semantic search database info with detailed statistics
        print()
        print("🧠 Semantic Search Database:")

        # Check for semantic search config
        config_path = get_config_path()
        if config_path.exists():
            try:
                from zotero_mcp.semantic_search import create_semantic_search

                # Get database status (similar to db-status command)
                search = create_semantic_search(str(config_path))
                status = search.get_database_status()

                collection_info = status.get("collection_info", {})

                print("  Status: ✅ Configuration file found")
                print(f"  Config path: {config_path}")
                print(f"  Collection: {collection_info.get('name', 'Unknown')}")
                print(f"  Document count: {collection_info.get('count', 0)}")
                print(f"  Embedding model: {collection_info.get('embedding_model', 'Unknown')}")
                print(f"  Database path: {collection_info.get('persist_directory', 'Unknown')}")

                update_config = status.get("update_config", {})
                print(f"  Auto update: {update_config.get('auto_update', False)}")
                print(f"  Update frequency: {update_config.get('update_frequency', 'manual')}")
                print(f"  Last update: {update_config.get('last_update', 'Never')}")
                print(f"  Should update: {status.get('should_update', False)}")

                if collection_info.get("error"):
                    print(f"  Error: {collection_info['error']}")

            except Exception as e:
                print("  Status: ⚠️ Configuration found but database error")
                print(f"  Error: {e}")
        else:
            print("  Status: ⚠️ Not configured")
            print("  💡 Run 'zotero-mcp setup' to configure semantic search")

        sys.exit(0)

    elif args.command == "setup":
        from zotero_mcp.setup_helper import main as setup_main

        sys.exit(setup_main(args))

    elif args.command == "update-db":
        # Setup Zotero environment variables
        setup_zotero_environment()

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = args.config_path
        if not config_path:
            config_path = get_config_path()
        else:
            config_path = Path(config_path)

        print(f"Using configuration: {config_path}")

        # Get optional db_path override from CLI
        db_path = getattr(args, "db_path", None)
        if db_path:
            print(f"Using custom Zotero database: {db_path}")
            # Save the db_path to config file for future use
            _save_zotero_db_path_to_config(config_path, db_path)

        try:
            # Create semantic search instance with optional db_path override
            search = create_semantic_search(str(config_path), db_path=db_path)

            print("Starting database update...")
            if args.fulltext:
                from zotero_mcp.utils import is_local_mode

                if not is_local_mode():
                    print(
                        "Error: --fulltext requires local mode but ZOTERO_LOCAL is not enabled.\n"
                        "Full-text indexing needs access to Zotero's local database.\n"
                        "Set ZOTERO_LOCAL=true or run 'zotero-mcp setup' to enable local mode.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                print("Extracting full-text content from local Zotero database...")
            stats = search.update_database(
                force_full_rebuild=args.force_rebuild, limit=args.limit, extract_fulltext=args.fulltext
            )

            print("\nDatabase update completed:")
            print(f"- Total items: {stats.get('total_items', 0)}")
            print(f"- Processed: {stats.get('processed_items', 0)}")
            print(f"- Added: {stats.get('added_items', 0)}")
            print(f"- Updated: {stats.get('updated_items', 0)}")
            print(f"- Skipped: {stats.get('skipped_items', 0)}")
            print(f"- Errors: {stats.get('errors', 0)}")
            print(f"- Duration: {stats.get('duration', 'Unknown')}")

            if stats.get("error"):
                print(f"Error: {stats['error']}")
                sys.exit(1)

        except Exception as e:
            print(f"Error updating database: {e}")
            sys.exit(1)

    elif args.command == "db-status":
        # Setup Zotero environment variables
        setup_zotero_environment()

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = args.config_path
        if not config_path:
            config_path = get_config_path()
        else:
            config_path = Path(config_path)

        try:
            # Create semantic search instance
            search = create_semantic_search(str(config_path))

            # Get database status
            status = search.get_database_status()

            print("=== Semantic Search Database Status ===")

            collection_info = status.get("collection_info", {})
            print(f"Collection: {collection_info.get('name', 'Unknown')}")
            print(f"Document count: {collection_info.get('count', 0)}")
            print(f"Embedding model: {collection_info.get('embedding_model', 'Unknown')}")
            print(f"Database path: {collection_info.get('persist_directory', 'Unknown')}")

            update_config = status.get("update_config", {})
            print("\nUpdate configuration:")
            print(f"- Auto update: {update_config.get('auto_update', False)}")
            print(f"- Frequency: {update_config.get('update_frequency', 'manual')}")
            print(f"- Last update: {update_config.get('last_update', 'Never')}")
            print(f"- Should update: {status.get('should_update', False)}")

            if collection_info.get("error"):
                print(f"\nError: {collection_info['error']}")

        except Exception as e:
            print(f"Error getting database status: {e}")
            sys.exit(1)

    elif args.command == "db-inspect":
        # Setup Zotero environment variables
        setup_zotero_environment()

        from collections import Counter

        from zotero_mcp.semantic_search import create_semantic_search

        # Determine config path
        config_path = args.config_path
        if not config_path:
            config_path = get_config_path()
        else:
            config_path = Path(config_path)

        try:
            search = create_semantic_search(str(config_path))
            client = search.chroma_client
            col = client.collection

            if args.stats:
                # Show aggregate stats (merged from former db-stats)
                meta = col.get(include=["metadatas"])  # type: ignore
                metas = meta.get("metadatas", [])
                print("=== Semantic DB Inspection (Stats) ===")
                info = client.get_collection_info()
                print(f"Collection: {info.get('name')} @ {info.get('persist_directory')}")
                print(f"Count: {info.get('count')}")

                # Item type distribution
                item_types = [(m or {}).get("item_type", "") for m in metas]
                ct_types = Counter(item_types)
                print("Item types:")
                for t, c in ct_types.most_common(20):
                    print(f"  {t or '(missing)'}: {c}")

                # Fulltext coverage by type (pdf/html)
                coverage = {}
                for m in metas:
                    m = m or {}
                    t = m.get("item_type", "") or "(missing)"
                    cov = coverage.setdefault(t, {"total": 0, "with_fulltext": 0, "pdf": 0, "html": 0})
                    cov["total"] += 1
                    if m.get("has_fulltext"):
                        cov["with_fulltext"] += 1
                        src = (m.get("fulltext_source") or "").lower()
                        if src == "pdf":
                            cov["pdf"] += 1
                        elif src == "html":
                            cov["html"] += 1
                print("Fulltext coverage (by type):")
                for t, cov in coverage.items():
                    print(f"  {t}: {cov['with_fulltext']}/{cov['total']} (pdf:{cov['pdf']}, html:{cov['html']})")

                # Common titles (may indicate duplicates)
                titles = [(m or {}).get("title", "") for m in metas]
                from collections import Counter as _Counter

                ct_titles = _Counter([t for t in titles if t])
                common = [(t, c) for t, c in ct_titles.most_common(10)]
                if common:
                    print("Common titles:")
                    for t, c in common:
                        print(f"  {t[:80]}{'...' if len(t) > 80 else ''}: {c}")
                return

            include = ["metadatas"]
            if args.show_documents:
                include.append("documents")

            # Fetch up to limit; filter client-side if requested
            data = col.get(limit=args.limit, include=include)

            print("=== Semantic DB Inspection ===")
            total = client.get_collection_info().get("count", 0)
            print(f"Total documents: {total}")
            print(f"Showing up to: {args.limit}")

            shown = 0
            for i, meta in enumerate(data.get("metadatas", [])):
                meta = meta or {}
                title = meta.get("title", "")
                creators = meta.get("creators", "")
                if args.filter_text:
                    needle = args.filter_text.lower()
                    if needle not in (title or "").lower() and needle not in (creators or "").lower():
                        continue
                print(f"- {title} | {creators}")
                if args.show_documents:
                    doc = (data.get("documents", [""])[i] or "").strip()
                    snippet = doc[:200].replace("\n", " ") + ("..." if len(doc) > 200 else "")
                    if snippet:
                        print(f"  doc: {snippet}")
                shown += 1
                if shown >= args.limit:
                    break

            if shown == 0:
                print("No records matched your filter.")

        except Exception as e:
            print(f"Error inspecting database: {e}")
            sys.exit(1)

    elif args.command == "build-index":
        from zotero_mcp.chroma_client import resolve_isolated_embedding
        from zotero_mcp.client import get_zotero_client
        from zotero_mcp.index_build import IsolatedIndexBuildError, build_isolated_index

        config_path = (Path(args.config_path) if args.config_path else get_config_path()).expanduser().resolve()
        try:
            protected_paths = _recovery_protected_paths(config_path)
            setup_zotero_environment(config_path, strict_source=True)
            resolved = resolve_isolated_embedding(str(config_path))
            manifest = build_isolated_index(
                get_zotero_client(),
                resolved,
                args.output,
                collection_name=args.collection,
                protected_paths=protected_paths,
            )
            print(
                json.dumps(
                    {
                        "status": "complete",
                        "output": str(Path(args.output).expanduser().resolve()),
                        "index_identity": manifest["index_identity"],
                        "counts": manifest["counts"],
                        "embedding": resolved.public_descriptor(),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "code": getattr(exc, "code", IsolatedIndexBuildError.code),
                        "message": str(exc),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            sys.exit(2)

    elif args.command == "export-snapshot":
        from zotero_mcp.snapshot_export import (
            SnapshotExportError,
            export_sealed_snapshot,
            write_snapshot_atomic,
        )

        config_path = (Path(args.config_path) if args.config_path else get_config_path()).expanduser().resolve()
        try:
            protected_paths = _recovery_protected_paths(config_path)
            output_path = _validate_export_output(
                args.output,
                config_path,
                args.index_path,
                protected_paths,
            )
            setup_zotero_environment(config_path, strict_source=True)
            snapshot = export_sealed_snapshot(config_path, args.index_path)
            papers = snapshot.get("papers")
            embeddings = snapshot.get("embeddings")
            if not isinstance(papers, list) or not isinstance(embeddings, list):
                raise SnapshotExportError("sealed snapshot records are incomplete")
            write_snapshot_atomic(snapshot, output_path)
            print(
                json.dumps(
                    {
                        "status": "exported",
                        "output": str(output_path),
                        "snapshot_id": snapshot["snapshot_id"],
                        "snapshot_hash": snapshot["snapshot_hash"],
                        "papers": len(papers),
                        "embeddings": len(embeddings),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "code": getattr(exc, "code", SnapshotExportError.code),
                        "message": str(exc),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            sys.exit(2)

    elif args.command == "update":
        from zotero_mcp.updater import update_zotero_mcp

        try:
            print("Checking for updates...")

            result = update_zotero_mcp(check_only=args.check_only, force=args.force, method=args.method)

            print("\n" + "=" * 50)
            print("UPDATE RESULTS")
            print("=" * 50)

            if args.check_only:
                print(f"Current version: {result.get('current_version', 'Unknown')}")
                print(f"Latest version: {result.get('latest_version', 'Unknown')}")
                print(f"Update needed: {result.get('needs_update', False)}")
                print(f"Status: {result.get('message', 'Unknown')}")
            else:
                if result.get("success"):
                    print("✅ Update completed successfully!")
                    print(
                        f"Version: {result.get('current_version', 'Unknown')} → {result.get('latest_version', 'Unknown')}"
                    )
                    print(f"Method: {result.get('method', 'Unknown')}")
                    print(f"Message: {result.get('message', '')}")

                    print("\n📋 Next steps:")
                    print("• All configurations have been preserved")
                    print("• Restart Claude Desktop if it's running")
                    print("• Your semantic search database is intact")
                    print("• Run 'zotero-mcp version' to verify the update")
                else:
                    print("❌ Update failed!")
                    print(f"Error: {result.get('message', 'Unknown error')}")

                    if backup_dir := result.get("backup_dir"):
                        print(f"\n🔄 Backup created at: {backup_dir}")
                        print("You can manually restore configurations if needed")

                    sys.exit(1)

        except Exception as e:
            print(f"❌ Update error: {e}")
            sys.exit(1)

    elif args.command == "serve":
        # Lazy import — triggers heavy dependencies (FastMCP, ChromaDB, etc.)
        from zotero_mcp.server import mcp

        # Get transport with a default value if not specified
        transport = getattr(args, "transport", "stdio")
        # Ensure environment is initialized (Claude config or standalone config)
        setup_zotero_environment()
        if transport == "stdio":
            mcp.run(transport="stdio")
        elif transport == "streamable-http":
            host = getattr(args, "host", "localhost")
            port = getattr(args, "port", 8000)
            mcp.run(transport="streamable-http", host=host, port=port)
        elif transport == "sse":
            host = getattr(args, "host", "localhost")
            port = getattr(args, "port", 8000)
            import warnings

            warnings.warn(
                "The SSE transport is deprecated and may be removed in a future version. New applications should use Streamable HTTP transport instead.",
                UserWarning,
            )
            mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    main()
