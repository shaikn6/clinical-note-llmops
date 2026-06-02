import React, { useState, useCallback } from 'react';
import { PHIHighlighter } from './components/PHIHighlighter';
import { ComplianceChart } from './components/ComplianceChart';
import { AuditTable } from './components/AuditTable';
import './styles/audit.css';
import type {
  TabId,
  PHIEntity,
  NoteAuditRecord,
  DeidentificationResult,
  AuditSummary,
} from './types';

// ── Sample data for demo ─────────────────────────────────────────────────────

const SAMPLE_NOTE = `Patient: John Smith, DOB: 03/15/1968, MRN: 789-32-1045
Address: 4521 Oak Street, Dayton, OH 45402
Phone: (937) 555-0192  Email: jsmith1968@email.com

DISCHARGE SUMMARY
Attending Physician: Dr. Sarah Johnson, MD
Facility: Dayton Regional Medical Center

Chief Complaint: The patient presented on 2024-01-15 with chest pain.
History: Mr. Smith is a 55-year-old male with a history of hypertension.
He was last seen in clinic on December 3rd, 2023.

SSN: 523-87-4410
Insurance ID: BCBS-OH-2847561

Discharge Medications: Lisinopril 10mg daily, Metformin 500mg BID.
Follow-up scheduled for February 8, 2024 with Dr. Johnson.`;

const MOCK_ENTITIES: PHIEntity[] = [
  { type: 'PERSON',   text: 'John Smith',         start: 9,   end: 19,  replacement: '[PATIENT]',      confidence: 0.98 },
  { type: 'DATE',     text: '03/15/1968',          start: 26,  end: 36,  replacement: '[DOB]',           confidence: 0.99 },
  { type: 'MRN',      text: '789-32-1045',         start: 43,  end: 55,  replacement: '[MRN]',           confidence: 0.97 },
  { type: 'LOCATION', text: '4521 Oak Street, Dayton, OH 45402', start: 65, end: 97, replacement: '[ADDRESS]', confidence: 0.95 },
  { type: 'PHONE',    text: '(937) 555-0192',      start: 106, end: 120, replacement: '[PHONE]',         confidence: 0.99 },
  { type: 'EMAIL',    text: 'jsmith1968@email.com',start: 128, end: 149, replacement: '[EMAIL]',         confidence: 0.99 },
  { type: 'PERSON',   text: 'Sarah Johnson',       start: 208, end: 221, replacement: '[PROVIDER]',     confidence: 0.96 },
  { type: 'DATE',     text: '2024-01-15',          start: 293, end: 303, replacement: '[DATE]',          confidence: 0.99 },
  { type: 'AGE',      text: '55-year-old',         start: 363, end: 374, replacement: '[AGE_RANGE]',    confidence: 0.94 },
  { type: 'DATE',     text: 'December 3rd, 2023',  start: 427, end: 446, replacement: '[DATE]',          confidence: 0.98 },
  { type: 'SSN',      text: '523-87-4410',         start: 448, end: 459, replacement: '[SSN]',           confidence: 0.99 },
  { type: 'PERSON',   text: 'Dr. Johnson',         start: 558, end: 569, replacement: '[PROVIDER]',     confidence: 0.95 },
];

function buildMockAuditRecords(): NoteAuditRecord[] {
  const noteTypes: NoteAuditRecord['noteType'][] = ['DISCHARGE', 'PROGRESS', 'RADIOLOGY', 'PATHOLOGY', 'OPERATIVE', 'OTHER'];
  return Array.from({ length: 42 }, (_, i) => ({
    id: `rec-${String(i + 1).padStart(4, '0')}`,
    noteId: `NOTE-2024-${String(10000 + i)}`,
    patientMrn: `MRN-${String(100000 + i * 7).padStart(6, '0')}`,
    noteType: noteTypes[i % noteTypes.length],
    processedAt: new Date(Date.now() - i * 2_700_000).toISOString(),
    processingDurationMs: 120 + Math.floor(Math.random() * 480),
    phiEntityCount: 3 + (i % 18),
    entityBreakdown: { PERSON: 1, DATE: 2, LOCATION: 1 },
    complianceStatus: {
      isCompliant: i % 7 !== 0,
      hipaaRule: '164.514(b)',
      auditTrailComplete: true,
      reviewRequired: i % 5 === 0,
      lastReviewedAt: i % 3 === 0 ? new Date(Date.now() - i * 86_400_000).toISOString() : null,
      issues: i % 7 === 0 ? ['High PHI density — manual review required'] : [],
    },
    reviewedBy: i % 3 === 0 ? 'compliance.officer@hospital.org' : null,
    flaggedForReview: i % 7 === 0,
  }));
}

