// SRAN MCP Gateway (R11 B2 + Desktop Phase 7 PART B + Phase 10 C1/C3/C5)
// Runs stdio MCP servers as child processes behind streamable HTTP via
// supergateway, plus a catalog endpoint the desktop app reads.
//
// EVERY server — built-in (auth_type "none") and oauth2 alike — is reached
// through POST /mcp/servers/{id}/mcp and REQUIRES a valid portal access
// JWT (Phase 10 C1; the shared, unauthenticated none-auth children are
// gone). A child is spawned per (portal user, connector):
//   auth_type "none"   — filesystem/memory scoped to /data/workspace/{sub}
//     (per-user root / per-user memory graph); no provider token needed.
//   auth_type "oauth2" — this gateway additionally fetches the user's
//     provider token from the backend broker (forwarding that same JWT)
//     and injects it into the child's env. Tokens live only in this
//     process's memory and the child env — never on disk, never in a URL,
//     never logged.
//
// Env:
//   MCP_PUBLIC_BASE    e.g. http://localhost (dev) | https://sran.company.com
//   MCP_PUBLIC_MODE    "port" (dev) | "path" ({base}/mcp/servers/{id})
//   PORTAL_JWT_SECRET  HS256 secret the backend signs portal JWTs with
//   BACKEND_BASE       backend broker base URL (default http://backend:8080)
//   MCP_SLACK_TEAM_ID  Slack workspace/team id for the slack connector
const express = require('express');
const fs = require('fs');
const { spawn } = require('child_process');
const { Readable } = require('stream');
const {
  SERVERS,
  forModels,
  internalTokenOk,
  buildCatalog,
  verifyPortalJwt,
  userChildConfig,
  InstanceManager,
  TokenFetchError
} = require('./lib');

const PUBLIC_BASE = (process.env.MCP_PUBLIC_BASE || 'http://localhost').replace(/\/$/, '');
const PUBLIC_MODE = process.env.MCP_PUBLIC_MODE || 'port';
const PORTAL_JWT_SECRET = process.env.PORTAL_JWT_SECRET || '';
const BACKEND_BASE = (process.env.BACKEND_BASE || 'http://backend:8080').replace(/\/$/, '');
// AM9: chat-edge's token for the servers no model may use (the mail connector)
const MCP_INTERNAL_TOKEN = process.env.MCP_INTERNAL_TOKEN || '';

// ── Per-user children — spawned on demand, LRU + idle eviction ─────────────

function spawnUserChild(server, port, token, sub) {
  // C1: per-user scope — filesystem root / memory file under the user's
  // own /data/workspace/{sub}; oauth2 servers keep their static args.
  const scope = userChildConfig(server, sub);
  for (const dir of scope.dirs) fs.mkdirSync(dir, { recursive: true });

  const stdioCmd = [server.command, ...scope.args].join(' ');
  const extra = typeof server.static_env === 'function' ? server.static_env() : {};
  // AM9: a clean_env child (the mail connector) gets PATH and its own variables only
  const base = server.clean_env ? { PATH: process.env.PATH || '', HOME: '/tmp', NODE_ENV: 'production' } : process.env;
  const env = { ...base, ...extra, ...scope.env };
  // The provider token rides ONLY in the child env — never argv (argv is
  // visible in `ps`), never logged.
  if (server.token_env && token) env[server.token_env] = token;

  const child = spawn('npx', [
    '-y', 'supergateway',
    '--stdio', stdioCmd,
    '--outputTransport', 'streamableHttp',
    '--port', String(port),
    '--streamableHttpPath', '/mcp'
  ], { stdio: 'inherit', env });
  console.log(`[mcp-gateway] spawned ${server.id} child on :${port}`);
  return child;
}

async function fetchProviderToken(connectorId, authHeader) {
  const resp = await fetch(
    `${BACKEND_BASE}/portal/connectors/${encodeURIComponent(connectorId)}/token`,
    { method: 'POST', headers: { Authorization: authHeader } }
  );
  if (!resp.ok) throw new TokenFetchError(resp.status);
  return resp.json();
}

// ── HTTP surface ───────────────────────────────────────────────────────────

function rpcError(res, httpStatus, code, message) {
  res.status(httpStatus).json({
    jsonrpc: '2.0',
    id: null,
    error: { code, message }
  });
}

