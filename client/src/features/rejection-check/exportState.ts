import type { LogicCheckResultState, RejectionCheckResultState, SubjectCheckResultState, TypoCheckResultState } from './types';

type ExportableResultState = RejectionCheckResultState | TypoCheckResultState | LogicCheckResultState | SubjectCheckResultState;

function isCurrentExportableResult(result: ExportableResultState, expectedSignature: string) {
  return Boolean(
    expectedSignature
    && result.inputSignature === expectedSignature
    && (result.status === 'success' || result.findings.length > 0),
  );
}

export function hasExportableRejectionResults({
  rejectionCheckResult,
  typoCheckResult,
  logicCheckResult,
  subjectCheckResult,
  rejectionInputSignature,
  bidSignature,
  subjectInputSignature,
}: {
  rejectionCheckResult: RejectionCheckResultState;
  typoCheckResult: TypoCheckResultState;
  logicCheckResult: LogicCheckResultState;
  subjectCheckResult?: SubjectCheckResultState;
  rejectionInputSignature: string;
  bidSignature: string;
  subjectInputSignature?: string;
}) {
  return isCurrentExportableResult(rejectionCheckResult, rejectionInputSignature)
    || isCurrentExportableResult(typoCheckResult, bidSignature)
    || isCurrentExportableResult(logicCheckResult, bidSignature)
    || Boolean(subjectCheckResult && isCurrentExportableResult(subjectCheckResult, subjectInputSignature || ''));
}
