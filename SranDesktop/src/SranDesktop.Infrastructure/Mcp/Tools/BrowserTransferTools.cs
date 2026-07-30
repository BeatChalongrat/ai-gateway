using System.Security.Cryptography;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class BrowserUploadTool :
    IMcpTool,
    IPerceptualActionTool,
    ISensitiveDataTool
{
    internal const long MaximumTransferBytes = 512 * 1024;
    private static readonly IReadOnlyDictionary<string, string> ContentTypes =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            [".txt"] = "text/plain",
            [".csv"] = "text/csv",
            [".json"] = "application/json",
            [".png"] = "image/png",
            [".jpg"] = "image/jpeg",
            [".jpeg"] = "image/jpeg",
            [".pdf"] = "application/pdf",
            [".docx"] =
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            [".xlsx"] =
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            [".pptx"] =
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        };

    private readonly IBrowserBridgeService _bridge;
    private readonly IWorkspaceFileBroker _files;
    private readonly BrowserInteractionObserver _observer;

    public BrowserUploadTool(
        IBrowserBridgeService bridge,
        IWorkspaceFileBroker? files = null)
    {
        _bridge = bridge;
        _files = files ?? WorkspaceFileBroker.Shared;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_upload";
    public string Description =>
        "Select an authorized workspace file in a browser file input and verify the selected name and size.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "WorkspaceFile.ReadLocal",
        "Browser.UploadToCurrentPage",
        "workspace_file",
        "unknown",
        "current_browser_page",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: (int)MaximumTransferBytes);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
          "type": "object",
          "properties": {
            "path": { "type": "string", "description": "Authorized workspace file" },
            "ref": { "type": "integer", "description": "Current file-input ref" },
            "tab_id": { "type": "integer" },
            "observation_id": { "type": "string" },
            "dom_generation": { "type": "integer" },
            "expected_state": { "type": "string" }
          },
          "required": ["path", "ref", "tab_id", "observation_id", "dom_generation", "expected_state"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        var path = ToolHelpers.GetString(arguments, "path");
        if (string.IsNullOrWhiteSpace(path))
            return ToolResult.Fail("'path' is required.");
        if (!TryBoundReference(arguments, out var elementRef, out var tabId,
                out var observationId, out var domGeneration, out var error))
            return ToolResult.Fail(error);

        try
        {
            var snapshot = await _files.ReadFileAsync(
                path,
                MaximumTransferBytes,
                ct);
            var extension = Path.GetExtension(snapshot.Name);
            if (!ContentTypes.TryGetValue(extension, out var contentType))
                return ToolResult.Fail(
                    "UploadFileTypeDenied: this file extension is not allowed.");

            var result = await _bridge.RequestAsync(
                "page.upload",
                new
                {
                    @ref = elementRef,
                    tab_id = tabId,
                    observation_id = observationId,
                    dom_generation = domGeneration,
                    file_name = snapshot.Name,
                    content_type = contentType,
                    content_base64 = Convert.ToBase64String(snapshot.Bytes),
                    expected_state = ToolHelpers.GetString(
                        arguments,
                        "expected_state")
                },
                ct);
            var verified = result.TryGetProperty(
                               "upload_verified",
                               out var verification)
                           && verification.ValueKind == JsonValueKind.True;
            var selectedName = BrowserToolHelpers.GetString(
                result,
                "selected_file_name");
            var selectedSize = BrowserToolHelpers.GetLong(
                result,
                "selected_file_size");
            if (!verified
                || !string.Equals(
                    selectedName,
                    snapshot.Name,
                    StringComparison.Ordinal)
                || selectedSize != snapshot.Bytes.LongLength)
                return ToolResult.ExecutedUnverified(
                    "The browser received the upload selection but exact file metadata was not verified.",
                    "UploadSelectionNotVerified",
                    "Selected filename or size did not match the authorized workspace file.",
                    retryable: false);

            return ToolResult.Verified(
                $"Selected {snapshot.Name} ({snapshot.Bytes.LongLength} bytes) for upload.",
                [
                    "browser_transfer_verified=true",
                    $"upload_sha256={snapshot.Sha256}",
                    $"upload_length={snapshot.Bytes.LongLength}"
                ],
                ["external_file_selected=true"]);
        }
        catch (Exception ex) when (
            ex is BrowserBridgeException or BrowserNotConnectedException
                or TimeoutException)
        {
            // A consequential upload whose answer was lost may already have
            // attached the file — never report that as a clean no-effect failure.
            return BrowserToolHelpers.DispatchFailure(ex, mutating: true);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException
                or ArgumentException or NotSupportedException)
        {
            return ToolResult.Fail(ex.Message);
        }
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Upload,
            $"dom-ref:{BrowserToolHelpers.GetInt(arguments, "ref")}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.Consequential,
            Reversible: true,
            Idempotent: false,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(5),
            MaximumAttempts: 1);

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveAfterAsync(
            arguments,
            DescribeAction(arguments),
            before,
            ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);

    internal static bool TryBoundReference(
        JsonElement arguments,
        out int elementRef,
        out int tabId,
        out string observationId,
        out long domGeneration,
        out string error)
    {
        elementRef = BrowserToolHelpers.GetInt(arguments, "ref");
        tabId = BrowserToolHelpers.GetInt(arguments, "tab_id");
        observationId = ToolHelpers.GetString(arguments, "observation_id") ?? "";
        domGeneration = BrowserToolHelpers.GetLong(arguments, "dom_generation");
        error = "";
        if (elementRef < 0 || tabId <= 0 || string.IsNullOrWhiteSpace(observationId)
            || domGeneration <= 0)
        {
            error =
                "StaleDomReference: ref, tab_id, observation_id, and dom_generation are required.";
            return false;
        }
        return true;
    }
}

