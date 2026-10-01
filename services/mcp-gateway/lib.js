// SRAN MCP Gateway — testable core (Desktop Phase 7 PART B).
// server.js wires these pieces to express + real child processes; tests
// (node --test) exercise them with fakes. No new npm dependencies: the
// portal JWT is HS256, verified with node's crypto.

const crypto = require('crypto');
const path = require('path');

// ---------------------------------------------------------------------------
// Server registry
// ---------------------------------------------------------------------------
// ALL servers now spawn a child per (portal user, connector) — Phase 10 C1
// removed the shared none-auth children so one user can never read another
// user's files or memory graph. none-auth ("built-in") servers skip the
// provider-token fetch but still REQUIRE a valid portal access JWT; their
// per-user scope (filesystem root / memory file) comes from `user_scope`.
// oauth2 servers additionally get the user's provider token in `token_env`,
// fetched from the backend broker at request time.

const WORKSPACE_BASE = '/data/workspace';

const SERVERS = [
  {
    id: 'filesystem',
    name: 'File System (your workspace)',
    description: 'Read and write files in your private server workspace',
    category: 'built-in',
    auth_type: 'none',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', WORKSPACE_BASE],
    // C1: each user gets an isolated root — /data/workspace/{sub}/files.
    user_scope: (root) => ({
      args: ['-y', '@modelcontextprotocol/server-filesystem', `${root}/files`],
      env: {},
      dirs: [`${root}/files`]
    }),
    tools: ['read_file', 'write_file', 'list_directory', 'search_files']
  },
  {
    id: 'memory',
    name: 'Knowledge Graph Memory',
    description: 'Persistent knowledge-graph memory the AI can read and write',
    category: 'built-in',
    auth_type: 'none',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-memory'],
    // C1: per-user graph file, kept OUTSIDE the filesystem root ({root}/files)
    // so the filesystem tools can't tamper with it.
    user_scope: (root) => ({
      args: ['-y', '@modelcontextprotocol/server-memory'],
      env: { MEMORY_FILE_PATH: `${root}/memory.json` },
      dirs: [root]
    }),
    tools: ['create_entities', 'create_relations', 'search_nodes', 'read_graph']
  },
  {
    id: 'github',
    name: 'GitHub',
    description: 'Search repositories, read and manage issues and pull requests as you',
    category: 'developer',
    auth_type: 'oauth2',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-github'],
    // Env contract per @modelcontextprotocol/server-github README.
    token_env: 'GITHUB_PERSONAL_ACCESS_TOKEN',
    tools: ['search_repositories', 'get_issue', 'create_issue', 'list_pull_requests', 'get_file_contents']
  },
  {
    id: 'slack',
    name: 'Slack',
    description: 'Send messages, read channels and react in your Slack workspace',
    category: 'communication',
    auth_type: 'oauth2',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-slack'],
    // Env contract per @modelcontextprotocol/server-slack README: the bot
    // token plus SLACK_TEAM_ID (a deployment-level value — set
    // MCP_SLACK_TEAM_ID on this service; it is not per-user).
    token_env: 'SLACK_BOT_TOKEN',
    static_env: () => ({ SLACK_TEAM_ID: process.env.MCP_SLACK_TEAM_ID || '' }),
    tools: ['slack_post_message', 'slack_list_channels', 'slack_get_channel_history', 'slack_add_reaction']
  },
  {
    // AM9 — "send as me": a person's approved message, from their own mailbox,
    // with its attachments (servers/mail-send.js; scope gmail.send only — it
    // cannot read mail). The same per-(person, connector) child and the same
    // token broker as github/slack: the token lives only in this child's env.
    //
    // NOT a model's tool, ever: `models: false` keeps it out of the catalogue
    // every model's tools are resolved from (the desktop's), and the proxy
    // refuses a call without chat-edge's internal token (MCP_INTERNAL_TOKEN).
    // A model drafts; chat-edge sends exactly what the person approved.
    id: 'mail',
    name: 'E-mail (send as me)',
    description: "Sends a message the person approved from their own mailbox. Used by the app, never by a model.",
    category: 'communication',
    auth_type: 'oauth2',
    command: 'node',
    args: [path.join(__dirname, 'servers', 'mail-send.js')],
    token_env: 'MAIL_ACCESS_TOKEN',
    static_env: () => (process.env.MCP_MAIL_API_BASE ? { MAIL_API_BASE: process.env.MCP_MAIL_API_BASE } : {}),
    // the child gets what it needs and nothing of the gateway's own environment
    clean_env: true,
    models: false,
    tools: ['send_message']
  }
];

/** A server whose tools no model may have (AM9: the mail connector). */
function forModels(server) {
  return server.models !== false;
}

