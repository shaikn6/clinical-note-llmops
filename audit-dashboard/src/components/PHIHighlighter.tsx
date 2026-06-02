import React, { useMemo } from 'react';
import type { PHICategory, PHIEntity } from '../types';

const CATEGORY_COLORS: Record<PHICategory, { bg: string; border: string; label: string }> = {
  PERSON:      { bg: 'rgba(239,68,68,0.25)',   border: '#ef4444', label: 'Person' },
  DATE:        { bg: 'rgba(59,130,246,0.25)',  border: '#3b82f6', label: 'Date' },
  LOCATION:    { bg: 'rgba(34,197,94,0.25)',   border: '#22c55e', label: 'Location' },
  PHONE:       { bg: 'rgba(234,179,8,0.25)',   border: '#eab308', label: 'Phone' },
  EMAIL:       { bg: 'rgba(168,85,247,0.25)',  border: '#a855f7', label: 'Email' },
  SSN:         { bg: 'rgba(239,68,68,0.35)',   border: '#dc2626', label: 'SSN' },
  MRN:         { bg: 'rgba(249,115,22,0.25)',  border: '#f97316', label: 'MRN' },
  DEVICE:      { bg: 'rgba(20,184,166,0.25)',  border: '#14b8a6', label: 'Device' },
  URL:         { bg: 'rgba(99,102,241,0.25)',  border: '#6366f1', label: 'URL' },
  IP_ADDRESS:  { bg: 'rgba(236,72,153,0.25)',  border: '#ec4899', label: 'IP' },
  VEHICLE:     { bg: 'rgba(245,158,11,0.25)',  border: '#f59e0b', label: 'Vehicle' },
  ACCOUNT:     { bg: 'rgba(16,185,129,0.25)',  border: '#10b981', label: 'Account' },
  LICENSE:     { bg: 'rgba(139,92,246,0.25)',  border: '#8b5cf6', label: 'License' },
  CERTIFICATE: { bg: 'rgba(6,182,212,0.25)',   border: '#06b6d4', label: 'Cert' },
  AGE:         { bg: 'rgba(251,191,36,0.25)',  border: '#fbbf24', label: 'Age' },
  UNKNOWN:     { bg: 'rgba(107,114,128,0.25)', border: '#6b7280', label: '?' },
};

interface Segment {
  text: string;
  entity: PHIEntity | null;
  key: string;
}

function buildSegments(text: string, entities: PHIEntity[]): Segment[] {
  if (!entities.length) {
    return [{ text, entity: null, key: 'plain-0' }];
  }

  const sorted = [...entities].sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let cursor = 0;

  for (const entity of sorted) {
    if (entity.start > cursor) {
      segments.push({ text: text.slice(cursor, entity.start), entity: null, key: `plain-${cursor}` });
    }
    if (entity.start >= cursor) {
      segments.push({ text: text.slice(entity.start, entity.end), entity, key: `entity-${entity.start}` });
      cursor = entity.end;
    }
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), entity: null, key: `plain-${cursor}` });
  }

  return segments;
}

interface PHIHighlighterProps {
  text: string;
  entities: PHIEntity[];
  showReplacements?: boolean;
}

export const PHIHighlighter: React.FC<PHIHighlighterProps> = ({
  text,
  entities,
  showReplacements = false,
}) => {
  const segments = useMemo(() => buildSegments(text, entities), [text, entities]);

  return (
    <div className="phi-highlighter" aria-label="Clinical note with PHI highlighted">
      <p className="phi-text">
        {segments.map((seg) => {
          if (!seg.entity) {
            return <span key={seg.key}>{seg.text}</span>;
          }
          const colors = CATEGORY_COLORS[seg.entity.type];
          const display = showReplacements ? seg.entity.replacement : seg.text;
          return (
            <span
              key={seg.key}
              className="phi-span"
              title={`${colors.label} — confidence: ${(seg.entity.confidence * 100).toFixed(1)}%`}
              style={{
                backgroundColor: colors.bg,
                borderBottom: `2px solid ${colors.border}`,
                borderRadius: '2px',
                padding: '0 2px',
                cursor: 'help',
              }}
            >
              {display}
              <sup
                style={{ fontSize: '0.6em', color: colors.border, marginLeft: '2px' }}
                aria-label={colors.label}
              >
                {colors.label}
              </sup>
            </span>
          );
        })}
      </p>

      <div className="phi-legend" aria-label="PHI color legend">
        {entities.length > 0 &&
          [...new Set(entities.map((e) => e.type))].map((type) => {
            const colors = CATEGORY_COLORS[type];
            return (
              <span key={type} className="legend-item">
                <span
                  className="legend-swatch"
                  style={{ background: colors.border }}
                  aria-hidden="true"
                />
                {colors.label}
              </span>
            );
          })}
      </div>
    </div>
  );
};

export default PHIHighlighter;
