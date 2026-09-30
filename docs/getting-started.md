# Getting Started with Zotero MCP

This guide will walk you through the setup and basic usage of the Zotero MCP server, which allows AI assistants like Claude to interact with your Zotero library.

## Installation

First, install the Zotero MCP server using pip:

```bash
pip install zotero-mcp-server
```

## Configuration

The server needs to know how to connect to your Zotero library. There are two main ways to do this:

### Option 1: Local Zotero (Recommended)

If you're running Zotero 7 or newer on the same machine, you can connect to the local API:

1. Enable the local API in Zotero's preferences:
   - Open Zotero
   - Go to Edit > Preferences > Advanced > API
   - Check "Enable local API"

2. Set the environment variable:
   ```bash
   export ZOTERO_LOCAL=true
   ```

### Option 2: Zotero Web API

If you want to connect to your Zotero library via the web API:

1. Get your Zotero API key:
   - Go to [https://www.zotero.org/settings/keys](https://www.zotero.org/settings/keys)
   - Create a new key with appropriate permissions (at least "Read" access)

2. Find your library ID:
   - For personal libraries, your user ID is available at the same page
   - For group libraries, it's the number in the URL when viewing the group

3. Set the environment variables:
   ```bash
   export ZOTERO_API_KEY=your_api_key
   export ZOTERO_LIBRARY_ID=your_library_id
   export ZOTERO_LIBRARY_TYPE=user  # or 'group' for group libraries
   ```

## Integrating with Claude Desktop

To use Zotero MCP with Claude Desktop:

1. Make sure you have Claude Desktop installed
2. Open your Claude Desktop configuration:
   - On macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - On Windows: `%APPDATA%\Claude\claude_desktop_config.json`

3. Add the Zotero MCP server to the configuration:
   ```json
   {
     "mcpServers": {
       "zotero": {
         "command": "zotero-mcp",
         "env": {
           "ZOTERO_LOCAL": "true"
         }
       }
     }
   }
   ```

4. Restart Claude Desktop

- The tool should be available automatically: if not, you might need to double check in the connections menu under Settings.

## **New**: Integrating with OpenAI's ChatGPT

This is a new (September 2025) option available through the ChatGPT web app. For the web app, you must use [ChatGPT Developer mode](https://platform.openai.com/docs/guides/developer-mode) which may be restricted to a limited number of OpenAI platforms and apps. A paid subscription appears to be required.

As of today, zotero-mcp is not available by default on as a web-based MCP, and it seems likely that many users will want to stick with a local MCP due to their large document libraries. Since ChatGPT does not support local MCPs natively through their desktop app (yet?) the way you can move forward is by tunneling.

**Use at your own risk**
While we think that the risk to many individuals will be quite low (Zotero libraries are often composed of large numbers of publically-available documents), the risk of data loss or theft will be present. We are working on a way to secure the server connection (this should be available soon), but even with absolute security there is still the exposure to the AI itself, which we leave to the user to judge for themselves. Please consider your situation before continuing with this guide.

### Setting up a desktop tunnel for zotero-mcp

A tunnel makes your locally running `zotero-mcp` server securely available to a web service like ChatGPT. We recommend [ngrok](https://ngrok.com/) for this.

1.  **Install ngrok**: Follow the instructions on the [ngrok website](https://ngrok.com/download) to download and install it. Mac users can use `brew` and we have successfully tested this approach.

2.  **Start the `zotero-mcp` server**: Before starting the tunnel, make sure your MCP server is running. For web-based clients, the `sse` transport is recommended. Open a terminal and run:
    ```bash
    # Make sure your Zotero environment variables are set first!
    # e.g., export ZOTERO_LOCAL=true
    zotero-mcp serve --transport sse --host 0.0.0.0 --port 8000
    ```

Important: you should probably leave this terminal open in order to ensure tunnel traffic is successfully transiting to the server.

3.  **Start the ngrok tunnel**: Open a *second* terminal and start ngrok, pointing it to the port your server is using (8000). Here is an instruction that will work on a mac
    ```bash
    ngrok http 8000
    ```
4.  **Copy the URL**: Ngrok will provide a public `Forwarding` URL that looks something like `https://<random-string>.ngrok-free.app`. Copy this HTTPS URL—you'll need it for the ChatGPT connector setup.

### Setting up a ChatGPT or OpenAI client for zotero-mcp
There are actually two ways to work with ChatGPT on the web once you have a tunnel open to your server: through the ChatGPT app at [chatgpt.com](https://chatgpt.com), or through the chat prompt builder screen at the [OpenAI platform page](https://platform.openai.com/chat).

The setup is nearly identical for both.

#### 1. ChatGPT.com setup

1.  Navigate to [chatgpt.com](https://chatgpt.com). Make sure you are logged in, and at the base "chat" user interface.
2.  Click on your profile name, then **Settings**.
3.  Go to the **Connectors** tab:
    *   First you must enable "Developer Mode." At the bottom of the connectors tab, there is an "Advanced..." button. Click this and then on the next screen enable "Developer Mode."
    *   Now from the main Connectors browser window, click **Create**
4.  Fill in the details:
    *   **Name**: Zotero MCP
    *   **Description**: Search and retrieve documents from a local Zotero library.
    *   **MCP Server URL**: This is the critical part. You need to combine your ngrok URL, the `/sse/` endpoint (with a trailing slash), and a unique `session_id`.
        *   The trailing slash on `/sse/` is important to avoid a redirect.
        *   The `session_id` must be a valid [UUIDv4](https://www.uuidgenerator.net/). While some clients might negotiate a session automatically, explicitly providing a unique ID is the most reliable method.
        *   Example URL: `https://<YOUR_NGROK_URL>.ngrok-free.app/sse/?session_id=<YOUR_UUID>`
    *   **Authentication**: `No authentication`
    *   Tick the "I trust this application" checkbox.
5.  Click **Create**. If you are successfully connecting you should see relevant communications logs in your tunnel and your server terminals. If this is successful, an important indication will be the listing of all zotero-mcp tools in the ChatGPT interface.
    *   *Important: our testing indicates that you need to turn all the "Edit" sliders to "Off" in the list of tools.* Otherwise the tool may not be enabled in Developer Mode.

      ![ChatGPT Connector Tool List](../public/ChatGPT_zot_mcp_1.png)

6.  You should now be ready to add Zotero-MCP to new chats. To do this, go to the main ChatGPT interface. It should indicate that you are in development mode. When you start a new chat, click the "plus" icon in the text box interface to select "Deep Research" as a chat mode. A "Sources" menu will become available: enable Zotero-MCP as one of the sources:

      ![Enable Zotero-MCP as a Source](../public/ChatGPT_zot_mcp_2.png)

#### 2. OpenAI Chat Builder setup

The process is the same as above, but you create the connector within the context of building a custom GPT on the OpenAI Platform.

1.  Navigate to the [OpenAI Platform Chat page](https://platform.openai.com/chat).
2.  When configuring a custom GPT, go to the **Tools** section and choose to add an MCP connector.
3.  Follow the same steps as in the `ChatGPT.com setup` to configure the connector URL and other details.

## Integrating with Chorus.sh

[Chorus.sh](https://chorus.sh) is a popular multi-chatbot interface that configures MCP servers through an online preferences form rather than config files.
This would be one possible path to working with Zotero with chatbots other than Claude.

To set up Zotero MCP with Chorus.sh:

1. **Find your installation path**:
   - For uv: typically `/Users/USERNAME/.pyenv/versions/3.12.8/bin/zotero-mcp` on macOS
   - For other methods: use `zotero-mcp setup-info` to get the exact path and configuration details

2. **Configure in Chorus.sh preferences**:
   - **Command**: Enter the full path to your zotero-mcp installation
   - **Arguments**: Leave empty (no custom --port or --host arguments needed unless set at config time)
   - **Environment (JSON)**: Take your environment configuration JSON (including outer brackets), remove newlines, and paste as a single line

3. **Example Environment JSON** (single line format):
   ```json
   {"ZOTERO_LOCAL": "true"}
   ```

Many other MCP consumers use similar configuration approaches with command path, arguments, and environment variables.

## Using with Other MCP Clients

Zotero MCP works with any MCP-compatible client. You can start the server manually:

```bash
zotero-mcp serve --transport stdio
```

For HTTP/SSE-based clients:

```bash
zotero-mcp serve --transport sse --host localhost --port 8000
```

## Optional Services in the Paper Acquisition Workflow

The core MCP server can run by itself for Zotero search, metadata, fulltext, notes, annotations, and basic write operations. Two optional services extend acquisition behavior when you need URL translation or session-gated PDF downloads.

### Zotero translation-server

Zotero translation-server is a separate Node.js HTTP service from the Zotero project. It is not the Zotero desktop binary and is not started by `zotero-mcp`.

`zotero-mcp` uses it only as a client:

- default URL: `http://127.0.0.1:1969`
- override: `ZOTERO_TRANSLATION_SERVER_URL`
- check tool: `translation_server_status`
- translate tool: `translate_with_translation_server`

When `resolve_paper_access` receives a URL, the resolver first checks whether translation-server is reachable. If available, translated Zotero metadata and PDF attachments can seed the acquisition result before the fallback URL translator path runs.

### Browser bridge for institutional PDFs

Institutional access is decided by `zotero-mcp`, but browser automation is delegated to the bridge server in `packages/opencode-deep-research`.

#### Operator setup for authenticated bridge calls

The bridge now expects authenticated caller requests. Set `BRIDGE_AUTH_TOKEN` on both the `zotero-mcp` side and the bridge-server side, and keep the value private.

What to keep in mind:

- `BRIDGE_AUTH_TOKEN` must be the same on both sides and should be at least 32 characters long.
- Store it in a private env file, secret manager, or service definition, not in committed config or shared screenshots.
- With no caller bridge configuration at all (none of `ZOTERO_BRIDGE_TOKEN`, `BRIDGE_AUTH_TOKEN`, `BRIDGE_TOKEN` or `BRIDGE_SERVER_URL` set), bridge calls are skipped and `acquire_paper` uses the normal direct-download path. This is the only case in which direct download is used.
- Once any of those is set, the bridge is the only acquisition channel and `session_name` is required. An empty, missing or malformed token fails with `BRIDGE_AUTH_INVALID` before any bridge request; an unreachable bridge fails with `BRIDGE_UNREACHABLE`, a bridge that is not ready with `CAPABILITY_UNAVAILABLE`, and an invalid readiness response with `BRIDGE_HEALTH_INVALID`. Acquisition stops on each of these and never switches to direct download.
- `BRIDGE_ALLOWED_DOMAINS` is optional and should contain only operator-approved extra hostnames that are not already derived from the candidate URL or nested redirect targets.

#### Rollout order

Setting any bridge variable on the caller makes the bridge the only acquisition channel, so bring the bridge server up first.

1. Set `BRIDGE_AUTH_TOKEN` on the bridge server, start it, and authenticate the named browser session.
2. Set the same `BRIDGE_AUTH_TOKEN` where `zotero-mcp` runs. Add `BRIDGE_ALLOWED_DOMAINS` only when you need extra approved hosts.
3. Pass `session_name` on every `acquire_paper` call from then on.

To return to direct HTTP downloads, unset every bridge variable on the caller side.

#### Allowed-domain behavior

`GET /bridge/health/ready` carries bearer auth only. `POST /bridge/download` carries bearer auth plus an explicit allowed-domain set.

- The caller derives hosts from the requested `candidate_url`.
- For LibProxy and other nested redirect URLs, the caller also derives hosts from nested targets, such as the proxied `url=` destination.
- `BRIDGE_ALLOWED_DOMAINS` adds optional normalized operator-managed extras for known publisher CDNs or secondary download hosts.
- Redirects never auto-expand trust. If a later hop lands on a host outside the derived or explicit allowlist, the bridge rejects it.

The source-level flow is:

1. `resolve_paper_access` normalizes the input and resolves public locations through Unpaywall, Semantic Scholar, PMC OA, arXiv, URL translators, and optional institutional access.
2. Institutional LibProxy locations are marked with `requires_session=true` and `session_kind="libproxy"`.
3. With the bridge configured, `acquire_paper(identifier, session_name="libproxy-snu")` checks `GET /bridge/health/ready` on `BRIDGE_SERVER_URL` (default `http://127.0.0.1:9870`) with bearer auth.
4. If the bridge is ready, it sends `POST /bridge/download` with `doi`, `candidate_url`, `session_name`, and the required allowed-domain list.
5. If the bridge succeeds, the returned provenance includes `bridge_session` and `file_path` is a caller-owned verified copy (also the file ingested with `auto_ingest=true`). Every bridge failure is terminal and returns its own `error_code`: `BRIDGE_AUTH_INVALID` or `UNAUTHORIZED` for auth, `BRIDGE_UNREACHABLE`, `CAPABILITY_UNAVAILABLE` (not ready) or `BRIDGE_HEALTH_INVALID` for readiness, `BRIDGE_RESPONSE_INVALID` or `TIMEOUT` for the download, and the server's own code for a refused request. There is no fallback to direct HTTP; that path is used only when the bridge is not configured.

The bridge does not automate campus login. Start and authenticate the named Pinchtab browser session before calling `acquire_paper` with `session_name`.


## Available Tools

When connected to Claude Desktop or another MCP client, you'll have access to these tools:

- **zotero_search_items**: Search your library by title, creator, or content
- **zotero_semantic_search**: Search your library by embedding similarity when semantic search is configured
- **zotero_get_item_metadata**: Get detailed information about a specific item
- **zotero_get_item_fulltext**: Get the full text content of an item
- **zotero_get_collections**: List all collections in your library
- **zotero_get_collection_items**: Get all items in a specific collection
- **zotero_get_item_children**: Get child items (attachments, notes) for a specific item
- **zotero_get_tags**: Get all tags used in your library
- **zotero_get_recent**: Get recently added items to your library
- **resolve_paper_access**: Resolve DOI, arXiv ID, or URL inputs to candidate access locations
- **acquire_paper**: Resolve, download, and optionally ingest a paper, with optional browser-bridge routing via `session_name`
- **translation_server_status**: Check whether the optional Zotero translation-server helper is reachable

## Example Queries

Once connected, you can ask Claude questions like:

- "Search my Zotero library for papers about machine learning"
- "Find articles by Smith in my Zotero library"
- "Show me my most recent additions to Zotero"
- "What collections do I have in my Zotero library?"
- "Get the full text of paper XYZ from my Zotero library"

## Troubleshooting

If you encounter issues:

- Make sure Zotero is running (for local API)
- Check that your API key has the correct permissions
- Verify your library ID and type
- Look for error messages in the Claude Desktop logs or MCP server output

### Local Library Limitations

Some functionality will not work for local libraries due to the distinct differences with [Zotero's local JS API](https://www.zotero.org/support/dev/client_coding/javascript_api). For instance, tagging and other library modifications might not work as expected with the local API connection.

**Workaround**: Even without web storage, a workaround for some of these functionalities might be to set up a web library, point the MCP at that, and then things like setting tags should work properly. We're thinking about better ways to work with local instances in future updates.

### Database Issues

Switching installs or install methods (sometimes to deal with failed installs), as well as toggling between search options, can sometimes lead to database problems. These can frequently be solved with:

```bash
zotero-mcp update-db --force-rebuild
```

Other than time waiting for the rebuild, there is generally little to no risk involved in triggering the rebuild - so if you're experiencing database-related issues, it's worth trying this command.

For more help, try the [discussions](https://github.com/54yyyu/zotero-mcp/discussions).
