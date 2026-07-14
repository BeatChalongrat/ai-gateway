# SRAN Desktop — Round 11: Production Deployment for Customers

## Goal

Make the entire SRAN stack production-ready for customer deployment. This round has **two tracks**: desktop app changes (Fable) and infrastructure consolidation (compose + ops).

---

## Track A — Desktop App: Auto-Update + Production Config

### A1. Auto-Update System

The app currently has NO update mechanism. Customers will be stuck on old versions forever. Build a complete auto-update system modeled after how professional desktop apps (VS Code, Slack, Discord) handle it.

#### A1a. Update manifest endpoint

The app will check a JSON manifest hosted on your server (or GitHub Releases):

```
GET https://releases.sran.ai/desktop/latest.json
```

Response:
```json
{
  "version": "1.2.0",
  "releaseDate": "2026-07-15T00:00:00Z",
  "downloadUrl": "https://releases.sran.ai/desktop/SranDesktop-1.2.0-Setup.exe",
  "sha256": "abc123...",
  "releaseNotes": "- MCP connector improvements\n- Thai voice quality upgrade",
  "minVersion": "1.0.0",
  "mandatory": false
}
```

For development / self-hosted: the URL is configurable via `appsettings.json`:
```json
{
  "Sran": {
    "UpdateManifestUrl": "https://releases.sran.ai/desktop/latest.json"
  }
}
```

#### A1b. Create `Application/Services/UpdateService.cs`

```csharp
public interface IUpdateService
{
    Task<UpdateCheckResult?> CheckForUpdateAsync(CancellationToken ct = default);
    Task<string> DownloadUpdateAsync(UpdateCheckResult update, IProgress<double>? progress = null, CancellationToken ct = default);
    void LaunchInstallerAndExit(string installerPath);
}

public record UpdateCheckResult(
    string NewVersion,
    string CurrentVersion,
    string DownloadUrl,
    string Sha256,
    string? ReleaseNotes,
    bool IsMandatory);
```

Implementation:
```csharp
public class UpdateService : IUpdateService
{
    private readonly HttpClient _http;
    private readonly string _manifestUrl;
    private readonly string _currentVersion;
    private static readonly string DownloadDir =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SRAN Desktop", "updates");

    public UpdateService(IConfiguration config, IHttpClientFactory httpFactory)
    {
        _http = httpFactory.CreateClient();
        _manifestUrl = config["Sran:UpdateManifestUrl"]
            ?? "https://releases.sran.ai/desktop/latest.json";
        _currentVersion = typeof(UpdateService).Assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
            ?? "0.0.0";
    }

    public async Task<UpdateCheckResult?> CheckForUpdateAsync(CancellationToken ct)
    {
        try
        {
            var json = await _http.GetStringAsync(_manifestUrl, ct);
            var manifest = JsonSerializer.Deserialize<UpdateManifest>(json,
                new JsonSerializerOptions { PropertyNameHandling = JsonNamingPolicy.CamelCase });
            if (manifest is null) return null;

            var current = Version.Parse(_currentVersion.Split('-')[0]);
            var latest = Version.Parse(manifest.Version);

            if (latest <= current) return null;

            return new UpdateCheckResult(
                manifest.Version, _currentVersion,
                manifest.DownloadUrl, manifest.Sha256,
                manifest.ReleaseNotes,
                manifest.Mandatory || current < Version.Parse(manifest.MinVersion));
        }
        catch (Exception ex)
        {
            Serilog.Log.Warning(ex, "Update check failed");
            return null;
        }
    }

    public async Task<string> DownloadUpdateAsync(
        UpdateCheckResult update, IProgress<double>? progress, CancellationToken ct)
    {
        Directory.CreateDirectory(DownloadDir);
        var fileName = $"SranDesktop-{update.NewVersion}-Setup.exe";
        var filePath = Path.Combine(DownloadDir, fileName);

        // Skip download if already downloaded and hash matches
        if (File.Exists(filePath) && VerifyHash(filePath, update.Sha256))
            return filePath;

        using var response = await _http.GetAsync(update.DownloadUrl,
            HttpCompletionOption.ResponseHeadersRead, ct);
        response.EnsureSuccessStatusCode();

        var totalBytes = response.Content.Headers.ContentLength ?? -1;
        await using var contentStream = await response.Content.ReadAsStreamAsync(ct);
        await using var fileStream = new FileStream(filePath, FileMode.Create,
            FileAccess.Write, FileShare.None, 8192, true);

        var buffer = new byte[81920];
        long downloaded = 0;
        int bytesRead;

        while ((bytesRead = await contentStream.ReadAsync(buffer, ct)) > 0)
        {
            await fileStream.WriteAsync(buffer.AsMemory(0, bytesRead), ct);
            downloaded += bytesRead;
            if (totalBytes > 0)
                progress?.Report((double)downloaded / totalBytes);
        }

        // Verify SHA-256
        if (!VerifyHash(filePath, update.Sha256))
        {
            File.Delete(filePath);
            throw new InvalidOperationException("Downloaded file hash mismatch — possible corruption or tampering.");
        }

        return filePath;
    }

    public void LaunchInstallerAndExit(string installerPath)
    {
        // /SILENT = no wizard UI, /CLOSEAPPLICATIONS = kill running instance
        Process.Start(new ProcessStartInfo
        {
            FileName = installerPath,
            Arguments = "/SILENT /CLOSEAPPLICATIONS",
            UseShellExecute = true
        });
        System.Windows.Application.Current.Shutdown();
    }

    private static bool VerifyHash(string path, string expectedSha256)
    {
        if (string.IsNullOrEmpty(expectedSha256)) return true; // skip if not provided
        using var sha256 = System.Security.Cryptography.SHA256.Create();
        using var stream = File.OpenRead(path);
        var hash = BitConverter.ToString(sha256.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        return hash == expectedSha256.ToLowerInvariant();
    }
}
```

