// 上传步：招标（可选）/投标（必选）两行上传板 + 多维度引导面板。
// 交互语义对齐参考页 DuplicateCheckPage.tsx :1027-1133（R7）。
import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { WorkspaceFile } from '../api/types';
import { formatDate, formatFileSize } from '../lib/format';

const ACCEPT = '.doc,.docx,.pdf,.xlsx,.txt,.md';

export function fileKey(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf('.');
  return dot >= 0 ? fileName.slice(dot) : '';
}

function pillBadge(extension: string): string {
  return extension.replace('.', '').slice(0, 4).toUpperCase() || 'DOC';
}

function pillMeta(size: number, modified: string): string {
  return `${formatFileSize(size)} · ${formatDate(modified)}`;
}

function FilePill({
  badge, name, meta, onRemove, removeDisabled,
}: {
  badge: string;
  name: string;
  meta: string;
  onRemove?: () => void;
  removeDisabled?: boolean;
}) {
  return (
    <span className="file-pill">
      <span className="file-pill-badge">{badge}</span>
      <span className="file-pill-name" title={name}>{name}</span>
      <span className="file-pill-meta">{meta}</span>
      {onRemove && (
        <button
          type="button"
          className="file-pill-remove"
          onClick={onRemove}
          disabled={removeDisabled}
          aria-label={`删除 ${name}`}
        >
          ✕
        </button>
      )}
    </span>
  );
}

interface UploadRowProps {
  index: string;
  title: string;
  note: string;
  /** 上次会话留下的服务端文件（无字节，只读展示） */
  hydratedFiles: WorkspaceFile[];
  /** 本会话选中的文件（可增删，删除即重发全量） */
  sessionFiles: File[];
  collectionLocked: boolean;
  busy: boolean;
  running: boolean;
  onSelect: (files: File[]) => void;
  onRemove: (file: File) => void;
  emptyTitle: string;
  emptyHint: string;
  selectLabel: string;
  emptySelectLabel: string;
}

function UploadRow({
  index, title, note, hydratedFiles, sessionFiles, collectionLocked,
  busy, running, onSelect, onRemove, emptyTitle, emptyHint, selectLabel,
  emptySelectLabel,
}: UploadRowProps) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const disabled = busy || running || (collectionLocked && hydratedFiles.length > 0);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onSelect(files);
  }

  function handlePick(files: FileList | null) {
    if (!files?.length) return;
    onSelect(Array.from(files));
    if (inputRef.current) inputRef.current.value = '';
  }

  const hasAny = hydratedFiles.length > 0 || sessionFiles.length > 0;

  return (
    <div
      className={`upload-row${dragOver ? ' is-dragover' : ''}`}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <div className="upload-row-head">
        <div className="upload-row-title">
          <span className="upload-row-index">{index}</span>
          <strong>{title}</strong>
          <small>{note}</small>
        </div>
        <button
          type="button"
          className="primary-action"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
        >
          {busy ? '选择中...' : sessionFiles.length ? '继续上传' : selectLabel}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(event) => handlePick(event.target.files)}
        />
      </div>

      {hasAny ? (
        <div className="file-list">
          {hydratedFiles.map((file) => (
            <FilePill
              key={`hydrated-${file.id}`}
              badge={pillBadge(extensionOf(file.file_name))}
              name={file.file_name}
              meta={pillMeta(file.size, file.modified_at)}
            />
          ))}
          {sessionFiles.map((file) => (
            <FilePill
              key={fileKey(file)}
              badge={pillBadge(extensionOf(file.name))}
              name={file.name}
              meta={pillMeta(file.size, new Date(file.lastModified).toISOString())}
              onRemove={() => onRemove(file)}
              removeDisabled={busy || running}
            />
          ))}
        </div>
      ) : (
        <div className="upload-empty">
          <strong>{emptyTitle}</strong>
          <p>{emptyHint}</p>
          <button
            type="button"
            className="text-button"
            onClick={() => inputRef.current?.click()}
            disabled={disabled}
          >
            {emptySelectLabel}
          </button>
        </div>
      )}
      {collectionLocked && hydratedFiles.length > 0 && (
        <p className="upload-note">以上文件来自上次会话，不能直接增删；点击工具条「重置」后可重新选择。</p>
      )}
    </div>
  );
}