// A freshly spawned supergateway needs a moment to listen — retry
// connection refusals briefly before giving up.
async function proxyWithRetry(port, req, attempts = 10, delayMs = 300) {
  const headers = { accept: req.headers.accept || 'application/json, text/event-stream' };
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
  if (req.headers['mcp-session-id']) headers['mcp-session-id'] = req.headers['mcp-session-id'];

  for (let i = 0; ; i++) {
    try {
      return await fetch(`http://127.0.0.1:${port}/mcp`, {
        method: req.method,
        headers,
        body: req.method === 'POST' && req.body?.length ? req.body : undefined
      });
    } catch (err) {
      if (i >= attempts - 1) throw err;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}

/**
 * Build the express app. Injectable for tests; server.js's bootstrap
 * (below, under require.main) passes the real manager.
 */
function createApp({
  servers = SERVERS,
  manager,
  publicBase = PUBLIC_BASE,
  publicMode = PUBLIC_MODE,
  portalJwtSecret = PORTAL_JWT_SECRET,
  internalToken = MCP_INTERNAL_TOKEN,
  retryAttempts = 10,
  retryDelayMs = 300
} = {}) {
  const app = express();

  app.get('/catalog.json', (_req, res) => {
    res.json(buildCatalog(servers, publicBase, publicMode));
  });

  // C5: unauthenticated health is a liveness probe ONLY — no instance
  // counts or other tenant-shaped internals leak here.
  app.get('/health', (_req, res) =>
    res.json({
      status: 'ok',
      servers: servers.map((s) => s.id)
    }));

  // Streamable-HTTP proxy for both server kinds under the path convention.
  // POST carries JSON-RPC; DELETE closes an Mcp-Session-Id session.
  app.all(
    '/mcp/servers/:id/mcp',
    express.raw({ type: '*/*', limit: '20mb' }),
    async (req, res) => {
      const server = servers.find((s) => s.id === req.params.id);
      if (!server) return rpcError(res, 404, -32601, `unknown server '${req.params.id}'`);
      if (req.method !== 'POST' && req.method !== 'DELETE')
        return rpcError(res, 405, -32600, 'method not allowed');

      // C1: EVERY server requires a valid portal access JWT — none-auth
      // (filesystem/memory) included. Before Phase 10 the none-auth branch
      // skipped verification entirely and proxied anyone who could reach
      // :3200 to a single child shared across ALL users.
      const payload = verifyPortalJwt(
        req.headers.authorization?.replace(/^Bearer\s+/i, ''),
        portalJwtSecret
      );
      if (!payload) return rpcError(res, 401, -32001, 'invalid or missing portal token');
      // AM9: a server no model may use answers chat-edge only — a person's own
      // token (the desktop's, a model's tool loop) is not enough
      if (!forModels(server) && !internalTokenOk(req.headers['x-internal-token'], internalToken))
        return rpcError(res, 403, -32003, 'not available to this caller');

      let port;
      try {
        const entry = await manager.acquire(server, payload.sub, req.headers.authorization);
        port = entry.port;
      } catch (err) {
        if (err instanceof TokenFetchError) {
          // Connector not connected / token dead: the desktop shows this
          // as NotConfigured and prompts a re-connect.
          return rpcError(res, 401, -32001, 'connector_not_connected');
        }
        console.error(`[mcp-gateway] acquire ${server.id} failed:`, err.message);
        return rpcError(res, 502, -32000, 'connector instance unavailable');
      }

      try {
        const upstream = await proxyWithRetry(port, req, retryAttempts, retryDelayMs);
        res.status(upstream.status);
        for (const h of ['content-type', 'mcp-session-id']) {
          const v = upstream.headers.get(h);
          if (v) res.setHeader(h, v);
        }
        // C3: PIPE the upstream body instead of buffering it — a
        // text/event-stream MCP response must reach the client
        // incrementally, and long tool calls must not stall on
        // arrayBuffer() waiting for the stream to close.
        if (upstream.body) {
          const body = Readable.fromWeb(upstream.body);
          body.on('error', (err) => {
            console.error(`[mcp-gateway] upstream stream from :${port} broke:`, err.message);
            res.destroy(); // headers already sent — cut the socket
          });
          res.on('close', () => body.destroy()); // client went away — cancel upstream
          body.pipe(res);
        } else {
          res.end();
        }
      } catch (err) {
        console.error(`[mcp-gateway] proxy to :${port} failed:`, err.message);
        rpcError(res, 502, -32000, 'upstream MCP server unavailable');
      }
    }
  );

  return app;
}

module.exports = { createApp, spawnUserChild, fetchProviderToken, proxyWithRetry };

// ── Bootstrap (skipped under node --test: tests require() this module) ─────

if (require.main === module) {
  const manager = new InstanceManager({
    spawnChild: spawnUserChild,
    fetchToken: fetchProviderToken
  });
  setInterval(() => manager.sweep(), 60 * 1000).unref();
  process.on('SIGTERM', () => {
    manager.dispose();
    process.exit(0);
  });

  createApp({ manager }).listen(3200, () =>
    console.log('[mcp-gateway] catalog on :3200'));
}