function buildAuditSummary(): AuditSummary {
  return {
    totalNotesProcessed: 1_847,
    totalPHIEntitiesRemoved: 22_341,
    complianceRate: 96.4,
    averageProcessingTimeMs: 287,
    entityTypeDistribution: [
      { type: 'PERSON',   count: 6820, percentage: 30.5 },
      { type: 'DATE',     count: 5920, percentage: 26.5 },
      { type: 'LOCATION', count: 3350, percentage: 15.0 },
      { type: 'PHONE',    count: 1790, percentage: 8.0 },
      { type: 'MRN',      count: 1340, percentage: 6.0 },
      { type: 'SSN',      count:  895, percentage: 4.0 },
      { type: 'EMAIL',    count:  670, percentage: 3.0 },
      { type: 'AGE',      count:  447, percentage: 2.0 },
      { type: 'ACCOUNT',  count:  335, percentage: 1.5 },
      { type: 'UNKNOWN',  count:  774, percentage: 3.5 },
    ],
    dailyVolume: Array.from({ length: 14 }, (_, i) => ({
      date: new Date(Date.now() - (13 - i) * 86_400_000).toISOString().slice(0, 10),
      count: 100 + Math.floor(Math.sin(i * 0.8) * 40) + Math.floor(Math.random() * 30),
    })),
    flaggedForReview: 72,
  };
}

// ── App component ─────────────────────────────────────────────────────────────

const AUDIT_RECORDS = buildMockAuditRecords();
const SUMMARY = buildAuditSummary();

const TABS: { id: TabId; label: string }[] = [
  { id: 'live-deid',  label: 'Live De-ID' },
  { id: 'audit-log',  label: 'Audit Log' },
  { id: 'compliance', label: 'Compliance Metrics' },
];

