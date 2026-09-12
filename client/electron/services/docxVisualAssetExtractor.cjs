'use strict';

const crypto = require('node:crypto');
const AdmZip = require('adm-zip');

const EMU_PER_PX = 9525;
const PT_TO_EMU = 12700;
const PAGE_SCAN_MIN_WIDTH_PX = 500;
const PAGE_SCAN_MIN_HEIGHT_PX = 650;
const NEARBY_TEXT_RADIUS = 160;

const MIME_BY_EXTENSION = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.webp': 'image/webp',
  '.emf': 'image/emf',
  '.wmf': 'image/wmf',
};

function isDocxFilePath(filePath) {
  return /\.docx$/i.test(String(filePath || ''));
}

function parseRelationships(xml) {
  const map = new Map();
  const re = /<Relationship\b[^>]*>/g;
  for (const tag of String(xml || '').matchAll(re)) {
    const id = /\bId="([^"]+)"/.exec(tag[0])?.[1];
    const target = /\bTarget="([^"]+)"/.exec(tag[0])?.[1];
    if (id && target) map.set(id, target.replace(/^\/+/, ''));
  }
  return map;
}

function unescapeXmlEntities(text) {
  return String(text || '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&apos;/g, "'");
}

// 只拼接 <w:t>（可见文本）的内容。
// 不能整段剥标签：<wp:posOffset>1143000</wp:posOffset> 这类坐标的数字**本身就是元素文本**，
// 剥完标签会留下「1143000 132715」这种残渣，既污染喂给模型的上下文，又会被章节编号规则误判成章节。
function extractRunText(block) {
  const source = String(block || '');
  const runs = [];
  const runRe = /<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g;
  let match = runRe.exec(source);
  while (match) {
    runs.push(unescapeXmlEntities(match[1]));
    match = runRe.exec(source);
  }
  return runs.join('').replace(/\s+/g, ' ').trim();
}

const MAX_SECTION_TITLE_LENGTH = 40;
const MAX_NEARBY_TEXT_LENGTH = 200;
const NEARBY_PARAGRAPH_RADIUS = 2;

// 中文标书大量使用纯文本编号当章节，并不使用 Word 标题样式。
const CHAPTER_TEXT_PATTERNS = [
  /^第[一二三四五六七八九十百千0-9]+[章节篇]/,
  /^[一二三四五六七八九十]+、/,
  /^（[一二三四五六七八九十]+）/,
  /^\d+(\.\d+)*[\s、.]/,
];
const HEADING_STYLE_PATTERN = /^(heading\s*[1-9]|标题\s*[1-9一二三四五六七八九十])$/i;
// 章节候选必须含中文，否则纯数字/坐标残渣会命中上面的编号规则。
const CJK_PATTERN = /[\u4e00-\u9fa5]/;

// 一次性把 XML 切成段落数组并预计算特征，避免每张图片都重扫全文。
function buildParagraphIndex(xml) {
  const source = String(xml || '');
  const paragraphs = [];
  const positions = [];
  const paragraphRe = /<w:p\b[^>]*>[\s\S]*?<\/w:p>/g;
  let match = paragraphRe.exec(source);
  while (match) {
    const block = match[0];
    const styleMatch = /<w:pStyle\b[^>]*w:val="([^"]+)"/.exec(block);
    paragraphs.push({
      text: extractRunText(block),
      styleVal: styleMatch ? styleMatch[1] : '',
      bold: /<w:b\b[^>]*\/?>/.test(block),
    });
    positions.push({ start: match.index, end: match.index + block.length });
    match = paragraphRe.exec(source);
  }
  return { paragraphs, positions };
}

function findParagraphPosition(positions, charIndex) {
  for (let index = positions.length - 1; index >= 0; index -= 1) {
    if (charIndex >= positions[index].start && charIndex < positions[index].end) return index;
  }
  return -1;
}

