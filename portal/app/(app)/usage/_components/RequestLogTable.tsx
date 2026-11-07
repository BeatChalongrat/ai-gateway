import { Activity, ArrowRight } from "lucide-react";

import EmptyState from "../../_components/EmptyState";

export type LogRow = {
  id: string;
  timestamp: string;
  model: string;
  project: string;
  tokens: number;
  latencyMs: number;
  status: "success" | "error" | "rate_limited";
};

type Props = {
  rows: LogRow[];
};

const STATUS_PALETTE: Record<
  LogRow["status"],
  { bg: string; color: string }
> = {
  success: {
    bg: "rgba(16,185,129,0.12)",
    color: "var(--accent-success)",
  },
  error: {
    bg: "rgba(248,113,113,0.12)",
    color: "var(--accent-danger)",
  },
  rate_limited: {
    bg: "rgba(245,158,11,0.14)",
    color: "var(--accent-warning)",
  },
};

const STATUS_LABEL: Record<LogRow["status"], string> = {
  success: "200 OK",
  error: "Error",
  rate_limited: "429",
};

function shellStyle(): React.CSSProperties {
  return {
    background:
      "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
    border: "1px solid var(--border-default)",
    borderRadius: 12,
    overflow: "hidden",
  };
}

function Header() {
  return (
    <div
      className="px-6 py-4"
      style={{ borderBottom: "1px solid var(--border-subtle)" }}
    >
      <h3
        className="font-heading"
        style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}
      >
        Recent requests
      </h3>
      <p style={{ fontSize: 12.5, color: "var(--text-tertiary)", marginTop: 2 }}>
        Your most recent requests
      </p>
    </div>
  );
}

export default function RequestLogTable({ rows }: Props) {
  if (rows.length === 0) {
    return (
      <div style={shellStyle()}>
        <Header />
        <EmptyState
          icon={<Activity className="w-7 h-7" strokeWidth={1.5} />}
          title="No requests in this period"
          description="Once you send requests with your API keys, they'll appear here with token counts and latency."
          action={{
            label: (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                View API Keys <ArrowRight size={14} />
              </span>
            ),
            href: "/projects",
          }}
        />
      </div>
    );
  }

  return (
    <div style={shellStyle()}>
      <Header />
      <div className="overflow-x-auto">
        <table className="w-full" style={{ fontSize: 13, borderCollapse: "collapse" }}>
          <thead>
            <tr
              style={{
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-surface-2)",
              }}
            >
              <Th>Time</Th>
              <Th>Model</Th>
              <Th>Project</Th>
              <Th align="right">Tokens</Th>
              <Th align="right">Latency</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const palette = STATUS_PALETTE[row.status];
              return (
                <tr
                  key={row.id}
                  className="row-hover"
                  style={{ borderBottom: "1px solid var(--border-subtle)" }}
                >
                  <Td color="var(--text-secondary)" nowrap>
                    {row.timestamp}
                  </Td>
                  <Td color="var(--text-primary)" mono>
                    {row.model}
                  </Td>
                  <Td color="var(--text-primary)">{row.project}</Td>
                  <Td color="var(--text-primary)" align="right" weight={500}>
                    {row.tokens.toLocaleString()}
                  </Td>
                  <Td color="var(--text-secondary)" align="right">
                    {row.latencyMs}ms
                  </Td>
                  <Td>
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded"
                      style={{
                        fontSize: 11,
                        fontWeight: 500,
                        background: palette.bg,
                        color: palette.color,
                      }}
                    >
                      {STATUS_LABEL[row.status]}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        padding: "12px 24px",
        textAlign: align,
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  color = "var(--text-primary)",
  align = "left",
  mono = false,
  nowrap = false,
  weight,
}: {
  children: React.ReactNode;
  color?: string;
  align?: "left" | "right";
  mono?: boolean;
  nowrap?: boolean;
  weight?: number;
}) {
  return (
    <td
      style={{
        padding: "12px 24px",
        color,
        textAlign: align,
        fontFamily: mono ? "var(--ff-mono)" : "inherit",
        fontSize: mono ? 12 : 13,
        whiteSpace: nowrap ? "nowrap" : "normal",
        fontWeight: weight,
      }}
    >
      {children}
    </td>
  );
}