/** Constant-time comparison of chat-edge's internal token (empty configured = refuse). */
function internalTokenOk(supplied, configured) {
  if (!configured || !supplied) return false;
  const a = Buffer.from(String(supplied));
  const b = Buffer.from(String(configured));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// ---------------------------------------------------------------------------
// Catalog — field names are the desktop McpConnector JSON contract:
// id, name, description, category, auth_type, server_url, tools.
// ---------------------------------------------------------------------------

function buildCatalog(servers, publicBase, publicMode) {
  const base = String(publicBase || '').replace(/\/$/, '');
  // AM9: a server no model may use is not in the catalogue at all
  return {
    connectors: servers.filter(forModels).map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
      category: s.category,
      auth_type: s.auth_type,
      server_url: serverUrl(s, base, publicMode),
      tools: s.tools
    }))
  };
}

function serverUrl(server, base, mode) {
  // Phase 10 C1: EVERY server — none-auth included — routes through this
  // gateway's authenticated per-user path (the desktop appends /mcp →
  // POST /mcp/servers/{id}/mcp). The legacy direct child ports (:3201/:3202)
  // bypassed JWT verification entirely and are gone.
  return mode === 'path'
    ? `${base}/mcp/servers/${server.id}`
    : `${base}:3200/mcp/servers/${server.id}`;
}

// ---------------------------------------------------------------------------
// Portal JWT (HS256) — same secret the backend signs with.
// ---------------------------------------------------------------------------

function b64urlDecode(part) {
  return Buffer.from(part.replace(/-/g, '+').replace(/_/g, '/'), 'base64');
}

/**
 * Verify an HS256 portal JWT. Returns the payload ({sub, tenant_id, ...})
 * or null on ANY failure — bad shape, bad signature, expired, or not a
 * portal-type token. Never throws.
 */