function isHeadingParagraph(paragraph) {
  if (!paragraph || !paragraph.text) return false;
  if (paragraph.styleVal && HEADING_STYLE_PATTERN.test(String(paragraph.styleVal).trim())) return true;
  if (paragraph.text.length > MAX_SECTION_TITLE_LENGTH) return false;
  if (!CJK_PATTERN.test(paragraph.text)) return false;
  return CHAPTER_TEXT_PATTERNS.some((pattern) => pattern.test(paragraph.text));
}

// 向前全篇回溯：先找标题样式/编号段落，再退一步找加粗短段落，都没有就返回 undefined。
function extractSectionTitle(paragraphs, paragraphPosition) {
  for (let index = paragraphPosition - 1; index >= 0; index -= 1) {
    if (!paragraphs[index]?.text) continue;
    if (isHeadingParagraph(paragraphs[index])) return paragraphs[index].text;
  }
  for (let index = paragraphPosition - 1; index >= 0; index -= 1) {
    const paragraph = paragraphs[index];
    if (!paragraph?.text || paragraph.text.length > MAX_SECTION_TITLE_LENGTH) continue;
    if (paragraph.bold && CJK_PATTERN.test(paragraph.text)) return paragraph.text;
  }
  return undefined;
}

// 取图片所在段落文本；图片独占段落时取前后各最多 2 个有文本的段落。
function extractNearbyText(paragraphs, paragraphPosition) {
  if (paragraphPosition < 0) return '';
  const own = paragraphs[paragraphPosition]?.text || '';
  if (own) return own.slice(0, MAX_NEARBY_TEXT_LENGTH);

  const parts = [];
  for (let offset = 1; offset <= NEARBY_PARAGRAPH_RADIUS; offset += 1) {
    const text = paragraphs[paragraphPosition - offset]?.text;
    if (text) parts.push(text);
  }
  for (let offset = 1; offset <= NEARBY_PARAGRAPH_RADIUS; offset += 1) {
    const text = paragraphs[paragraphPosition + offset]?.text;
    if (text) parts.push(text);
  }
  return parts.join(' ').slice(0, MAX_NEARBY_TEXT_LENGTH);
}

function classifyAssetKind(widthPx, heightPx, placement) {
  if (widthPx >= PAGE_SCAN_MIN_WIDTH_PX && heightPx >= PAGE_SCAN_MIN_HEIGHT_PX) return 'page-scan';
  return placement === 'inline' || placement === 'anchor' ? 'standalone' : 'unknown';
}

function collectDrawingOccurrences(xml, relationships, partLabel, paragraphIndex) {
  const { paragraphs, positions } = paragraphIndex;
  const occurrences = [];
  const drawingRe = /<w:drawing>[\s\S]*?<\/w:drawing>/g;
  for (const drawing of String(xml || '').matchAll(drawingRe)) {
    const block = drawing[0];
    const embed = /\ba:blip\b[^>]*\br:embed="([^"]+)"/.exec(block)?.[1]
      ?? /\br:embed="([^"]+)"/.exec(block)?.[1];
    if (!embed) continue;
    const target = relationships.get(embed);
    if (!target || !/^word\/media\//.test(target) && !/^media\//.test(target)) continue;
    const placement = /<wp:anchor\b/.test(block) ? 'anchor' : 'inline';
    const extent = /<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/.exec(block)
      ?? /<wp:extent\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"/.exec(block);
    let widthPx = 0;
    let heightPx = 0;
    if (extent) {
      widthPx = Math.round(Number(extent[1]) / EMU_PER_PX);
      heightPx = Math.round(Number(extent[2]) / EMU_PER_PX);
    }
    occurrences.push({
      mediaName: target.replace(/^word\//, ''),
      relationshipId: embed,
      placement,
      width: widthPx,
      height: heightPx,
      assetKind: classifyAssetKind(widthPx, heightPx, placement),
      locationHint: partLabel,
      nearbyText: extractNearbyText(paragraphs, findParagraphPosition(positions, drawing.index)),
      sectionTitle: extractSectionTitle(paragraphs, findParagraphPosition(positions, drawing.index)),
    });
  }
  return occurrences;
}