public sealed class BrowserDownloadTool : IMcpTool, IPerceptualActionTool
{
    private readonly IBrowserBridgeService _bridge;
    private readonly IWorkspaceFileBroker _files;
    private readonly BrowserInteractionObserver _observer;

    public BrowserDownloadTool(
        IBrowserBridgeService bridge,
        IWorkspaceFileBroker? files = null)
    {
        _bridge = bridge;
        _files = files ?? WorkspaceFileBroker.Shared;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_download";
    public string Description =>
        "Fetch a bound browser download and write verified bytes to an authorized workspace path.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
          "type": "object",
          "properties": {
            "destination_path": { "type": "string", "description": "Authorized workspace output path" },
            "expected_file_name": { "type": "string" },
            "ref": { "type": "integer", "description": "Current download-link ref" },
            "tab_id": { "type": "integer" },
            "observation_id": { "type": "string" },
            "dom_generation": { "type": "integer" }
          },
          "required": ["destination_path", "expected_file_name", "ref", "tab_id", "observation_id", "dom_generation"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        var destination = ToolHelpers.GetString(arguments, "destination_path");
        var expectedName = ToolHelpers.GetString(arguments, "expected_file_name");
        if (string.IsNullOrWhiteSpace(destination)
            || string.IsNullOrWhiteSpace(expectedName)
            || !string.Equals(
                Path.GetFileName(destination),
                expectedName,
                StringComparison.Ordinal))
            return ToolResult.Fail(
                "DownloadDestinationInvalid: destination filename must exactly match expected_file_name.");
        if (!BrowserUploadTool.TryBoundReference(
                arguments,
                out var elementRef,
                out var tabId,
                out var observationId,
                out var domGeneration,
                out var error))
            return ToolResult.Fail(error);

        try
        {
            var result = await _bridge.RequestAsync(
                "page.download",
                new
                {
                    @ref = elementRef,
                    tab_id = tabId,
                    observation_id = observationId,
                    dom_generation = domGeneration
                },
                ct);
            if (!result.TryGetProperty("download_verified", out var verified)
                || verified.ValueKind != JsonValueKind.True)
                return ToolResult.ExecutedUnverified(
                    "The browser did not return a completed download.",
                    "DownloadIncomplete",
                    "No completed browser transfer was observed.",
                    retryable: false);

            var browserName = BrowserToolHelpers.GetString(result, "file_name");
            if (!string.Equals(browserName, expectedName, StringComparison.Ordinal))
                return ToolResult.Fail(
                    "DownloadFileNameMismatch: browser filename did not match the authorized destination.");
            var encoded = BrowserToolHelpers.GetString(result, "content_base64");
            var bytes = Convert.FromBase64String(encoded);
            if (bytes.LongLength > BrowserUploadTool.MaximumTransferBytes
                || bytes.LongLength != BrowserToolHelpers.GetLong(result, "byte_count"))
                return ToolResult.Fail(
                    "DownloadSizeMismatch: completed byte count was invalid.");
            var browserHash = BrowserToolHelpers.GetString(result, "sha256");
            var computedHash = Convert.ToHexString(SHA256.HashData(bytes));
            if (!string.Equals(
                    browserHash,
                    computedHash,
                    StringComparison.OrdinalIgnoreCase))
                return ToolResult.Fail(
                    "DownloadHashMismatch: browser bytes failed SHA-256 validation.");

            var receipt = await _files.WriteBytesAsync(destination, bytes, ct);
            if (!string.Equals(
                    receipt.Sha256,
                    computedHash,
                    StringComparison.OrdinalIgnoreCase))
                return ToolResult.FailedEffect(
                    "DownloadWriteVerificationFailed",
                    "Workspace read-back hash did not match the browser transfer.",
                    retryable: false);

            return ToolResult.Verified(
                $"Downloaded and verified {expectedName} ({receipt.Length} bytes).",
                [
                    "browser_transfer_verified=true",
                    $"download_sha256={receipt.Sha256}",
                    $"download_length={receipt.Length}",
                    "download_trust=untrusted_external_content"
                ],
                [$"workspace_file_created={receipt.FullPath}"]);
        }
        catch (Exception ex) when (
            ex is BrowserBridgeException or BrowserNotConnectedException
                or TimeoutException)
        {
            return BrowserToolHelpers.DispatchFailure(ex, mutating: true);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException
                or ArgumentException or FormatException
                or NotSupportedException)
        {
            return ToolResult.Fail(ex.Message);
        }
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Download,
            $"dom-ref:{BrowserToolHelpers.GetInt(arguments, "ref")}",
            "",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: true,
            Idempotent: true,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(20),
            MaximumAttempts: 2);

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveAfterAsync(
            arguments,
            DescribeAction(arguments),
            before,
            ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);
}

