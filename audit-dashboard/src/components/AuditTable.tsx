import React, { useState } from 'react';
import type { NoteAuditRecord } from '../types';

const PAGE_SIZE = 10;

interface AuditTableProps {
  records: NoteAuditRecord[];
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function statusBadge(record: NoteAuditRecord): React.ReactElement {
  const { isCompliant, reviewRequired } = record.complianceStatus;
  if (!isCompliant) {
    return <span className="badge badge-red">Non-Compliant</span>;
  }
  if (reviewRequired || record.flaggedForReview) {
    return <span className="badge badge-yellow">Review Needed</span>;
  }
  return <span className="badge badge-green">Compliant</span>;
}

export const AuditTable: React.FC<AuditTableProps> = ({ records }) => {
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<keyof NoteAuditRecord>('processedAt');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [filter, setFilter] = useState('');

  const filtered = records.filter(
    (r) =>
      r.noteId.toLowerCase().includes(filter.toLowerCase()) ||
      r.noteType.toLowerCase().includes(filter.toLowerCase()),
  );

  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortKey] as string | number;
    const bv = b[sortKey] as string | number;
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const pageCount = Math.ceil(sorted.length / PAGE_SIZE);
  const visible = sorted.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const toggleSort = (key: keyof NoteAuditRecord) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(0);
  };

  const SortIcon: React.FC<{ col: keyof NoteAuditRecord }> = ({ col }) => {
    if (sortKey !== col) return <span aria-hidden="true"> ↕</span>;
    return <span aria-hidden="true">{sortDir === 'asc' ? ' ↑' : ' ↓'}</span>;
  };

  return (
    <div className="audit-table-container">
      <div className="table-controls">
        <input
          type="search"
          className="table-search"
          placeholder="Filter by Note ID or type…"
          value={filter}
          onChange={(e) => { setFilter(e.target.value); setPage(0); }}
          aria-label="Filter audit records"
        />
        <span className="record-count">{filtered.length} records</span>
      </div>

      <div className="table-scroll" role="region" aria-label="Audit log table" tabIndex={0}>
        <table className="audit-table" aria-label="HIPAA audit log">
          <thead>
            <tr>
              <th>
                <button className="sort-btn" onClick={() => toggleSort('noteId')}>
                  Note ID <SortIcon col="noteId" />
                </button>
              </th>
              <th>
                <button className="sort-btn" onClick={() => toggleSort('noteType')}>
                  Type <SortIcon col="noteType" />
                </button>
              </th>
              <th>
                <button className="sort-btn" onClick={() => toggleSort('processedAt')}>
                  Processed <SortIcon col="processedAt" />
                </button>
              </th>
              <th>
                <button className="sort-btn" onClick={() => toggleSort('phiEntityCount')}>
                  PHI Count <SortIcon col="phiEntityCount" />
                </button>
              </th>
              <th>
                <button className="sort-btn" onClick={() => toggleSort('processingDurationMs')}>
                  Duration (ms) <SortIcon col="processingDurationMs" />
                </button>
              </th>
              <th>Status</th>
              <th>Reviewer</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-row">No records match the current filter.</td>
              </tr>
            ) : (
              visible.map((rec) => (
                <tr key={rec.id} className={rec.flaggedForReview ? 'row-flagged' : ''}>
                  <td className="mono">{rec.noteId}</td>
                  <td>
                    <span className="note-type-badge">{rec.noteType}</span>
                  </td>
                  <td>{formatDate(rec.processedAt)}</td>
                  <td className={rec.phiEntityCount > 20 ? 'phi-count-high' : 'phi-count'}>
                    {rec.phiEntityCount}
                  </td>
                  <td>{rec.processingDurationMs.toLocaleString()}</td>
                  <td>{statusBadge(rec)}</td>
                  <td>{rec.reviewedBy ?? <span className="unreviewed">—</span>}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <nav className="pagination" aria-label="Audit table pagination">
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            aria-label="Previous page"
          >
            ‹
          </button>
          <span className="page-info">
            Page {page + 1} of {pageCount}
          </span>
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={page === pageCount - 1}
            aria-label="Next page"
          >
            ›
          </button>
        </nav>
      )}
    </div>
  );
};

export default AuditTable;
