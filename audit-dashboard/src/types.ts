// HIPAA PHI Entity Types
export type PHICategory =
  | 'PERSON'
  | 'DATE'
  | 'LOCATION'
  | 'PHONE'
  | 'EMAIL'
  | 'SSN'
  | 'MRN'
  | 'DEVICE'
  | 'URL'
  | 'IP_ADDRESS'
  | 'VEHICLE'
  | 'ACCOUNT'
  | 'LICENSE'
  | 'CERTIFICATE'
  | 'AGE'
  | 'UNKNOWN';

export interface PHIEntity {
  type: PHICategory;
  text: string;
  start: number;
  end: number;
  replacement: string;
  confidence: number;
}

export interface DeidentificationResult {
  originalText: string;
  deidentifiedText: string;
  entities: PHIEntity[];
  processingTimeMs: number;
  modelVersion: string;
  complianceLevel: 'HIPAA_SAFE_HARBOR' | 'HIPAA_EXPERT_DETERMINATION' | 'NON_COMPLIANT';
}

export interface NoteAuditRecord {
  id: string;
  noteId: string;
  patientMrn: string;
  noteType: 'DISCHARGE' | 'PROGRESS' | 'RADIOLOGY' | 'PATHOLOGY' | 'OPERATIVE' | 'OTHER';
  processedAt: string;
  processingDurationMs: number;
  phiEntityCount: number;
  entityBreakdown: Partial<Record<PHICategory, number>>;
  complianceStatus: ComplianceStatus;
  reviewedBy: string | null;
  flaggedForReview: boolean;
}

export interface ModelPrediction {
  modelId: string;
  modelVersion: string;
  precision: number;
  recall: number;
  f1Score: number;
  falsePositiveRate: number;
  falseNegativeRate: number;
  evaluatedAt: string;
  notesProcessed: number;
}

export interface ComplianceStatus {
  isCompliant: boolean;
  hipaaRule: '164.514(b)' | '164.514(e)' | 'PENDING';
  auditTrailComplete: boolean;
  reviewRequired: boolean;
  lastReviewedAt: string | null;
  issues: string[];
}

export interface AuditSummary {
  totalNotesProcessed: number;
  totalPHIEntitiesRemoved: number;
  complianceRate: number;
  averageProcessingTimeMs: number;
  entityTypeDistribution: Array<{ type: PHICategory; count: number; percentage: number }>;
  dailyVolume: Array<{ date: string; count: number }>;
  flaggedForReview: number;
}

export type TabId = 'live-deid' | 'audit-log' | 'compliance';