export default function App(): React.ReactElement {
  const [activeTab, setActiveTab] = useState<TabId>('live-deid');
  const [noteInput, setNoteInput] = useState(SAMPLE_NOTE);
  const [deidResult, setDeidResult] = useState<DeidentificationResult | null>(null);
  const [showReplaced, setShowReplaced] = useState(false);
  const [processing, setProcessing] = useState(false);

  const handleDeidentify = useCallback(() => {
    setProcessing(true);
    // Simulate async de-identification (replace with real API call)
    setTimeout(() => {
      const result: DeidentificationResult = {
        originalText: noteInput,
        deidentifiedText: noteInput,
        entities: MOCK_ENTITIES,
        processingTimeMs: 142,
        modelVersion: 'phi-detector-v3.2.1',
        complianceLevel: 'HIPAA_SAFE_HARBOR',
      };
      setDeidResult(result);
      setProcessing(false);
    }, 600);
  }, [noteInput]);

  const handleReset = useCallback(() => {
    setDeidResult(null);
    setNoteInput('');
    setShowReplaced(false);
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>HIPAA De-ID Audit Dashboard</h1>
        <span className="hipaa-badge">HIPAA SAFE HARBOR</span>
      </header>

      <nav className="tab-nav" aria-label="Dashboard sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
            aria-selected={activeTab === tab.id}
            role="tab"
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="tab-content" role="tabpanel">
        {activeTab === 'live-deid' && (
          <section aria-labelledby="deid-heading">
            <h2 id="deid-heading" className="section-title">Live PHI De-Identification</h2>
            <div className="deid-layout">
              <div className="deid-panel">
                <h2>Input Note</h2>
                <textarea
                  className="note-textarea"
                  aria-label="Clinical note input"
                  value={noteInput}
                  onChange={(e) => setNoteInput(e.target.value)}
                  placeholder="Paste a clinical note to de-identify…"
                  spellCheck={false}
                />
                <div className="deid-controls">
                  <button
                    className="btn-primary"
                    onClick={handleDeidentify}
                    disabled={processing || !noteInput.trim()}
                    aria-busy={processing}
                  >
                    {processing ? 'Processing…' : 'De-Identify Note'}
                  </button>
                  <button className="btn-secondary" onClick={handleReset}>
                    Reset
                  </button>
                  {deidResult && (
                    <label className="toggle-label">
                      <input
                        type="checkbox"
                        checked={showReplaced}
                        onChange={(e) => setShowReplaced(e.target.checked)}
                        aria-label="Show replacements"
                      />
                      Show replacements
                    </label>
                  )}
                </div>
              </div>

              <div className="deid-panel">
                <h2>
                  {deidResult
                    ? `${deidResult.entities.length} PHI entities detected`
                    : 'De-identified Output'}
                </h2>
                {deidResult ? (
                  <>
                    <PHIHighlighter
                      text={deidResult.originalText}
                      entities={deidResult.entities}
                      showReplacements={showReplaced}
                    />
                    <div className="entity-summary" aria-label="Detected entity types">
                      {[...new Set(deidResult.entities.map((e) => e.type))].map((type) => {
                        const count = deidResult.entities.filter((e) => e.type === type).length;
                        return (
                          <span key={type} className="entity-chip">
                            {type} × {count}
                          </span>
                        );
                      })}
                    </div>
                    <p style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-dim)' }}>
                      Model: {deidResult.modelVersion} ·
                      Processed in {deidResult.processingTimeMs}ms ·
                      {deidResult.complianceLevel.replace(/_/g, ' ')}
                    </p>
                  </>
                ) : (
                  <p style={{ color: 'var(--color-text-dim)', padding: 'var(--space-md) 0' }}>
                    Paste a clinical note and click "De-Identify Note" to see results.
                  </p>
                )}
              </div>
            </div>
          </section>
        )}

        {activeTab === 'audit-log' && (
          <section aria-labelledby="audit-heading">
            <h2 id="audit-heading" className="section-title">Audit Log</h2>
            <AuditTable records={AUDIT_RECORDS} />
          </section>
        )}

        {activeTab === 'compliance' && (
          <section aria-labelledby="compliance-heading">
            <h2 id="compliance-heading" className="section-title">Compliance Metrics</h2>
            <div className="stat-grid">
              <div className="stat-card">
                <p className="stat-label">Notes Processed</p>
                <p className="stat-value">{SUMMARY.totalNotesProcessed.toLocaleString()}</p>
              </div>
              <div className="stat-card">
                <p className="stat-label">PHI Entities Removed</p>
                <p className="stat-value">{SUMMARY.totalPHIEntitiesRemoved.toLocaleString()}</p>
              </div>
              <div className="stat-card">
                <p className="stat-label">Compliance Rate</p>
                <p className="stat-value">{SUMMARY.complianceRate}%</p>
              </div>
              <div className="stat-card">
                <p className="stat-label">Avg Processing Time</p>
                <p className="stat-value">{SUMMARY.averageProcessingTimeMs}ms</p>
                <p className="stat-sub">per note</p>
              </div>
              <div className="stat-card">
                <p className="stat-label">Flagged for Review</p>
                <p className="stat-value" style={{ color: 'var(--color-warn)' }}>
                  {SUMMARY.flaggedForReview}
                </p>
              </div>
            </div>
            <ComplianceChart
              distribution={SUMMARY.entityTypeDistribution}
              dailyVolume={SUMMARY.dailyVolume}
            />
          </section>
        )}
      </main>
    </div>
  );
}
