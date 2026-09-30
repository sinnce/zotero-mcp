// Runs the real pkb-browser-bridge 2.0.0 server modules under Bun and prints
// their verdicts. Usage: bun server_oracle.ts <server lib dir> < cases.json
// The lib dir is imported read-only; nothing is written to the server repo.
//
// Case kinds (stdin is a JSON array, stdout the matching JSON array):
//   {"kind": "request", "raw_b64": "..."}  -> {"http_status": n, "body": {...}}
//   {"kind": "token", "raw": "<token>"}    -> the validated token, or null
//   {"kind": "request_schema", "body": {}} -> the parsed request, or null
const lib = process.argv[2]
if (!lib) throw new Error("usage: bun server_oracle.ts <server lib dir>")
const server = await import(`${lib}/bridge-server.ts`)
const contract = await import(`${lib}/bridge-v2-contract.ts`)

const TOKEN = "OracleToken_0123456789abcdefghijklmnopqrstuvwxyz"
const handler = server.createBridgeRequestHandler({
  token: TOKEN,
  sessions: new Set(["campus"]),
  // Every destination resolves to one public IPv4 address, so a request that
  // passes the policy checks reaches readiness.
  createResolver: () => ({
    resolve4: async () => ["93.184.216.34"],
    resolve6: async () => {
      throw Object.assign(new Error("nodata"), { code: "ENODATA" })
    },
    cancel: () => {},
  }),
  // Unqualified readiness: an accepted request ends as CAPABILITY_UNAVAILABLE.
  readiness: async () => ({
    browser_get_version: false,
    fetch_interception: false,
    configured_sessions: 1,
    ready_sessions: 0,
  }),
})

const cases = JSON.parse(await Bun.stdin.text()) as any[]
const out: unknown[] = []
for (const c of cases) {
  if (c.kind === "request") {
    // The exact bytes and headers httpx sends, Content-Length included.
    const raw = Buffer.from(c.raw_b64, "base64")
    const response = await handler(
      new Request("http://127.0.0.1:9870/bridge/download", {
        method: "POST",
        headers: {
          authorization: `Bearer ${TOKEN}`,
          "content-type": "application/json; charset=utf-8",
          "content-length": String(raw.length),
        },
        body: raw,
      }),
    )
    out.push({ http_status: response.status, body: await response.json() })
  } else if (c.kind === "token") {
    try {
      out.push(server.validateBridgeToken(c.raw))
    } catch {
      out.push(null)
    }
  } else if (c.kind === "request_schema") {
    const parsed = contract.bridgeDownloadRequestSchema.safeParse(c.body)
    out.push(parsed.success ? parsed.data : null)
  } else {
    throw new Error(`unknown case kind: ${c.kind}`)
  }
}
console.log(JSON.stringify(out))
