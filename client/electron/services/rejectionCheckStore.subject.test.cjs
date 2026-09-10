'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createSqliteDatabase } = require('./sqliteDatabase.cjs');
const { createRejectionCheckStore } = require('./rejectionCheckStore.cjs');

function withStore(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'yibiao-subject-store-'));
  // createSqliteDatabase 会调用 app.once('will-quit', ...)，并返回 { db, close, ... } 包装对象；
  // store 需要的是包装对象里的原生 better-sqlite3 实例。
  const app = { getPath: () => dir, once: () => {} };
  const db = createSqliteDatabase(app).db;
  try {
    const store = createRejectionCheckStore({
      app,
      db,
      fileService: {},
      technicalPlanStore: {},
      taskLogStore: { list: () => [], sync: () => {} },
    });
    // 事务首次写入会补建 meta 行，先跑一次空补丁，保证后续 loadRejectionCheck 可用。
    store.updateRejectionCheckWithoutReload({});
    run(store);
  } finally {
    db.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('补丁里的 subjectProfile 会真正落库', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'pending-confirmation',
        officialName: '',
        aliases: [],
        candidates: [{
          bidDocumentId: 'bid-1',
          name: '北方长风智能科技有限公司',
          confidence: 0.9,
          locationHint: '投标函落款',
          originalText: '投标人：北方长风智能科技有限公司（盖章）',
          roleEvidence: '落款标注投标人',
          aliases: [],
        }],
        preselectIndex: 0,
      },
    });
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'pending-confirmation');
    assert.equal(profile.candidates.length, 1);
    assert.equal(profile.candidates[0].name, '北方长风智能科技有限公司');
  });
});

test('identificationTarget 能存取往返', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'confirmed',
        officialName: '北方长风智能科技有限公司',
        aliases: [],
        candidates: [],
        identificationTarget: { mode: 'single', bidDocumentId: 'bid-2' },
      },
    });
    assert.deepEqual(
      store.loadRejectionCheck().subjectProfile.identificationTarget,
      { mode: 'single', bidDocumentId: 'bid-2' },
    );
  });
});

test('后续补丁未携带 identificationTarget 时保留旧值', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'confirmed', officialName: '甲公司', aliases: [], candidates: [], identificationTarget: { mode: 'all' } },
    });
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'identifying', officialName: '', aliases: [], candidates: [] },
    });
    assert.deepEqual(
      store.loadRejectionCheck().subjectProfile.identificationTarget,
      { mode: 'all' },
    );
  });
});

test('resetSubjectProfile 清空 identificationTarget', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: { status: 'confirmed', officialName: '甲公司', aliases: [], candidates: [], identificationTarget: { mode: 'all' } },
    });
    store.resetSubjectProfile('投标文件已更新，请重新确认投标主体');
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'idle');
    assert.equal(profile.identificationTarget, undefined);
  });
});

test('confirmSubject 未显式传值时保留既有 identificationTarget', () => {
  withStore((store) => {
    store.updateRejectionCheckWithoutReload({
      subjectProfile: {
        status: 'pending-confirmation',
        officialName: '',
        aliases: [],
        candidates: [],
        identificationTarget: { mode: 'single', bidDocumentId: 'bid-1' },
      },
    });
    store.confirmSubject({ officialName: '北方长风智能科技有限公司', aliases: [] });
    const profile = store.loadRejectionCheck().subjectProfile;
    assert.equal(profile.status, 'confirmed');
    assert.deepEqual(profile.identificationTarget, { mode: 'single', bidDocumentId: 'bid-1' });
  });
});
