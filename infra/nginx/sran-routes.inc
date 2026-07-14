# Shared routing rules — included by both the :80 and :443 server blocks.

client_max_body_size 100M;  # file uploads (chat attachments, KB documents)

# ── chat-edge health alias ──
# chat-edge serves GET /health at its root; the desktop app probes
# {base}/api/v1/health in single-URL mode, so map it explicitly.
location = /api/v1/health {
    proxy_pass http://chat_edge/health;
    proxy_set_header Host $host;
}

# ── Chat Edge API ──
location /api/v1/ {
    proxy_pass http://chat_edge/api/v1/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE streaming: no buffering, long read timeout
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
    proxy_set_header Connection '';
    chunked_transfer_encoding off;
}

# ── Gateway (auth + completions + web search) ──
location /v1/ {
    proxy_pass http://gateway_up/v1/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;  # agent loops run long
    proxy_set_header Connection '';
}

# ── Gateway portal auth (desktop login: /portal/auth/*) ──
location /portal/auth/ {
    proxy_pass http://gateway_up/portal/auth/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# ── Gateway portal API (agent key provisioning: /portal/projects/*) ──
location /portal/projects/ {
    proxy_pass http://gateway_up/portal/projects/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# ── Gateway health/metrics ──
location /__gw/ {
    proxy_pass http://gateway_up/__gw/;
    proxy_set_header Host $host;
}

# ── MCP catalog ──
location = /mcp/catalog.json {
    proxy_pass http://mcp_catalog/catalog.json;
    proxy_set_header Host $host;
}

location = /mcp/health {
    proxy_pass http://mcp_catalog/health;
    proxy_set_header Host $host;
}

# ── MCP servers (streamable HTTP) ──
# Phase 10 C1: ALL servers — built-in filesystem/memory AND oauth2
# connectors — go through the mcp-gateway's JWT-verified per-user proxy on
# :3200. The old per-port locations (:3201/:3202) bypassed authentication
# entirely and let any internet client drive a filesystem server shared
# across every user.
location /mcp/servers/ {
    proxy_pass http://mcp_catalog/mcp/servers/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_set_header Connection '';
}

# ── Admin UI ──
location /admin/ {
    proxy_pass http://admin_up/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# ── Desktop releases (installer + update manifest, R11 B4) ──
location /desktop/ {
    alias /var/www/releases/;
    autoindex off;
    types { application/json json; application/octet-stream exe; }
}

# ── Default → Portal (landing, login pages, OAuth) ──
location / {
    proxy_pass http://portal_up/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    # WebSocket upgrade (Next.js dev HMR; harmless in prod)
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
