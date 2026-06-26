"use client";

import { useState, useMemo } from "react";
import EmptyState from "./EmptyState";
import ErrorState from "./ErrorState";
import { SkeletonTable } from "./Skeleton";
import SearchFilter from "./SearchFilter";
import Pagination from "./Pagination";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  width?: string;
  searchable?: boolean;
}

interface Props<T> {
  columns: Column<T>[];
  data: T[] | null;
  loading?: boolean;
  error?: string | null;
  emptyMessage?: string;
  emptyIcon?: React.ReactNode;
  emptyAction?: React.ReactNode;
  onRowClick?: (row: T) => void;
  rowKey?: (row: T) => string;
  searchable?: boolean;
  searchPlaceholder?: string;
  pageSize?: number;
  onRetry?: () => void;
}

export default function DataTable<T extends Record<string, any>>({
  columns,
  data,
  loading,
  error,
  emptyMessage = "No data found",
  emptyIcon,
  emptyAction,
  onRowClick,
  rowKey,
  searchable = false,
  searchPlaceholder,
  pageSize = 10,
  onRetry,
}: Props<T>) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    if (!data || !search) return data;
    const q = search.toLowerCase();
    return data.filter((row) =>
      columns.some((col) => {
        const val = row[col.key];
        return val && String(val).toLowerCase().includes(q);
      })
    );
  }, [data, search, columns]);

  const total = filtered?.length ?? 0;
  const paged = useMemo(() => {
    if (!filtered) return null;
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  // Reset to page 1 when search changes
  const handleSearch = (v: string) => {
    setSearch(v);
    setPage(1);
  };

  if (loading) return <SkeletonTable rows={pageSize > 8 ? 8 : pageSize} cols={columns.length} />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!data || data.length === 0) return <EmptyState message={emptyMessage} icon={emptyIcon} action={emptyAction} />;

  return (
    <>
      {searchable && (
        <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid var(--color-border-default)" }}>
          <SearchFilter value={search} onChange={handleSearch} placeholder={searchPlaceholder} />
        </div>
      )}
      {filtered && filtered.length === 0 ? (
        <EmptyState message={`No results for "${search}"`} icon="⌕" />
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="sran-table">
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th key={col.key} style={col.width ? { width: col.width } : undefined}>
                      {col.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paged?.map((row, i) => (
                  <tr
                    key={rowKey ? rowKey(row) : i}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    style={onRowClick ? { cursor: "pointer" } : undefined}
                  >
                    {columns.map((col) => (
                      <td key={col.key}>
                        {col.render ? col.render(row) : (row[col.key] ?? "—")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {total > pageSize && (
            <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} />
          )}
        </>
      )}
    </>
  );
}
