import { Activity } from "lucide-react";

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

const STATUS_STYLE: Record<LogRow["status"], string> = {
  success: "bg-green-50 text-green-700",
  error: "bg-rose-50 text-rose-700",
  rate_limited: "bg-amber-50 text-amber-700",
};

const STATUS_LABEL: Record<LogRow["status"], string> = {
  success: "200 OK",
  error: "Error",
  rate_limited: "429",
};

export default function RequestLogTable({ rows }: Props) {
  if (rows.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-6 py-4 border-b border-gray-100">
          <h3 className="font-heading font-semibold text-gray-900">
            Recent requests
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            The last 100 requests from your API keys
          </p>
        </div>
        <EmptyState
          icon={<Activity className="w-7 h-7" strokeWidth={1.5} />}
          title="No requests in this period"
          description="Once you send requests with your API keys, they'll appear here with token counts and latency."
          action={{ label: "View API Keys →", href: "/projects" }}
        />
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h3 className="font-heading font-semibold text-gray-900">
          Recent requests
        </h3>
        <p className="text-sm text-gray-500 mt-0.5">
          The last 100 requests from your API keys
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-100">
            <tr className="text-left text-xs uppercase tracking-wider text-gray-500">
              <th className="px-6 py-3 font-medium">Time</th>
              <th className="px-6 py-3 font-medium">Model</th>
              <th className="px-6 py-3 font-medium">Project</th>
              <th className="px-6 py-3 font-medium text-right">Tokens</th>
              <th className="px-6 py-3 font-medium text-right">Latency</th>
              <th className="px-6 py-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={row.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-6 py-3 text-gray-600 whitespace-nowrap">
                  {row.timestamp}
                </td>
                <td className="px-6 py-3 font-mono text-xs text-gray-900">
                  {row.model}
                </td>
                <td className="px-6 py-3 text-gray-900">{row.project}</td>
                <td className="px-6 py-3 text-right font-medium text-gray-900">
                  {row.tokens.toLocaleString()}
                </td>
                <td className="px-6 py-3 text-right text-gray-600">
                  {row.latencyMs}ms
                </td>
                <td className="px-6 py-3">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLE[row.status]}`}
                  >
                    {STATUS_LABEL[row.status]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