#### A1c. Background update check

In `App.xaml.cs`, after the main window loads, start a periodic check:

```csharp
// After MainWindow.Show() in OnStartup:
_ = Task.Run(async () =>
{
    // Wait 30 seconds after startup, then check
    await Task.Delay(TimeSpan.FromSeconds(30));
    var updateService = _host.Services.GetRequiredService<IUpdateService>();
    var result = await updateService.CheckForUpdateAsync();
    if (result is not null)
    {
        await Dispatcher.InvokeAsync(() =>
            MainViewModel.ShowUpdateNotification(result));
    }
});
```

Also check every 6 hours while app is running (add a timer).

#### A1d. Update notification UI

Add an update banner to `MainWindow.xaml` at the top (above sidebar + chat area):

```xml
<!-- Update notification bar -->
<Border Grid.Row="0" Grid.ColumnSpan="2"
        Background="{DynamicResource AccentBrush}" Padding="12,8"
        Visibility="{Binding HasUpdate, Converter={StaticResource BoolToVisibility}}">
    <DockPanel>
        <Button DockPanel.Dock="Right" Content="{loc:T update.install}"
                Style="{DynamicResource FlatRowButton}"
                Foreground="White" BorderBrush="White"
                Command="{Binding InstallUpdateCommand}" Margin="12,0,0,0" />
        <Button DockPanel.Dock="Right" Content="{loc:T update.later}"
                Style="{DynamicResource FlatRowButton}"
                Foreground="White" Opacity="0.7"
                Command="{Binding DismissUpdateCommand}" />
        <TextBlock VerticalAlignment="Center" Foreground="White">
            <Run Text="{loc:T update.available}" />
            <Run Text="{Binding UpdateVersion}" FontWeight="SemiBold" />
            <Run Text="—" />
            <Run Text="{Binding UpdateNotes}" />
        </TextBlock>
    </DockPanel>
</Border>
```

For mandatory updates: disable the "Later" button, show a modal dialog instead.

#### A1e. Download progress

When the user clicks "Install", show download progress in the same banner:

```xml
<ProgressBar Height="3" Value="{Binding DownloadProgress}"
             Maximum="1" Minimum="0"
             Visibility="{Binding IsDownloading, Converter={StaticResource BoolToVisibility}}" />
```

Flow: Click Install → download with progress → verify hash → launch installer → app exits → Inno Setup upgrades in place → app restarts.

#### A1f. Version display

Show current version in the settings popup footer:

```xml
<TextBlock Text="{Binding AppVersion}" FontSize="10.5"
           Foreground="{DynamicResource TextTertiaryBrush}"
           Margin="16,8" HorizontalAlignment="Center" />
```

Read from assembly: `Assembly.GetExecutingAssembly().GetName().Version.ToString(3)`

---

### A2. Production Server Configuration UX

The `FirstRunDialog` already asks for server URLs. Improve it for customers:

#### A2a. Server URL presets

Instead of showing raw URL fields (intimidating for non-technical users), offer presets:

