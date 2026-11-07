// FIX89 Part B — a card says what a connector DOES, derived from the tool
// names that already exist. The eight catalogue entries below are the real
// ones (SranDesktop.Infrastructure/Services/McpConnectorService.cs, the
// embedded starter catalogue), so a change to that list that this rule reads
// wrongly shows up here rather than on a card.
import { describe, expect, it } from "vitest";

import { connectorAccess, toolAccess, toolVerb } from "./connectorCapability";

/** The catalogue as it is on disk, id → tools. */
const CATALOGUE: Record<string, string[]> = {
  slack: ["slack_send_message", "slack_read_channel", "slack_search"],
  gmail: ["gmail_search", "gmail_read", "gmail_draft"],
  gdrive: ["gdrive_search", "gdrive_read"],
  notion: ["notion_search", "notion_read_page", "notion_create_page"],
  github: ["github_search", "github_read_issue", "github_list_prs"],
  jira: ["jira_search", "jira_read_issue", "jira_update_issue"],
  gcal: ["gcal_list_events", "gcal_create_event"],
  postgres: ["postgres_query", "postgres_schema"],
};

describe("the verb is the data", () => {
  it("strips the connector id and the MCP namespace the model sees", () => {
    expect(toolVerb("slack_send_message", "slack")).toBe("send_message");
    expect(toolVerb("mcp__slack__slack_send_message", "slack")).toBe("send_message");
    expect(toolVerb("mcp__my-server__do_thing")).toBe("do_thing");
    // an id that is not a prefix is left alone
    expect(toolVerb("read_file", "slack")).toBe("read_file");
  });

  it("classifies by the HEAD verb, and says 'unknown' rather than guessing", () => {
    expect(toolAccess("slack_read_channel", "slack")).toBe("read");
    expect(toolAccess("notion_create_page", "notion")).toBe("write");
    expect(toolAccess("postgres_schema", "postgres")).toBe("read");
    // a name whose head is not a verb we know: no guess, even though a later
    // word looks like one
    expect(toolAccess("frobnicate_widget")).toBe("unknown");
    expect(toolAccess("page_create")).toBe("unknown");
    expect(toolAccess("")).toBe("unknown");
  });
});

describe("every catalogue connector gets the right answer", () => {
  const expected: Record<string, string> = {
    slack: "connectors.access.readWrite", // send_message writes
    gmail: "connectors.access.readWrite", // draft writes
    gdrive: "connectors.access.readOnly",
    notion: "connectors.access.readWrite", // create_page writes
    github: "connectors.access.readOnly", // search / read_issue / list_prs
    jira: "connectors.access.readWrite", // update_issue writes
    gcal: "connectors.access.readWrite", // create_event writes
    postgres: "connectors.access.readOnly", // query / schema
  };
  for (const [id, tools] of Object.entries(CATALOGUE)) {
    it(`${id}: ${expected[id]!.split(".").pop()}`, () => {
      const a = connectorAccess(tools, id);
      expect(a.key).toBe(expected[id]);
      expect(a.unknown).toBe(false); // every catalogue name is recognised
    });
  }
});

describe("it would rather say less than claim wrongly", () => {
  it("never says read-only while a tool's name told us nothing", () => {
    const a = connectorAccess(["thing_search", "frobnicate"], "thing");
    expect(a.reads).toBe(true);
    expect(a.writes).toBe(false);
    expect(a.unknown).toBe(true);
    expect(a.key).toBe("connectors.access.reads"); // "Reads data" — no "only"
  });

  it("says nothing at all when no name was recognised (a custom server's own naming)", () => {
    expect(connectorAccess(["frobnicate", "wibble"], "mine").key).toBeNull();
    expect(connectorAccess([], "mine").key).toBeNull();
  });

  it("one write is enough to say so, whatever else is unknown", () => {
    expect(connectorAccess(["x_send_thing", "wibble"], "x").key).toBe("connectors.access.writeOnly");
    expect(connectorAccess(["x_send_thing", "x_read_thing"], "x").key).toBe("connectors.access.readWrite");
  });
});
