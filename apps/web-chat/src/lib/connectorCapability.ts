// FIX89 Part B — what a connector DOES, from what is already known about it.
//
// A card used to print the tool ids it exposes — `slack_send_message ·
// slack_read_channel · slack_search`. That is an implementation detail; the
// question a person deciding whether to connect actually has is "what can
// this reach, and can it change anything?". Claude's connector directory
// answers exactly that.
//
// The only fact we hold about a connector's abilities is its list of tool
// names, and every catalogue tool is named `<connectorId>_<verb…>`
// (`slack_send_message`, `gcal_list_events`, `postgres_query`); a connector
// the user added themselves reports whatever names its own MCP server
// announced, prefixed `mcp__<id>__` once it reaches the model. So the verb
// IS the data — no new metadata file, and nothing hand-written per
// connector that a tool does not actually support.
//
// The rule is deliberately conservative: a verb we do not recognise makes
// the tool `unknown`, and an unknown tool is never counted as a read or a
// write. A card would rather say less than claim a capability wrongly — in
// particular, nothing is called read-only unless every one of its tools is
// a recognised read.

export type ToolAccess = "read" | "write" | "unknown";

/** Verbs that only look. */
const READ_VERBS = [
  "read", "search", "list", "get", "fetch", "query", "schema", "view",
  "find", "describe", "download", "export", "show", "check", "status",
] as const;

/** Verbs that change something on the other side. */
const WRITE_VERBS = [
  "send", "create", "update", "draft", "delete", "write", "post", "add",
  "set", "remove", "append", "upload", "move", "rename", "archive",
  "reply", "comment", "assign", "close", "merge", "invite", "schedule",
] as const;

/** The verb part of a tool name: the id prefix and the MCP namespace off. */
export function toolVerb(tool: string, connectorId?: string): string {
  let name = tool.trim().toLowerCase();
  // `mcp__<id>__<tool>` — the name as the model sees it (connector-mcp.ts).
  const ns = name.match(/^mcp__[a-z0-9_-]+__(.+)$/);
  if (ns) name = ns[1]!;
  const id = connectorId?.trim().toLowerCase();
  if (id && name.startsWith(`${id}_`)) name = name.slice(id.length + 1);
  return name;
}

/** Does this tool read, write, or is it a name we do not recognise? */
export function toolAccess(tool: string, connectorId?: string): ToolAccess {
  const verb = toolVerb(tool, connectorId);
  if (!verb) return "unknown";
  const head = verb.split("_")[0]!;
  // The head verb decides: `read_page` reads, `create_page` writes. A name
  // whose head is not a verb we know is unknown even if a later word looks
  // like one (`page_create` is not a shape the catalogue uses, and guessing
  // at it would be inventing).
  if ((READ_VERBS as readonly string[]).includes(head)) return "read";
  if ((WRITE_VERBS as readonly string[]).includes(head)) return "write";
  return "unknown";
}

export interface ConnectorAccess {
  reads: boolean;
  writes: boolean;
  /** true when at least one tool's name told us nothing. */
  unknown: boolean;
  /** The dictionary key for the access line, or null when we cannot say. */
  key:
    | "connectors.access.readWrite"
    | "connectors.access.readOnly"
    | "connectors.access.writeOnly"
    | "connectors.access.reads"
    | null;
}

/**
 * What a whole connector can do. Four answers and a silence:
 *
 *   * writes anything            → "reads and changes" / "changes"
 *   * only recognised reads      → "reads only — changes nothing", the one
 *                                  claim strong enough to be worth making,
 *                                  and only when EVERY tool was recognised
 *   * reads plus something we
 *     could not classify         → "reads" (no "only": an unrecognised tool
 *                                  might change something)
 *   * nothing recognised         → null, the card says nothing about access
 *                                  — the honest answer for a custom server
 *                                  whose author named things their own way.
 */
export function connectorAccess(tools: readonly string[], connectorId?: string): ConnectorAccess {
  let reads = false;
  let writes = false;
  let unknown = false;
  for (const t of tools) {
    const a = toolAccess(t, connectorId);
    if (a === "read") reads = true;
    else if (a === "write") writes = true;
    else unknown = true;
  }
  const key = writes
    ? reads
      ? "connectors.access.readWrite"
      : "connectors.access.writeOnly"
    : reads
      ? unknown
        ? "connectors.access.reads"
        : "connectors.access.readOnly"
      : null;
  return { reads, writes, unknown, key };
}