```xml
<!-- FirstRunDialog improvements -->
<StackPanel Margin="24,16">
    <TextBlock Text="{loc:T setup.serverTitle}" FontSize="16" FontWeight="SemiBold" />
    <TextBlock Text="{loc:T setup.serverDesc}" FontSize="12.5"
               Foreground="{DynamicResource TextSecondaryBrush}" Margin="0,4,0,16" />

    <!-- Preset buttons -->
    <RadioButton GroupName="Server" IsChecked="{Binding IsCloudSelected}"
                 Content="{loc:T setup.cloud}" Margin="0,4" />
    <Border Margin="24,0,0,8" Padding="8" CornerRadius="6"
            Background="{DynamicResource BgSecondaryBrush}"
            Visibility="{Binding IsCloudSelected, Converter={StaticResource BoolToVisibility}}">
        <TextBlock Text="sran.ai (managed service)" FontSize="11"
                   Foreground="{DynamicResource TextTertiaryBrush}" />
    </Border>

    <RadioButton GroupName="Server" IsChecked="{Binding IsSelfHostedSelected}"
                 Content="{loc:T setup.selfHosted}" Margin="0,4" />
    <StackPanel Margin="24,0,0,8"
                Visibility="{Binding IsSelfHostedSelected, Converter={StaticResource BoolToVisibility}}">
        <TextBlock Text="{loc:T setup.serverUrl}" FontSize="11" Margin="0,0,0,4" />
        <TextBox Text="{Binding ServerBaseUrl, UpdateSourceTrigger=PropertyChanged}"
                 PlaceholderText="https://gateway.yourcompany.com" />
        <TextBlock Text="{loc:T setup.serverUrlHint}" FontSize="10.5"
                   Foreground="{DynamicResource TextTertiaryBrush}" Margin="0,2,0,0" />
    </StackPanel>
</StackPanel>
```

#### A2b. Single URL mode

For self-hosted: let customers enter **one** base URL. Derive both endpoints from it:
- If user enters `https://sran.yourcompany.com`:
  - `ChatEdgeBaseUrl` = `https://sran.yourcompany.com` (nginx routes `/api/v1/` → chat-edge-api:4000)
  - `GatewayBaseUrl` = `https://sran.yourcompany.com` (nginx routes `/v1/` → gateway:8080)

This means the nginx config needs path-based routing (see Track B).

#### A2c. Connection test

Add a "Test Connection" button that:
1. Calls `GET {baseUrl}/__gw/health` (gateway health)
2. Calls `GET {baseUrl}/api/v1/health` (chat-edge health)
3. Shows green checkmark or red error with message
4. Only enables "Continue" when both pass

```csharp
[RelayCommand]
private async Task TestConnectionAsync()
{
    IsTestingConnection = true;
    ConnectionStatus = "Testing...";
    try
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
        var gwResponse = await http.GetAsync($"{ServerBaseUrl}/__gw/health");
        var chatResponse = await http.GetAsync($"{ServerBaseUrl}/api/v1/health");

        if (gwResponse.IsSuccessStatusCode && chatResponse.IsSuccessStatusCode)
        {
            ConnectionStatus = "Connected ✓";
            IsConnectionValid = true;
        }
        else
        {
            ConnectionStatus = $"Error: Gateway={gwResponse.StatusCode}, Chat={chatResponse.StatusCode}";
            IsConnectionValid = false;
        }
    }
    catch (Exception ex)
    {
        ConnectionStatus = $"Cannot reach server: {ex.Message}";
        IsConnectionValid = false;
    }
    finally { IsTestingConnection = false; }
}
```

---

### A3. Error Reporting / Crash Telemetry

Customers won't send you logs manually. Add opt-in crash reporting.

#### A3a. Global exception handler

In `App.xaml.cs`:
```csharp
protected override void OnStartup(StartupEventArgs e)
{
    // Catch unhandled UI thread exceptions
    DispatcherUnhandledException += (s, args) =>
    {
        Log.Fatal(args.Exception, "Unhandled UI exception");
        ReportCrashAsync(args.Exception).ConfigureAwait(false);
        args.Handled = true; // prevent crash, show dialog
        ShowCrashDialog(args.Exception);
    };

    // Catch unhandled background thread exceptions
    AppDomain.CurrentDomain.UnhandledException += (s, args) =>
    {
        if (args.ExceptionObject is Exception ex)
        {
            Log.Fatal(ex, "Unhandled domain exception");
            ReportCrashAsync(ex).ConfigureAwait(false);
        }
    };

    // Catch unobserved task exceptions
    TaskScheduler.UnobservedTaskException += (s, args) =>
    {
        Log.Error(args.Exception, "Unobserved task exception");
        args.SetObserved(); // don't crash
    };
}
```

#### A3b. Crash report service

