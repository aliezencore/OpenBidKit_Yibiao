export type RejectionDocumentRole = 'tender' | 'bid';

export type RejectionDocumentTabId = 'tender' | string;

export type RejectionDocumentSource = 'upload' | 'technical-plan';

export type RejectionCheckStep = 'documents' | 'items' | 'results';

export type RejectionResultTab = 'analysis' | 'custom';

export type RejectionCheckResultTab = 'rejection' | 'typo' | 'logic' | 'subject';

export type RejectionExtractionStatus = 'idle' | 'running' | 'success' | 'error';

export type RejectionExtractionSource = 'ai' | 'technical-plan';

export type RejectionCheckRunStatus = 'idle' | 'running' | 'success' | 'error';

export type RejectionFindingType = 'invalidBid' | 'rejectionItem';

export type RejectionFindingSeverity = 'high' | 'medium' | 'low';

export type RejectionBackgroundTaskType = 'rejection-items-extraction' | 'rejection-check-run' | 'bid-subject-identification';

export type RejectionBackgroundTaskStatus = 'running' | 'success' | 'error';

export interface RejectionBackgroundTaskState {
  task_id: string;
  type: RejectionBackgroundTaskType;
  status: RejectionBackgroundTaskStatus;
  progress: number;
  logs: string[];
  started_at: string;
  updated_at: string;
  error?: string;
}

export interface RejectionDocumentContent {
  id: string;
  role: RejectionDocumentRole;
  fileName: string;
  content: string;
  source: RejectionDocumentSource;
  parserLabel?: string;
  importedAt: string;
}

export interface RejectionCheckWorkspaceState {
  tenderDocument: RejectionDocumentContent | null;
  tenderDocuments: RejectionDocumentContent[];
  bidDocuments: RejectionDocumentContent[];
  activeDocumentTab: RejectionDocumentTabId;
  step?: RejectionCheckStep;
  activeResultTab?: RejectionResultTab;
  activeCheckResultTab?: RejectionCheckResultTab;
  invalidBidAndRejectionItems?: RejectionExtractionState;
  customCheckItems?: string;
  checkOptions?: RejectionCheckOptions;
  rejectionCheckResult?: RejectionCheckResultState;
  typoCheckResult?: TypoCheckResultState;
  logicCheckResult?: LogicCheckResultState;
  subjectCheckResult?: SubjectCheckResultState;
  subjectProfile?: SubjectProfileState;
  extractionTask?: RejectionBackgroundTaskState;
  checkTask?: RejectionBackgroundTaskState;
  subjectIdentificationTask?: RejectionBackgroundTaskState;
}

export type RejectionCheckWorkspacePatch = Omit<Partial<RejectionCheckWorkspaceState>,
  'rejectionCheckResult' | 'typoCheckResult' | 'logicCheckResult' | 'subjectCheckResult'> & {
  rejectionCheckResult?: Partial<RejectionCheckResultState>;
  typoCheckResult?: Partial<TypoCheckResultState>;
  logicCheckResult?: Partial<LogicCheckResultState>;
  subjectCheckResult?: Partial<SubjectCheckResultState>;
};

export interface RejectionCheckOptions {
  rejectionCheck: boolean;
  typoCheck: boolean;
  logicCheck: boolean;
  subjectCheck: boolean;
  subjectSealCheck: boolean;
}

export interface RejectionExtractionState {
  status: RejectionExtractionStatus;
  content: string;
  source?: RejectionExtractionSource;
  tenderSignature?: string;
  updatedAt?: string;
  error?: string;
}

export interface RejectionCheckFinding {
  id: string;
  bidDocumentId: string;
  type: RejectionFindingType;
  severity: RejectionFindingSeverity;
  title: string;
  summary: string;
  requirement: string;
  bidEvidence: string;
  riskReason: string;
  suggestion: string;
}

export interface RejectionCheckResultState {
  status: RejectionCheckRunStatus;
  findings: RejectionCheckFinding[];
  inputSignature?: string;
  activeFindingId?: string;
  progressMessage?: string;
  updatedAt?: string;
  error?: string;
}

export interface TypoCheckFinding {
  id: string;
  bidDocumentId: string;
  wrongText: string;
  correctText: string;
  originalExcerpt: string;
  reason: string;
  locationHint?: string;
}

export interface TypoCheckResultState {
  status: RejectionCheckRunStatus;
  findings: TypoCheckFinding[];
  inputSignature?: string;
  activeFindingId?: string;
  progressMessage?: string;
  updatedAt?: string;
  error?: string;
}

export interface LogicCheckFinding {
  id: string;
  bidDocumentId: string;
  title: string;
  originalText: string;
  locationHint: string;
  fallacyReason: string;
  suggestion: string;
}

export interface LogicCheckResultState {
  status: RejectionCheckRunStatus;
  findings: LogicCheckFinding[];
  inputSignature?: string;
  activeFindingId?: string;
  progressMessage?: string;
  updatedAt?: string;
  error?: string;
}

export interface RejectionRiskItem {
  id: string;
  title: string;
  source: string;
  suggestion: string;
  severity: 'low' | 'medium' | 'high';
}

export interface RejectionCheckReport {
  passed: boolean;
  risks: RejectionRiskItem[];
}

export type SubjectProfileStatus = 'idle' | 'identifying' | 'pending-confirmation' | 'confirmed' | 'error';

export interface SubjectIdentificationTarget {
  mode: 'single' | 'all';
  bidDocumentId?: string;
}

export type SubjectFindingLevel = 'confirmed-mismatch' | 'needs-review';

export type SubjectFindingSource = 'text' | 'seal';

export type SubjectLocationKind = 'body' | 'table' | 'signature' | 'standalone-image' | 'scanned-page';

export type SubjectVisionStatus = 'disabled' | 'pending' | 'running' | 'success' | 'partial' | 'unavailable' | 'error';

export interface SubjectVisionStats {
  totalAssets: number;
  candidateAssets: number;
  inspectedAssets: number;
  sealAssets: number;
  skippedAssets: number;
  reviewAssets: number;
  failedAssets: number;
}

export interface SubjectCandidate {
  bidDocumentId: string;
  name: string;
  confidence: number;
  locationHint: string;
  originalText: string;
  roleEvidence: string;
  aliases: string[];
}

export interface SubjectProfileState {
  status: SubjectProfileStatus;
  officialName: string;
  aliases: string[];
  candidates: SubjectCandidate[];
  preselectIndex?: number;
  identificationTarget?: SubjectIdentificationTarget;
  bidSignature?: string;
  confirmedAt?: string;
  error?: string;
  updatedAt?: string;
}

export interface SubjectCheckFinding {
  id: string;
  bidDocumentId: string;
  level: SubjectFindingLevel;
  source: SubjectFindingSource;
  expectedSubject: string;
  detectedSubject: string;
  locationKind: SubjectLocationKind;
  title: string;
  summary: string;
  locationHint: string;
  originalText: string;
  roleReason: string;
  mismatchReason: string;
  suggestion: string;
  confidence: number;
  visualAssetId?: string;
  assetRelativePath?: string;
  sealCropRelativePath?: string;
}

export interface SubjectCheckResultState {
  status: RejectionCheckRunStatus;
  findings: SubjectCheckFinding[];
  inputSignature?: string;
  activeFindingId?: string;
  progressMessage?: string;
  updatedAt?: string;
  error?: string;
  stats?: {
    visionStatus: SubjectVisionStatus;
    visionStats?: SubjectVisionStats;
    visionMessage?: string;
    confirmedMismatchCount?: number;
    needsReviewCount?: number;
  };
}
