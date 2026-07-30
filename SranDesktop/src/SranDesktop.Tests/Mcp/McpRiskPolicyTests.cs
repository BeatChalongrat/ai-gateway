using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Risk classification and the retry rules that follow from it.
///
/// The central claim under test: a server's own annotation never buys it a
/// looser verdict. Everything else — the six classes, the conservative default,
/// what a disconnect permits — hangs off that.
/// </summary>
public sealed class McpRiskPolicyTests
{
    private static ConnectorRiskPolicy Declared() => new()
    {
        ConnectorId = "acme",
        PolicyDeclared = true,
        ReadOnlyTools = ["list_issues"],
        LocalMutationTools = ["cache_write"],
        ExternalMutationTools = ["send_message"],
        DestructiveTools = ["delete_project"],
        VerificationTools = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["send_message"] = "get_message"
        }
    };

    [Theory]
    [InlineData("list_issues", McpRiskClass.ReadOnly)]
    [InlineData("cache_write", McpRiskClass.LocalMutation)]
    [InlineData("send_message", McpRiskClass.ExternalMutation)]
    [InlineData("delete_project", McpRiskClass.Destructive)]
    public void A_Declared_Policy_Establishes_The_Class(string tool, McpRiskClass expected)
    {
        var assessment = McpRiskPolicy.Classify(Declared(), tool);

        assessment.Risk.Should().Be(expected);
        assessment.PolicyValidated.Should().BeTrue();
    }

    [Fact]
    public void Without_A_Policy_A_Read_Shaped_Name_Is_Only_Repeat_Safe_Not_Proven_Safe()
    {
        var assessment = McpRiskPolicy.Classify(
            ConnectorRiskPolicy.Undeclared("acme"), "get_invoice");

        assessment.Risk.Should().Be(
            McpRiskClass.IdempotentRead,
            "a name is a hint, not a proof that the tool has no side effect");
        assessment.PolicyValidated.Should().BeFalse();
    }

    [Fact]
    public void An_Unrecognised_Tool_Is_Unknown_And_Treated_As_Consequential()
    {
        var assessment = McpRiskPolicy.Classify(
            ConnectorRiskPolicy.Undeclared("acme"), "frobnicate");

        assessment.Risk.Should().Be(McpRiskClass.Unknown);
        assessment.RequiresExplicitApproval.Should().BeTrue();
        assessment.MayRetryAfterDispatch.Should().BeFalse(
            "an unknown tool that turns out to send a message must never be auto-retried");
        assessment.RequiresReconciliation.Should().BeTrue();
    }

    [Fact]
    public void A_Server_Claiming_Read_Only_Does_Not_Get_A_Read_Only_Verdict()
    {
        var assessment = McpRiskPolicy.Classify(
            ConnectorRiskPolicy.Undeclared("acme"),
            "wipe_everything",
            description: null,
            serverAnnotations: new McpToolAnnotations(ReadOnlyHint: true));

        assessment.Risk.Should().Be(McpRiskClass.Destructive);
        assessment.Evidence.Should().Contain("server_hint=read_only_rejected_unvalidated");
        assessment.MayRetryAfterDispatch.Should().BeFalse();
    }

    [Fact]
    public void A_Server_Claiming_Destructive_Is_Believed_Because_It_Only_Tightens()
    {
        var assessment = McpRiskPolicy.Classify(
            ConnectorRiskPolicy.Undeclared("acme"),
            "frobnicate",
            description: null,
            serverAnnotations: new McpToolAnnotations(DestructiveHint: true));

        assessment.Risk.Should().Be(McpRiskClass.Destructive);
        assessment.Evidence.Should().Contain("server_hint=destructive_accepted");
    }

    [Fact]
    public void A_Policy_Claiming_A_Delete_Is_Read_Only_Does_Not_Get_Away_With_It()
    {
        var wrong = Declared() with { ReadOnlyTools = ["delete_everything"] };

        var assessment = McpRiskPolicy.Classify(wrong, "delete_everything");

        assessment.Risk.Should().Be(
            McpRiskClass.Destructive,
            "a policy that contradicts what the tool plainly does is a mistake, "
            + "not a permission");
        assessment.PolicyValidated.Should().BeFalse();
    }

    [Theory]
    [InlineData(McpRiskClass.ReadOnly, true, true)]
    [InlineData(McpRiskClass.IdempotentRead, true, true)]
    [InlineData(McpRiskClass.LocalMutation, true, false)]
    [InlineData(McpRiskClass.ExternalMutation, true, false)]
    [InlineData(McpRiskClass.Destructive, true, false)]
    [InlineData(McpRiskClass.Unknown, true, false)]
    public void Retry_Rules_Follow_From_The_Class_Not_From_The_Call_Site(
        McpRiskClass risk,
        bool beforeDispatch,
        bool afterDispatch)
    {
        var policy = new ConnectorRiskPolicy
        {
            ConnectorId = "acme",
            PolicyDeclared = true,
            ReadOnlyTools = risk == McpRiskClass.ReadOnly ? ["probe"] : [],
            LocalMutationTools = risk == McpRiskClass.LocalMutation ? ["probe"] : [],
            ExternalMutationTools = risk == McpRiskClass.ExternalMutation ? ["probe"] : [],
            DestructiveTools = risk == McpRiskClass.Destructive ? ["probe"] : []
        };
        var toolName = risk switch
        {
            McpRiskClass.IdempotentRead => "get_probe",
            McpRiskClass.Unknown => "probe",
            _ => "probe"
        };
        var assessment = McpRiskPolicy.Classify(
            risk is McpRiskClass.IdempotentRead or McpRiskClass.Unknown
                ? ConnectorRiskPolicy.Undeclared("acme")
                : policy,
            toolName);

        assessment.Risk.Should().Be(risk);
        assessment.MayRetryBeforeDispatch.Should().Be(beforeDispatch,
            "a request that never left the queue provably did nothing");
        assessment.MayRetryAfterDispatch.Should().Be(afterDispatch);
        if (afterDispatch)
            assessment.RequiresProvenanceValidation.Should().BeTrue(
                "repeating a read is only safe against the same server");
    }

    [Fact]
    public void The_Verification_Tool_Comes_From_The_Policy_Not_From_The_Server()
    {
        McpRiskPolicy.ResolveVerificationTool(Declared(), "send_message")
            .Should().Be("get_message");
        McpRiskPolicy.ResolveVerificationTool(
                ConnectorRiskPolicy.Undeclared("acme"), "send_message")
            .Should().BeNull(
                "letting a server nominate its own verifier lets it nominate one "
                + "that always says yes");
    }

    [Theory]
    [InlineData("mcp.acme.tools/list", SideEffectClass.ReadOnly)]
    [InlineData("mcp.acme.resources/read", SideEffectClass.ReadOnly)]
    [InlineData("mcp.acme.prompts/get", SideEffectClass.ReadOnly)]
    [InlineData("mcp.acme.initialize", SideEffectClass.ReadOnly)]
    [InlineData("mcp.acme.send_message", SideEffectClass.Consequential)]
    [InlineData("mcp.acme.delete_project", SideEffectClass.Irreversible)]
    [InlineData("mcp.acme.frobnicate", SideEffectClass.Consequential)]
    public void The_Durable_Capability_Name_Classifies_Consistently(
        string capabilityName,
        SideEffectClass expected)
    {
        McpRiskPolicy.ClassifyCapabilityName(capabilityName).SideEffectClass
            .Should().Be(expected);
    }

    [Fact]
    public void The_Feature_Matrix_Never_Claims_Something_It_Also_Calls_Unsupported()
    {
        McpProtocolFeatureMatrix.Implemented
            .Intersect(McpProtocolFeatureMatrix.Unsupported.Keys)
            .Should().BeEmpty();
        McpProtocolFeatureMatrix.Unsupported.Values
            .Should().OnlyContain(reason => reason.Length > 20,
                "an unsupported feature must say what is missing, not just 'no'");
    }
}
