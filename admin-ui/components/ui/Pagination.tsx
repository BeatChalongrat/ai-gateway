"use client";

interface Props {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export default function Pagination({ page, pageSize, total, onPageChange }: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  return (
    <div className="sran-pagination">
      <span className="sran-pagination-info">
        Showing {start}–{end} of {total}
      </span>
      <div className="sran-pagination-controls">
        <button
          className="sran-btn sran-btn-secondary sran-btn-sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          ← Prev
        </button>
        <span className="sran-pagination-page">
          {page} / {totalPages}
        </span>
        <button
          className="sran-btn sran-btn-secondary sran-btn-sm"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next →
        </button>
      </div>
    </div>
  );
}