function verifyPortalJwt(token, secret, nowSeconds) {
  try {
    if (!token || !secret) return null;
    const parts = token.split('.');
    if (parts.length !== 3) return null;

    const header = JSON.parse(b64urlDecode(parts[0]).toString('utf8'));
    if (header.alg !== 'HS256') return null; // no alg confusion

    const expected = crypto
      .createHmac('sha256', secret)
      .update(`${parts[0]}.${parts[1]}`)
      .digest();
    const actual = b64urlDecode(parts[2]);
    if (expected.length !== actual.length) return null;
    if (!crypto.timingSafeEqual(expected, actual)) return null;

    const payload = JSON.parse(b64urlDecode(parts[1]).toString('utf8'));
    if (payload.type !== 'portal') return null;
    // Phase 10 C4: refresh tokens are allowed only at the Portal's /refresh
    // endpoint, never as a Bearer here — mirrors the Go verifier
    // (chat-edge-api/internal/auth/portal_jwt.go), which tolerates a missing
    // token_use (legacy access tokens) but rejects any other value.
    if (payload.token_use !== undefined
        && payload.token_use !== ''
        && payload.token_use !== 'access') return null;
    const now = nowSeconds !== undefined ? nowSeconds : Math.floor(Date.now() / 1000);
    if (typeof payload.exp !== 'number' || payload.exp <= now) return null;
    if (!payload.sub) return null;
    return payload;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Per-user child scoping (Phase 10 C1)
// ---------------------------------------------------------------------------

/**
 * Map a portal `sub` claim onto a single, traversal-safe directory name.
 * Clean ids (uuid-ish) pass through unchanged; anything else — path
 * separators, dots-only, unicode — is replaced by a deterministic hash so
 * two different subs can never collide onto the same directory.
 */
function sanitizeSubDir(sub) {
  const s = String(sub ?? '');
  if (/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(s)) return s;
  return 'u-' + crypto.createHash('sha256').update(s).digest('hex').slice(0, 32);
}

/**
 * Compute the per-user spawn plan ({args, env, dirs}) for a server.
 * Servers without a `user_scope` (oauth2 connectors — already isolated by
 * their per-user provider token) keep their static args.
 */
function userChildConfig(server, sub, workspaceBase = WORKSPACE_BASE) {
  if (typeof server.user_scope !== 'function') {
    return { args: server.args, env: {}, dirs: [] };
  }
  return server.user_scope(`${workspaceBase}/${sanitizeSubDir(sub)}`);
}

// ---------------------------------------------------------------------------
// Per-(user, connector) instance manager
// ---------------------------------------------------------------------------

/** Thrown when the backend refuses to hand out a provider token. */
class TokenFetchError extends Error {
  constructor(status, message) {
    super(message || `token fetch failed (${status})`);
    this.status = status;
  }
}

/**
 * LRU manager of per-(sub, connectorId) MCP children.
 *
 * Injectables (all optional — server.js passes the real ones):
 *   spawnChild(server, port, token) → child handle with .kill() and .on('exit')
 *   fetchToken(connectorId, authHeader) → {access_token, expires_at|null};
 *       throws TokenFetchError on backend 4xx
 *   now() → ms epoch;  idleMs (default 10 min);  maxChildren (default 20);
 *   basePort (default 3300)
 */
class InstanceManager {
  constructor(opts = {}) {
    this.spawnChild = opts.spawnChild;
    this.fetchToken = opts.fetchToken;
    this.now = opts.now || (() => Date.now());
    this.idleMs = opts.idleMs !== undefined ? opts.idleMs : 10 * 60 * 1000;
    this.maxChildren = opts.maxChildren !== undefined ? opts.maxChildren : 20;
    this.basePort = opts.basePort !== undefined ? opts.basePort : 3300;
    this.entries = new Map(); // key → {child, port, token, tokenExpiresAt, lastUsed}
    this.usedPorts = new Set();
    // C2: in-flight acquisitions, so concurrent callers for the same
    // (sub, connector) coalesce onto ONE spawn instead of leaking a child.
    this.pending = new Map(); // key → Promise<entry>
  }

  key(sub, connectorId) {
    return `${sub}|${connectorId}`;
  }

  /**
   * Get (or spawn) the child for this user+connector, refreshing the
   * provider token first when it is expired / within 60s of expiry.
   * none-auth (built-in) servers skip the broker fetch — the caller has
   * already verified the portal JWT — but are still per-user (C1).
   * Returns {port}. Throws TokenFetchError when the backend says no —
   * the caller maps that to a JSON-RPC error and we evict the child.
   */
  async acquire(server, sub, authHeader) {
    const key = this.key(sub, server.id);
    const existing = this.entries.get(key);
    const nowMs = this.now();

    if (existing && this.tokenFresh(existing, nowMs)) {
      existing.lastUsed = nowMs;
      return existing;
    }

    // C2: a concurrent caller already fetching/spawning for this key —
    // await ITS result instead of spawning a duplicate child (which used
    // to orphan the first child and leak its port forever).
    const pending = this.pending.get(key);
    if (pending) return pending;

    const promise = this.acquireSlow(server, key, sub, authHeader)
      .finally(() => this.pending.delete(key));
    this.pending.set(key, promise);
    return promise;
  }

  /** The await-y path of acquire(); only ever one in flight per key. */
  async acquireSlow(server, key, sub, authHeader) {
    let token = null;
    let tokenExpiresAt = Number.POSITIVE_INFINITY;

    if (server.auth_type !== 'none') {
      let tokenResp;
      try {
        tokenResp = await this.fetchToken(server.id, authHeader);
      } catch (err) {
        // Backend refused (expired + unrefreshable, disconnected, 401…):
        // a running child holds a dead token — kill it.
        this.evict(key);
        throw err;
      }
      token = tokenResp.access_token;
      tokenExpiresAt = tokenResp.expires_at
        ? Date.parse(tokenResp.expires_at)
        : Number.POSITIVE_INFINITY;
    }

    const nowMs = this.now();
    const existing = this.entries.get(key);
    if (existing) {
      if (existing.token === token) {
        // Same token, renewed lease.
        existing.tokenExpiresAt = tokenExpiresAt;
        existing.lastUsed = nowMs;
        return existing;
      }
      // Token rotated — the child's env is stale; replace it.
      this.evict(key);
    }

    this.enforceCap();
    const port = this.allocatePort();
    let child;
    try {
      child = this.spawnChild(server, port, token, sub);
    } catch (err) {
      this.usedPorts.delete(port); // don't leak the port on a failed spawn
      throw err;
    }
    const entry = { key, child, port, token, tokenExpiresAt, lastUsed: nowMs };
    if (child && typeof child.on === 'function') {
      child.on('exit', () => {
        // Crashed / finished — drop the entry so the next call respawns.
        if (this.entries.get(key) === entry) this.evictEntry(entry);
      });
    }
    this.entries.set(key, entry);
    return entry;
  }

  tokenFresh(entry, nowMs) {
    return entry.tokenExpiresAt - 60 * 1000 > nowMs;
  }

  allocatePort() {
    for (let p = this.basePort; p < this.basePort + this.maxChildren * 4; p++) {
      if (!this.usedPorts.has(p)) {
        this.usedPorts.add(p);
        return p;
      }
    }
    throw new Error('no free child ports');
  }

  enforceCap() {
    while (this.entries.size >= this.maxChildren) {
      let oldest = null;
      for (const entry of this.entries.values()) {
        if (!oldest || entry.lastUsed < oldest.lastUsed) oldest = entry;
      }
      if (!oldest) break;
      this.evictEntry(oldest);
    }
  }

  /** Kill children idle longer than idleMs. Called on a timer. */
  sweep() {
    const cutoff = this.now() - this.idleMs;
    for (const entry of [...this.entries.values()]) {
      if (entry.lastUsed < cutoff) this.evictEntry(entry);
    }
  }

  evict(key) {
    const entry = this.entries.get(key);
    if (entry) this.evictEntry(entry);
  }

  evictEntry(entry) {
    this.entries.delete(entry.key);
    this.usedPorts.delete(entry.port);
    try {
      if (entry.child && typeof entry.child.kill === 'function') entry.child.kill();
    } catch {
      // best-effort
    }
  }

  dispose() {
    for (const entry of [...this.entries.values()]) this.evictEntry(entry);
  }
}

module.exports = {
  SERVERS,
  forModels,
  internalTokenOk,
  WORKSPACE_BASE,
  buildCatalog,
  verifyPortalJwt,
  sanitizeSubDir,
  userChildConfig,
  InstanceManager,
  TokenFetchError
};
