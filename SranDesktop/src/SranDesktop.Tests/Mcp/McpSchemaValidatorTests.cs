using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Remote;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// The schema gate for MCP-advertised tools. The behaviour these lock down is
/// that a bad schema REMOVES the tool — it is never downgraded into a permissive
/// object schema, which would let an untrusted server send whatever it wanted.
/// </summary>
public sealed class McpSchemaValidatorTests
{
    [Fact]
    public void A_Well_Formed_Schema_Is_Accepted()
    {
        var verdict = McpSchemaValidator.Validate("""
            {
              "type": "object",
              "properties": {
                "query": { "type": "string" },
                "limit": { "type": "integer" }
              },
              "required": ["query"]
            }
            """);

        verdict.Accepted.Should().BeTrue(verdict.Reason);
    }

    [Theory]
    [InlineData("", "SchemaMissing")]
    [InlineData("{ not json", "SchemaUnparseable")]
    [InlineData("[]", "SchemaNotAnObject")]
    [InlineData("""{"type":"string"}""", "SchemaRootNotObjectType")]
    [InlineData("""{"type":"object","properties":[]}""", "SchemaPropertiesNotAnObject")]
    [InlineData(
        """{"type":"object","properties":{},"additionalProperties":true}""",
        "SchemaAllowsArbitraryFields")]
    [InlineData(
        """{"type":"object","properties":{"a":{"type":"wat"}}}""",
        "SchemaUnknownType")]
    [InlineData(
        """{"type":"object","properties":{"a":{"type":"string"}},"required":["b"]}""",
        "SchemaRequiredFieldUndeclared")]
    [InlineData(
        """{"type":"object","properties":{"a":{"type":"string","enum":[]}}}""",
        "SchemaInvalidEnum")]
    [InlineData(
        """{"type":"object","properties":{"a":"not-a-schema"}}""",
        "SchemaPropertyNotAnObject")]
    public void Unsafe_Or_Broken_Schemas_Are_Rejected_With_A_Reason(
        string schema,
        string expectedCode)
    {
        var verdict = McpSchemaValidator.Validate(schema);

        verdict.Accepted.Should().BeFalse();
        verdict.RejectionCode.Should().Be(expectedCode);
        verdict.Reason.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public void A_Zero_Argument_Tool_Schema_Is_Accepted()
    {
        // Real MCP servers advertise argument-less tools this way; rejecting it
        // would drop every legitimate no-parameter tool.
        McpSchemaValidator.Validate("""{"type":"object"}""")
            .Accepted.Should().BeTrue();
        McpSchemaValidator.Validate("""{"type":"object","properties":{}}""")
            .Accepted.Should().BeTrue();
    }

    [Fact]
    public void Deeply_Nested_Schemas_Are_Rejected_Rather_Than_Walked()
    {
        var schema = """{"type":"object","properties":{"a":""";
        var closing = "}}";
        for (var depth = 0; depth < McpSchemaValidator.MaxSchemaDepth + 3; depth++)
        {
            schema += """{"type":"object","properties":{"a":""";
            closing += "}}";
        }
        schema += """{"type":"string"}""" + closing;

        var verdict = McpSchemaValidator.Validate(schema);

        verdict.Accepted.Should().BeFalse();
        verdict.RejectionCode.Should().Be("SchemaTooDeep");
    }

    [Fact]
    public void An_Oversized_Schema_Is_Rejected_Before_Parsing()
    {
        var padding = new string('x', McpSchemaValidator.MaxSchemaBytes + 1);
        var verdict = McpSchemaValidator.Validate(
            "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"string\","
            + $"\"description\":\"{padding}\"}}}}}}");

        verdict.Accepted.Should().BeFalse();
        verdict.RejectionCode.Should().Be("SchemaTooLarge");
    }

    [Fact]
    public void Tool_Names_Are_Namespaced_By_Connector()
    {
        McpSchemaValidator.Namespaced("github", "search")
            .Should().Be("github.search");
        McpSchemaValidator.Namespaced("gitlab", "search")
            .Should().NotBe(McpSchemaValidator.Namespaced("github", "search"));
    }

    [Theory]
    [InlineData("search_issues", true)]
    [InlineData("search-issues", true)]
    [InlineData("", false)]
    [InlineData("has space", false)]
    [InlineData("path/traversal", false)]
    [InlineData("semi;colon", false)]
    public void Tool_Names_Are_Restricted_To_Safe_Characters(string name, bool expected) =>
        McpSchemaValidator.IsValidToolName(name).Should().Be(expected);
}