```csharp
public class CrashReportService
{
    private readonly string _endpoint;  // e.g., https://sran.ai/api/crash-reports
    private readonly string _appVersion;
    private readonly HttpClient _http;

    public async Task ReportAsync(Exception ex)
    {
        if (!_settingsService.IsCrashReportingEnabled) return;

        var report = new
        {
            version = _appVersion,
            os = Environment.OSVersion.ToString(),
            dotnet = Environment.Version.ToString(),
            exception = ex.GetType().Name,
            message = ex.Message,
            stackTrace = ex.StackTrace,
            innerException = ex.InnerException?.Message,
            timestamp = DateTimeOffset.UtcNow
        };

        try
        {
            var json = JsonSerializer.Serialize(report);
            await _http.PostAsync(_endpoint,
                new StringContent(json, Encoding.UTF8, "application/json"));
        }
        catch { /* best effort — don't crash the crash reporter */ }
    }
}
```

#### A3c. Opt-in toggle

Add to settings: "Send crash reports to help improve SRAN Desktop" toggle. Default OFF. Respect user choice.

#### A3d. Crash dialog

When an unhandled exception occurs, show a dialog:
```
Something went wrong.

[Error details collapsed]

[Send crash report]  [Restart app]  [Close]
```

---

### A4. i18n Keys for Round 11

```csharp
// Update
["update.available"] = "A new version is available:" / "มีเวอร์ชันใหม่:",
["update.install"] = "Install now" / "ติดตั้งเลย",
["update.later"] = "Later" / "ภายหลัง",
["update.downloading"] = "Downloading update..." / "กำลังดาวน์โหลด...",
["update.mandatory"] = "This update is required" / "อัพเดทนี้จำเป็นต้องติดตั้ง",
["update.restart"] = "Restart to update" / "รีสตาร์ทเพื่ออัพเดท",

// Setup
["setup.serverTitle"] = "Server connection" / "เชื่อมต่อเซิร์ฟเวอร์",
["setup.serverDesc"] = "Choose how to connect to SRAN AI" / "เลือกวิธีเชื่อมต่อกับ SRAN AI",
["setup.cloud"] = "SRAN Cloud (managed)" / "SRAN Cloud (บริการสำเร็จรูป)",
["setup.selfHosted"] = "Self-hosted server" / "เซิร์ฟเวอร์ขององค์กร",
["setup.serverUrl"] = "Server URL" / "URL เซิร์ฟเวอร์",
["setup.serverUrlHint"] = "Enter the URL provided by your admin" / "ใส่ URL ที่ผู้ดูแลระบบให้มา",
["setup.testConnection"] = "Test connection" / "ทดสอบการเชื่อมต่อ",
["setup.connected"] = "Connected" / "เชื่อมต่อสำเร็จ",

// Crash
["crash.title"] = "Something went wrong" / "เกิดข้อผิดพลาด",
["crash.sendReport"] = "Send crash report" / "ส่งรายงานข้อผิดพลาด",
["crash.restart"] = "Restart" / "รีสตาร์ท",

// Version
["settings.version"] = "Version" / "เวอร์ชัน",
```

---

## Track B — Infrastructure: Unified Production Stack

### B1. Merge chat-edge-api into main docker-compose.yml

Currently chat-edge-api lives in `docker-compose.webchat.yml` with its own postgres + redis. For production: merge into the main stack sharing the same postgres + redis.

Add to `docker-compose.yml` **after the `gateway` service** (around line 281):