const guideItems = [
  '同设备、同用户、同一个 WPS 账号、时间相近等问题，一秒锁定。',
  '可选上传招标文件，多份投标文件都引用了招标文件中的内容，不算重复。',
  '图片基于哈希校验，只能识别同一张图片，截图、压缩等相似图片筛不出来。',
];

const dimensions = [
  { title: '元数据', text: '检查设备、账号、编辑时间、作者等隐藏信息。' },
  { title: '目录', text: '比对章节结构和标题顺序，识别模板化复制。' },
  { title: '正文', text: '筛查段落、表格和关键描述的重复内容。' },
  { title: '图片', text: '对原图做哈希校验，定位完全一致的图片。' },
];

export function GuidePanel() {
  return (
    <section className="guide-panel">
      <strong>多维度筛查重复项</strong>
      <div className="dimension-grid">
        {dimensions.map((item) => (
          <article key={item.title}>
            <strong>{item.title}</strong>
            <p>{item.text}</p>
          </article>
        ))}
      </div>
      <ul className="guide-list">
        {guideItems.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </section>
  );
}

export interface UploadStepProps {
  tenderSession: File[];
  bidSession: File[];
  tenderHydrated: WorkspaceFile[];
  bidHydrated: WorkspaceFile[];
  collectionLocked: boolean;
  busy: 'tender' | 'bid' | null;
  running: boolean;
  onSelectTender: (files: File[]) => void;
  onSelectBid: (files: File[]) => void;
  onRemoveTender: (file: File) => void;
  onRemoveBid: (file: File) => void;
}

export default function UploadStep({
  tenderSession, bidSession, tenderHydrated, bidHydrated, collectionLocked,
  busy, running, onSelectTender, onSelectBid, onRemoveTender, onRemoveBid,
}: UploadStepProps) {
  const totalSize = [...tenderSession, ...bidSession]
    .reduce((sum, file) => sum + file.size, 0)
    + [...tenderHydrated, ...bidHydrated]
      .reduce((sum, file) => sum + file.size, 0);

  return (
    <>
      <section className="upload-board">
        <div className="upload-board-head">
          <div>
            <span className="section-kicker">STEP 01</span>
            <h2>选择标书</h2>
          </div>
          <div className="upload-summary">
            <span>
              {(tenderSession.length + tenderHydrated.length) > 0
                ? `${tenderSession.length + tenderHydrated.length} 份招标文件`
                : '未上传招标文件'}
            </span>
            <strong>{bidSession.length + bidHydrated.length} 份投标文件</strong>
            <small>{formatFileSize(totalSize)}</small>
          </div>
        </div>

        <UploadRow
          index="01"
          title="招标文件"
          note="可选，可多份"
          hydratedFiles={tenderHydrated}
          sessionFiles={tenderSession}
          collectionLocked={collectionLocked}
          busy={busy === 'tender'}
          running={running}
          onSelect={onSelectTender}
          onRemove={onRemoveTender}
          emptyTitle="等待招标文件"
          emptyHint="可选。上传后，多份投标文件引用招标文件中的内容不计入重复。"
          selectLabel="上传"
          emptySelectLabel="选择招标文件"
        />

        <UploadRow
          index="02"
          title="投标文件"
          note="必选，可多份"
          hydratedFiles={bidHydrated}
          sessionFiles={bidSession}
          collectionLocked={collectionLocked}
          busy={busy === 'bid'}
          running={running}
          onSelect={onSelectBid}
          onRemove={onRemoveBid}
          emptyTitle="等待投标文件"
          emptyHint="至少上传两份投标文件，用于互相比对重复内容。"
          selectLabel="上传"
          emptySelectLabel="选择投标文件"
        />
      </section>

      <GuidePanel />
    </>
  );
}
