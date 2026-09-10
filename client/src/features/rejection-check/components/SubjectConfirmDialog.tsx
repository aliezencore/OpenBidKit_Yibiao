import { useEffect, useRef, useState } from 'react';
import { AppDialog } from '../../../shared/ui';
import type { SubjectCandidate, SubjectIdentificationTarget } from '../types';

interface SubjectConfirmDialogProps {
  open: boolean;
  candidates: SubjectCandidate[];
  preselectIndex?: number;
  defaultOfficialName: string;
  defaultAliases: string[];
  identificationTarget: SubjectIdentificationTarget;
  bidDocuments: { id: string; fileName: string }[];
  sealEnabled: boolean;
  sealAvailable: boolean;
  identifying?: boolean;
  onIdentificationTargetChange: (target: SubjectIdentificationTarget) => void;
  onIdentify: () => void;
  onSealEnabledChange: (enabled: boolean) => void;
  onConfirm: (payload: { officialName: string; aliases: string[] }) => void;
  onCancel: () => void;
}

/**
 * 投标主体确认弹窗（spec §12.2）：手工填写为主，AI 候选识别为弹窗内可选操作。
 * 任何情况下都必须由用户确认，系统只做自动预选提示。
 */
export function SubjectConfirmDialog({
  open,
  candidates,
  preselectIndex,
  defaultOfficialName,
  defaultAliases,
  identificationTarget,
  bidDocuments,
  sealEnabled,
  sealAvailable,
  identifying = false,
  onIdentificationTargetChange,
  onIdentify,
  onSealEnabledChange,
  onConfirm,
  onCancel,
}: SubjectConfirmDialogProps) {
  const [officialName, setOfficialName] = useState(defaultOfficialName);
  const [aliases, setAliases] = useState<string[]>(defaultAliases);
  const [aliasDraft, setAliasDraft] = useState('');
  const wasOpenRef = useRef(false);

  useEffect(() => {
    // 仅在弹窗由关闭转为打开时灌入初值；识别结果后到时不得覆盖用户已填内容。
    if (open && !wasOpenRef.current) {
      setOfficialName(defaultOfficialName);
      setAliases(defaultAliases);
      setAliasDraft('');
    }
    wasOpenRef.current = open;
  }, [open, defaultOfficialName, defaultAliases]);

  useEffect(() => {
    // 候选后到时：仅在用户尚未填写名称的情况下套用系统预选。
    if (!open || identifying || officialName.trim()) return;
    if (preselectIndex === undefined || preselectIndex < 0) return;
    const candidate = candidates[preselectIndex];
    if (candidate) setOfficialName(candidate.name);
  }, [open, identifying, officialName, preselectIndex, candidates]);

  function addAlias() {
    const value = aliasDraft.trim();
    if (!value || aliases.includes(value)) {
      setAliasDraft('');
      return;
    }
    setAliases([...aliases, value]);
    setAliasDraft('');
  }

  const selectValue = identificationTarget.mode === 'all'
    ? 'all'
    : identificationTarget.bidDocumentId || bidDocuments[0]?.id || '';

  return (
    <AppDialog
      open={open}
      onOpenChange={(next) => { if (!next) onCancel(); }}
      kicker="主体确认"
      title="确认投标主体"
      description="可直接手工填写正式主体和允许简称；也可以让 AI 从指定投标文件中提取候选。任何情况下都需要你确认。"
      actions={(
        <>
          <button type="button" className="secondary-action" onClick={onCancel} disabled={identifying}>
            取消
          </button>
          <button
            type="button"
            className="primary-action"
            disabled={identifying || !officialName.trim()}
            onClick={() => onConfirm({ officialName: officialName.trim(), aliases })}
          >
            {identifying ? '识别中…' : '确认并开始检查'}
          </button>
        </>
      )}
    >
      <div className="subject-confirm-dialog">
        <section className="subject-confirm-dialog__field">
          <h4>AI 识别候选（可选）</h4>
          <p className="muted">
            不想等待时可直接手工填写下方名称。也可以先指定识别范围，让 AI 只扫描需要的投标文件。
          </p>
          <div className="subject-confirm-dialog__identify">
            {bidDocuments.length > 1 && (
              <select
                value={selectValue}
                aria-label="识别范围"
                onChange={(event) => {
                  const value = event.target.value;
                  onIdentificationTargetChange(value === 'all'
                    ? { mode: 'all' }
                    : { mode: 'single', bidDocumentId: value });
                }}
              >
                {bidDocuments.map((document) => (
                  <option key={document.id} value={document.id}>{document.fileName}</option>
                ))}
                <option value="all">全部投标文件</option>
              </select>
            )}
            <button
              type="button"
              className="secondary-action"
              onClick={onIdentify}
              disabled={identifying || !bidDocuments.length}
            >
              {identifying ? '识别中…' : candidates.length ? '重新识别' : 'AI 识别候选'}
            </button>
          </div>
          {bidDocuments.length === 0 && (
            <p className="muted">请先导入至少一份投标文件。</p>
          )}
        </section>

        <section className="subject-confirm-dialog__candidates">
          <h4>识别到的候选主体</h4>
          {candidates.length === 0 && (
            <p className="muted">可直接手工填写下方名称，或点上方「AI 识别候选」从文档中提取。</p>
          )}
          <ul>
            {candidates.map((candidate, index) => (
              <li key={`${candidate.bidDocumentId}-${candidate.name}-${index}`}>
                <label>
                  <input
                    type="radio"
                    name="subject-candidate"
                    checked={officialName === candidate.name}
                    onChange={() => setOfficialName(candidate.name)}
                  />
                  <span className="subject-confirm-dialog__name">{candidate.name}</span>
                  <span className="subject-confirm-dialog__confidence">
                    置信度 {(candidate.confidence * 100).toFixed(0)}%
                    {preselectIndex === index ? '（系统预选）' : ''}
                  </span>
                </label>
                <p className="subject-confirm-dialog__evidence">
                  {candidate.locationHint ? `位置：${candidate.locationHint}　` : ''}
                  {candidate.roleEvidence || ''}
                </p>
                {candidate.originalText && (
                  <blockquote className="subject-confirm-dialog__quote">{candidate.originalText}</blockquote>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>正式投标主体名称</h4>
          <input
            type="text"
            value={officialName}
            placeholder="请输入完整的投标主体名称"
            onChange={(event) => setOfficialName(event.target.value)}
          />
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>允许简称</h4>
          <p className="muted">
            只有这里确认的简称才会被视为投标方本身。系统不会自动把“有限公司”后缀、母子公司或相似名称视为同一主体。
          </p>
          <div className="subject-confirm-dialog__aliases">
            {aliases.map((alias) => (
              <span key={alias} className="subject-confirm-dialog__alias">
                {alias}
                <button type="button" onClick={() => setAliases(aliases.filter((item) => item !== alias))} aria-label={`删除简称 ${alias}`}>
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="subject-confirm-dialog__alias-input">
            <input
              type="text"
              value={aliasDraft}
              placeholder="输入简称后回车添加"
              onChange={(event) => setAliasDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  addAlias();
                }
              }}
            />
            <button type="button" className="secondary-action" onClick={addAlias}>添加</button>
          </div>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>语义代称说明</h4>
          <p className="muted">
            “我公司、本公司、我方、本投标人、投标人、应答人、供应商、响应人”等代称在语义上代表投标方，
            只会帮助模型理解发言角色，不作为公司名称参与字符串比较。
          </p>
        </section>

        <section className="subject-confirm-dialog__field">
          <h4>在线印章检查</h4>
          <label className="subject-confirm-dialog__switch">
            <input
              type="checkbox"
              checked={sealEnabled && sealAvailable}
              disabled={!sealAvailable}
              onChange={(event) => onSealEnabledChange(event.target.checked)}
            />
            <span>启用在线印章检查（可选）</span>
          </label>
          {!sealAvailable && (
            <p className="muted">当前文本模型未开启多模态支持，请到设置页的文本模型中开启“支持多模态”。</p>
          )}
          <p className="muted">
            启用后，筛选出的投标文件图片和必要的附近文字会发送到当前配置的多模态模型服务；客户端不会上传整份 DOCX。
            关闭此项后，正文主体检查仍可正常执行。
          </p>
        </section>
      </div>
    </AppDialog>
  );
}

export default SubjectConfirmDialog;