```yaml
  # Chat Edge API — conversations, messages, memory, KB, file storage,
  # web search, share links. The desktop app and web chat both talk to
  # this service. Shares the main postgres (separate database) and redis.
  chat-edge-api:
    build:
      context: ./services/chat-edge-api
      dockerfile: Dockerfile
    environment:
      # Use main postgres with a dedicated database.
      # Migration: chat-edge-api auto-applies on startup (initdb.d hook
      # no longer needed — the Go binary runs migrations internally).
      DATABASE_URL: postgres://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-postgres}@postgres:5432/${CHAT_EDGE_DB:-sran_chat}?sslmode=disable
      # Shared redis, different DB number (main stack uses db 0)
      REDIS_URL: redis://:${REDIS_PASSWORD:?}@redis:6379/2
      # Gateway is on the same compose network
      GATEWAY_API_URL: http://gateway:8080/v1
      GATEWAY_API_KEY: ${GATEWAY_API_KEY:-}
      INTERNAL_SERVICE_SECRET: ${INTERNAL_SERVICE_SECRET:-}
      SERVICE_JWT_SECRET: ${SERVICE_JWT_SECRET:?SERVICE_JWT_SECRET must be set}
      USER_JWT_SECRET: ${USER_JWT_SECRET:?USER_JWT_SECRET must be set}
      CSRF_SECRET: ${CSRF_SECRET:?CSRF_SECRET must be set}
      PORTAL_JWT_SECRET: ${PORTAL_JWT_SECRET:?PORTAL_JWT_SECRET must be set}
      PORT: "4000"
      CHAT_EDGE_ADMIN_API_KEY: ${CHAT_EDGE_ADMIN_API_KEY:?CHAT_EDGE_ADMIN_API_KEY must be set}
      # CORS: desktop app doesn't need CORS (native HTTP), web chat does.
      CORS_ALLOW_ORIGINS: ${CHAT_CORS_ORIGINS:-http://localhost:3002}
      MAX_CONTEXT_MESSAGES: ${MAX_CONTEXT_MESSAGES:-1000}
      # File storage: S3 for prod, local for dev
      STORAGE_BACKEND: ${STORAGE_BACKEND:-local}
      S3_BUCKET: ${S3_BUCKET:-sran-chat-files}
      S3_REGION: ${S3_REGION:-us-east-1}
      S3_ENDPOINT: ${S3_ENDPOINT:-}
      S3_ACCESS_KEY: ${S3_ACCESS_KEY:-}
      S3_SECRET_KEY: ${S3_SECRET_KEY:-}
      S3_FORCE_PATH_STYLE: ${S3_FORCE_PATH_STYLE:-true}
      IMAGE_GEN_MODEL: ${IMAGE_GEN_MODEL:-gpt-image-1}
      CHAT_EDGE_IMAGE_TIMEOUT_SECONDS: ${CHAT_EDGE_IMAGE_TIMEOUT_SECONDS:-180}
    volumes:
      - chat_edge_uploads:/app/data/uploads
    ports:
      - "4000:4000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      gateway:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://localhost:4000/api/v1/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
    restart: unless-stopped
```

Add `chat_edge_uploads:` to the `volumes:` section at the bottom.

Add to `docker-compose.prod.yml`:
```yaml
  chat-edge-api:
    environment:
      CHAT_EDGE_DEV: ""  # disable dev mode
      CORS_ALLOW_ORIGINS: ${CHAT_CORS_ORIGINS:?CHAT_CORS_ORIGINS must be set for prod}
    ports: !override
      - "127.0.0.1:4000:4000"
    restart: always
```

Create the chat-edge database in postgres init:
```yaml
  postgres:
    environment:
      # Create both databases on first run
      POSTGRES_MULTIPLE_DATABASES: ${POSTGRES_DB:-ai_gateway},${CHAT_EDGE_DB:-sran_chat}
```

Or simpler: add an init script. Create `infra/postgres/init-chat-edge-db.sh`:
```bash
#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE sran_chat'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sran_chat')\gexec
    GRANT ALL PRIVILEGES ON DATABASE sran_chat TO $POSTGRES_USER;
EOSQL
```

Mount it:
```yaml
  postgres:
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infra/postgres/init-chat-edge-db.sh:/docker-entrypoint-initdb.d/02-chat-edge.sh:ro
```

Use `pgvector/pgvector:pg16` image instead of `postgres:16` (chat-edge needs pgvector for KB embeddings).

---

### B2. MCP Server Service

Add an MCP gateway service that bundles several MCP servers behind a single HTTP endpoint.

#### B2a. Create `services/mcp-gateway/`

Create a lightweight Node.js service that hosts multiple MCP servers:

`services/mcp-gateway/Dockerfile`:
```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --production
COPY . .
EXPOSE 3200
CMD ["node", "server.js"]
```

`services/mcp-gateway/package.json`:
```json
{
  "name": "sran-mcp-gateway",
  "version": "1.0.0",
  "dependencies": {
    "@modelcontextprotocol/server-filesystem": "^2.0.0",
    "@modelcontextprotocol/server-fetch": "^2.0.0",
    "supergateway": "^2.0.0",
    "express": "^4.18.0"
  }
}
```

