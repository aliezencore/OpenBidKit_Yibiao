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

function stripXmlToText(xml) {
  return String(xml || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function extractNearbyText(xml, index) {
  const start = Math.max(0, index - NEARBY_TEXT_RADIUS * 3);
  const end = Math.min(xml.length, index + NEARBY_TEXT_RADIUS * 3);
  const text = stripXmlToText(xml.slice(start, end));
  const half = Math.floor(text.length / 2);
  return text.slice(Math.max(0, half - NEARBY_TEXT_RADIUS), half + NEARBY_TEXT_RADIUS);
}

function extractSectionTitle(xml, index) {
  // 向前找最近一个标题样式段落或加粗短行的纯文本。
  const before = xml.slice(Math.max(0, index - 4000), index);
  const headingMatch = /<w:p\b[^>]*>(?:(?!<\/w:p>)[\s\S])*?<w:t[^>]*>([^<]{2,60})<\/w:t>(?:(?!<\/w:p>)[\s\S])*?<\/w:p>\s*$/.exec(before);
  return headingMatch ? stripXmlToText(headingMatch[1]) : undefined;
}

function classifyAssetKind(widthPx, heightPx, placement) {
  if (widthPx >= PAGE_SCAN_MIN_WIDTH_PX && heightPx >= PAGE_SCAN_MIN_HEIGHT_PX) return 'page-scan';
  return placement === 'inline' || placement === 'anchor' ? 'standalone' : 'unknown';
}

function collectDrawingOccurrences(xml, relationships, partLabel) {
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
      nearbyText: extractNearbyText(xml, drawing.index),
      sectionTitle: extractSectionTitle(xml, drawing.index),
    });
  }
  return occurrences;
}

function collectVmlOccurrences(xml, relationships, partLabel) {
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
      nearbyText: extractNearbyText(xml, match.index),
      sectionTitle: extractSectionTitle(xml, match.index),
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
    occurrences.push(...collectDrawingOccurrences(part.xml, part.rels, part.label));
    occurrences.push(...collectVmlOccurrences(part.xml, part.rels, part.label));
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