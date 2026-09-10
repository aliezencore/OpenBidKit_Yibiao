'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { createTaskService } = require('./taskService.cjs');

test('confirmBidSubject 会把 identificationTarget 原样转发给 Store', () => {
  let captured;
  const taskService = createTaskService({
    aiService: {},
    agentService: {},
    autoConfirmationService: {},
    technicalPlanStore: { loadTechnicalPlan: () => ({}) },
    rejectionCheckStore: {
      confirmSubject: (payload) => { captured = payload; return { success: true }; },
      loadRejectionCheck: () => ({ subjectProfile: {} }),
    },
    duplicateCheckStore: { loadDuplicateCheck: () => ({}) },
    feasibilityReportStore: { loadFeasibilityReport: () => ({}) },
    knowledgeBaseService: {},
    duplicateCheckService: {},
    openXmlHelperService: {},
  });
  const target = { mode: 'all' };
  taskService.confirmBidSubject({ officialName: '甲公司', aliases: ['甲'], identificationTarget: target });
  assert.deepEqual(captured, { officialName: '甲公司', aliases: ['甲'], identificationTarget: target });
});