`services/mcp-gateway/server.js`:
```javascript
const express = require('express');
const { spawn } = require('child_process');
const app = express();

// Each MCP server runs as a child process on stdio,
// proxied to HTTP via supergateway on a sub-path.
const SERVERS = [
  {
    id: 'filesystem',
    name: 'File System',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/data/workspace'],
    port: 3201
  },
  {
    id: 'fetch',
    name: 'Web Fetch',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-fetch'],
    port: 3202
  }
  // Add more as needed
];

// Start each server with supergateway
for (const server of SERVERS) {
  spawn('npx', ['-y', 'supergateway', '--port', String(server.port), '--', server.command, ...server.args], {
    stdio: 'inherit',
    env: { ...process.env, PATH: process.env.PATH }
  });
}

// Catalog endpoint — desktop app reads this to know available connectors
app.get('/catalog.json', (req, res) => {
  res.json({
    connectors: SERVERS.map(s => ({
      id: s.id,
      name: s.name,
      serverUrl: `http://mcp-gateway:${s.port}`,
      category: 'built-in',
      authType: 'none'
    }))
  });
});

// Health check
app.get('/health', (req, res) => res.json({ status: 'ok' }));

app.listen(3200, () => console.log('MCP Gateway catalog on :3200'));
```

#### B2b. Add to docker-compose.yml

```yaml
  # MCP Gateway — hosts multiple MCP servers behind a single service.
  # Desktop app reads the catalog at :3200/catalog.json and connects
  # to individual servers on their assigned ports.
  mcp-gateway:
    build:
      context: ./services/mcp-gateway
    ports:
      - "3200:3200"
      - "3201:3201"  # filesystem
      - "3202:3202"  # fetch
    volumes:
      - mcp_workspace:/data/workspace
    restart: unless-stopped
```

Add `mcp_workspace:` to volumes.

Add to `docker-compose.prod.yml`:
```yaml
  mcp-gateway:
    ports: !override
      - "127.0.0.1:3200:3200"
      - "127.0.0.1:3201:3201"
      - "127.0.0.1:3202:3202"
    restart: always
```

#### B2c. Desktop app config

Set the catalog URL in `appsettings.json`:
```json
{
  "Sran": {
    "McpCatalogUrl": "http://localhost:3200/catalog.json"
  }
}
```

For production (single URL mode from A2b), the catalog would be at `https://sran.yourcompany.com/mcp/catalog.json` — nginx routes to mcp-gateway.

---

### B3. Nginx Unified Reverse Proxy

The desktop app should connect to **one** URL. Nginx routes by path:

Create `infra/nginx/sran-desktop.conf`:

```nginx
# SRAN Desktop production reverse proxy
# All services behind one domain + TLS.
#
# Desktop app connects to: https://sran.yourcompany.com
# Path routing:
#   /api/v1/*          → chat-edge-api:4000    (conversations, messages, KB, memory, share)
#   /v1/*              → gateway:8080          (auth, completions, agent keys)
#   /__gw/*            → gateway:8080          (health, metrics)
#   /portal/*          → portal:3001           (user login/registration)
#   /mcp/*             → mcp-gateway:3200+     (MCP server catalog + endpoints)
#   /admin/*           → admin-ui:3000         (admin panel)
#   /                  → portal:3001           (landing page)

upstream chat_edge {
    server chat-edge-api:4000;
    keepalive 32;
}

upstream gateway {
    server gateway:8080;
    keepalive 32;
}

upstream portal {
    server portal:3001;
}

upstream admin {
    server admin-ui:3000;
}

upstream mcp_catalog {
    server mcp-gateway:3200;
}

server {
    listen 80;
    server_name _;

    # Redirect HTTP → HTTPS in production
    # Uncomment when TLS is configured:
    # return 301 https://$host$request_uri;

    # For development without TLS:
    include /etc/nginx/conf.d/sran-routes.conf;
}

server {
    listen 443 ssl http2;
    server_name _;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    include /etc/nginx/conf.d/sran-routes.conf;
}
```

Create `infra/nginx/sran-routes.conf`:

```nginx
# Shared routing rules (included by both HTTP and HTTPS server blocks)

client_max_body_size 100M;  # file uploads

# ── Chat Edge API ──
location /api/v1/ {
    proxy_pass http://chat_edge/api/v1/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE streaming: disable buffering
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;  # long-running SSE streams
    proxy_set_header Connection '';
    chunked_transfer_encoding off;
}

# ── Gateway (auth + completions) ──
location /v1/ {
    proxy_pass http://gateway/v1/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE streaming for completions
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;  # agent loops can be long
    proxy_set_header Connection '';
}

# ── Gateway health/metrics ──
location /__gw/ {
    proxy_pass http://gateway/__gw/;
    proxy_set_header Host $host;
}

# ── Portal (login + OAuth) ──
location /portal/ {
    proxy_pass http://portal/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # WebSocket support for Next.js HMR (dev only)
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}

# ── MCP Catalog ──
location /mcp/ {
    proxy_pass http://mcp_catalog/;
    proxy_set_header Host $host;
}

# ── MCP Servers (individual ports proxied by path) ──
location /mcp/servers/filesystem/ {
    proxy_pass http://mcp-gateway:3201/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Connection '';
}

location /mcp/servers/fetch/ {
    proxy_pass http://mcp-gateway:3202/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Connection '';
}

# ── Admin UI ──
location /admin/ {
    proxy_pass http://admin/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

# ── Update manifest (static file) ──
location /desktop/ {
    alias /var/www/releases/;
    autoindex off;
}

# ── Default → Portal landing ──
location / {
    proxy_pass http://portal/;
    proxy_set_header Host $host;
}
```