function collectVmlOccurrences(xml, relationships, partLabel, paragraphIndex) {
  const { paragraphs, positions } = paragraphIndex;
  const occurrences = [];
  const vmlRe = /<v:imagedata\b[^>]*\br:id="([^"]+)"[^>]*\/?>/g;
  for (const match of String(xml || '').matchAll(vmlRe)) {
    const target = relationships.get(match[1]);
    if (!target || !/^word\/media\//.test(target) && !/^media\//.test(target)) continue;
    // VML 尺寸在外层 v:shape 的 style 中，单位 pt。
    const shapeStart = xml.lastIndexOf('<v:shape', match.index);
    const styleBlock = shapeStart >= 0 ? xml.slice(shapeStart, match.index) : '';
    const widthPt = Number(/width:([\d.]+)pt/i.exec(styleBlock)?.[1] || 0);
    const heightPt = Number(/height:([\d.]+)pt/i.exec(styleBlock)?.[1] || 0);
    const widthPx = Math.round((widthPt * PT_TO_EMU) / EMU_PER_PX);
    const heightPx = Math.round((heightPt * PT_TO_EMU) / EMU_PER_PX);
    occurrences.push({
      mediaName: target.replace(/^word\//, ''),
      relationshipId: match[1],
      placement: 'inline',
      width: widthPx,
      height: heightPx,
      assetKind: classifyAssetKind(widthPx, heightPx, 'inline'),
      locationHint: partLabel,
      nearbyText: extractNearbyText(paragraphs, findParagraphPosition(positions, match.index)),
      sectionTitle: extractSectionTitle(paragraphs, findParagraphPosition(positions, match.index)),
    });
  }
  return occurrences;
}

async function extractDocxVisualAssets(docxFilePath) {
  const zip = new AdmZip(docxFilePath);
  const readPart = (name) => {
    const entry = zip.getEntry(name);
    return entry ? entry.getData().toString('utf8') : null;
  };

  const documentXml = readPart('word/document.xml');
  if (!documentXml) throw new Error('DOCX 缺少 word/document.xml，无法提取图片');

  const parts = [{ xml: documentXml, rels: parseRelationships(readPart('word/_rels/document.xml.rels') || ''), label: '正文' }];
  for (const entry of zip.getEntries()) {
    const name = entry.entryName;
    const partMatch = /^word\/(header\d*|footer\d*)\.xml$/.exec(name);
    if (!partMatch) continue;
    const relsName = `word/_rels/${partMatch[1]}.xml.rels`;
    parts.push({
      xml: entry.getData().toString('utf8'),
      rels: parseRelationships(readPart(relsName) || ''),
      label: partMatch[1].startsWith('header') ? '页眉' : '页脚',
    });
  }

  const occurrences = [];
  for (const part of parts) {
    const paragraphIndex = buildParagraphIndex(part.xml);
    occurrences.push(...collectDrawingOccurrences(part.xml, part.rels, part.label, paragraphIndex));
    occurrences.push(...collectVmlOccurrences(part.xml, part.rels, part.label, paragraphIndex));
  }

  const mediaFiles = [];
  for (const entry of zip.getEntries()) {
    if (!/^word\/media\/[^/]+$/.test(entry.entryName)) continue;
    const buffer = entry.getData();
    const extension = /\.[^.]+$/.exec(entry.entryName)?.[0]?.toLowerCase() || '';
    mediaFiles.push({
      mediaName: entry.entryName.replace(/^word\//, ''),
      buffer,
      contentHash: crypto.createHash('sha256').update(buffer).digest('hex'),
      mimeType: MIME_BY_EXTENSION[extension] || 'application/octet-stream',
    });
  }

  return { occurrences, mediaFiles };
}

module.exports = {
  extractDocxVisualAssets,
  isDocxFilePath,
};