Update the existing `infra/nginx/nginx.conf` or replace it. The existing `nginx` service in the scaling profile can be promoted to always-on:

Move `nginx` out of `profiles: ["scaling"]` and update its config mount:

```yaml
  nginx:
    # Remove: profiles: ["scaling"]
    image: nginx:1.27-alpine
    volumes:
      - ./infra/nginx/sran-desktop.conf:/etc/nginx/conf.d/default.conf:ro
      - ./infra/nginx/sran-routes.conf:/etc/nginx/conf.d/sran-routes.conf:ro
      - ./infra/nginx/certs:/etc/nginx/certs:ro
      - ./releases:/var/www/releases:ro  # desktop installer downloads
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - gateway
      - chat-edge-api
      - portal
    restart: unless-stopped
```

---

### B4. Release Hosting

For the auto-update system, host installer files on the same server:

Create `releases/` directory in the project root:

```
releases/
  latest.json                          ← update manifest
  SranDesktop-1.0.0-Setup.exe          ← installer
```

Nginx serves this at `/desktop/latest.json` (see route config above).

#### B4a. Release script

Create `scripts/release-desktop.sh`:

```bash
#!/bin/bash
set -e

VERSION=$1
if [ -z "$VERSION" ]; then
    echo "Usage: ./scripts/release-desktop.sh 1.2.0"
    exit 1
fi

INSTALLER="SranDesktop/installer/SranDesktop-${VERSION}-Setup.exe"

if [ ! -f "$INSTALLER" ]; then
    echo "ERROR: Installer not found at $INSTALLER"
    echo "Run first: dotnet publish ... && ISCC.exe build/SranDesktop.iss"
    exit 1
fi

# Compute SHA-256
SHA256=$(sha256sum "$INSTALLER" | cut -d' ' -f1)

# Copy installer to releases
cp "$INSTALLER" "releases/SranDesktop-${VERSION}-Setup.exe"

# Update manifest
cat > releases/latest.json <<EOF
{
  "version": "${VERSION}",
  "releaseDate": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "downloadUrl": "/desktop/SranDesktop-${VERSION}-Setup.exe",
  "sha256": "${SHA256}",
  "releaseNotes": "",
  "minVersion": "1.0.0",
  "mandatory": false
}
EOF

echo "Release ${VERSION} ready:"
echo "  Installer: releases/SranDesktop-${VERSION}-Setup.exe"
echo "  Manifest:  releases/latest.json"
echo "  SHA-256:   ${SHA256}"
echo ""
echo "Edit releases/latest.json to add release notes, then deploy."
```

---

### B5. Production .env Template

Create `infra/production/.env.production.template`:

```bash
# ══════════════════════════════════════════════════════════════
# SRAN AI Gateway — Production Environment Variables
# Copy to .env and fill in ALL values marked REQUIRED.
# ══════════════════════════════════════════════════════════════

# ── Postgres ──
POSTGRES_DB=ai_gateway
POSTGRES_USER=sran_admin
POSTGRES_PASSWORD=                    # REQUIRED: strong random password
CHAT_EDGE_DB=sran_chat

# ── Redis ──
REDIS_PASSWORD=                       # REQUIRED: strong random password

# ── Gateway ──
APP_ENV=production
CORS_ORIGINS=https://sran.yourcompany.com
PORTAL_PUBLIC_URL=https://sran.yourcompany.com

# ── Chat Edge API ──
SERVICE_JWT_SECRET=                    # REQUIRED: openssl rand -hex 32
USER_JWT_SECRET=                       # REQUIRED: openssl rand -hex 32
CSRF_SECRET=                           # REQUIRED: openssl rand -hex 32
PORTAL_JWT_SECRET=                     # REQUIRED: must match backend's portal_jwt_secret
CHAT_EDGE_ADMIN_API_KEY=              # REQUIRED: openssl rand -hex 32
CHAT_CORS_ORIGINS=https://sran.yourcompany.com
GATEWAY_API_KEY=                       # REQUIRED: must match a valid API key in the gateway

# ── File Storage ──
STORAGE_BACKEND=s3                     # Use S3/MinIO for production
S3_BUCKET=sran-chat-files
S3_REGION=ap-southeast-1
S3_ENDPOINT=                           # MinIO: http://minio:9000, AWS: leave empty
S3_ACCESS_KEY=                         # REQUIRED if using S3
S3_SECRET_KEY=                         # REQUIRED if using S3
S3_FORCE_PATH_STYLE=true               # true for MinIO, false for AWS

# ── Image Generation ──
IMAGE_GEN_MODEL=gpt-image-1

# ── Provider Keys (gateway) ──
PROVIDER_KEY_MASTER_KEY=              # REQUIRED: AES key for encrypting provider API keys

# ── Observability ──
GRAFANA_ADMIN_PASSWORD=               # REQUIRED: Grafana admin password
LOG_LEVEL=info

# ── Desktop App ──
# The desktop app connects to this base URL (set in appsettings.json or FirstRunDialog)
# SRAN_BASE_URL=https://sran.yourcompany.com

# ── TLS ──
# Place cert files in infra/nginx/certs/:
#   fullchain.pem   — certificate + intermediates
#   privkey.pem     — private key
# Use Let's Encrypt: certbot certonly --standalone -d sran.yourcompany.com
```

---

### B6. One-Command Deploy

Add to `Makefile`:

```makefile
# ── Desktop Production Deploy ────────────────────────────────
.PHONY: prod-desktop

prod-desktop: ## Deploy full stack with desktop support (chat-edge + MCP)
	@echo "Checking required env vars..."
	@test -f .env || (echo "ERROR: .env not found. Copy from infra/production/.env.production.template" && exit 1)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
	@echo ""
	@echo "Stack is up. Services:"
	@echo "  Gateway:       https://sran.yourcompany.com/v1/"
	@echo "  Chat Edge API: https://sran.yourcompany.com/api/v1/"
	@echo "  Portal:        https://sran.yourcompany.com/"
	@echo "  Admin:         https://sran.yourcompany.com/admin/"
	@echo "  MCP Catalog:   https://sran.yourcompany.com/mcp/catalog.json"
	@echo "  Grafana:       http://localhost:3100"
	@echo ""
	@echo "Desktop app: set server URL to https://sran.yourcompany.com"
```

---

## Verification Checklist

### Track A — Desktop App
- [ ] `UpdateService.CheckForUpdateAsync` fetches manifest and detects new version
- [ ] `UpdateService.DownloadUpdateAsync` downloads with progress and verifies SHA-256
- [ ] Hash mismatch → download deleted, error shown
- [ ] Update banner appears when update available
- [ ] Mandatory update blocks "Later" button
- [ ] Download progress bar fills correctly
- [ ] After download: launches installer, app exits cleanly
- [ ] Inno Setup `/SILENT` mode upgrades without user interaction
- [ ] App version displayed in settings footer
- [ ] FirstRunDialog shows Cloud / Self-hosted radio buttons
- [ ] Self-hosted mode: single URL field derives both endpoints
- [ ] "Test Connection" validates both health endpoints
- [ ] Continue button disabled until connection test passes
- [ ] Crash handler catches unhandled exceptions without crashing
- [ ] Crash report sent only when opt-in enabled
- [ ] Crash dialog shows with restart option
- [ ] All new i18n keys in Dict.cs (EN + TH)
- [ ] Build: 0 errors, 0 warnings

### Track B — Infrastructure
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` starts all services
- [ ] chat-edge-api starts and connects to shared postgres
- [ ] chat-edge-api health check passes: `GET /api/v1/health`
- [ ] `sran_chat` database auto-created by init script
- [ ] MCP gateway starts, catalog accessible at `:3200/catalog.json`
- [ ] MCP filesystem server responds to JSON-RPC at `:3201`
- [ ] Nginx routes `/api/v1/*` → chat-edge-api correctly
- [ ] Nginx routes `/v1/*` → gateway correctly
- [ ] Nginx routes `/mcp/*` → mcp-gateway correctly
- [ ] SSE streaming works through nginx (no buffering)
- [ ] `/desktop/latest.json` serves update manifest
- [ ] TLS works when certs are mounted (test with self-signed)
- [ ] `make prod-desktop` runs the full deploy
- [ ] `releases/latest.json` served correctly
- [ ] All prod ports bind 127.0.0.1 (not 0.0.0.0)
- [ ] `.env.production.template` has all required vars documented
