'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const AdmZip = require('adm-zip');
const { extractDocxVisualAssets, isDocxFilePath } = require('./docxVisualAssetExtractor.cjs');

// 1x1 PNG（透明像素），用于充当媒体文件。
const PNG_BUFFER = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

function buildMinimalDocx() {
  const zip = new AdmZip();
  zip.addFile('word/document.xml', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
  xmlns:v="urn:schemas-microsoft-com:vml">
  <w:body>
    <w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>
    <w:p><w:r><w:t>致：某某招标代理公司。我公司郑重承诺：</w:t></w:r></w:p>
    <w:p><w:r>
      <w:drawing><wp:inline distT="0" distB="0"><wp:extent cx="1905000" cy="635000"/>
        <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:blipFill><a:blip r:embed="rId11"/></pic:blipFill>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing>
    </w:r></w:p>
    <w:p><w:r><w:t>授权委托书：本授权书自签订之日起生效。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r>
      <w:drawing><wp:anchor behindDoc="1"><wp:extent cx="5715000" cy="7620000"/>
        <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:blipFill><a:blip r:embed="rId11"/></pic:blipFill>
        </pic:pic></a:graphicData></a:graphic>
      </wp:anchor></w:drawing>
      <v:shape style="width:60pt;height:60pt"><v:imagedata r:id="rId12"/></v:shape>
      </w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:r><w:t>报价表：含税总价 1234567.89 元。</w:t></w:r></w:p>
  </w:body>
</w:document>`, 'utf8'));
  zip.addFile('word/_rels/document.xml.rels', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId11" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/seal-a.png"/>
  <Relationship Id="rId12" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo-b.png"/>
</Relationships>`, 'utf8'));
  zip.addFile('word/media/seal-a.png', PNG_BUFFER);
  zip.addFile('word/media/logo-b.png', PNG_BUFFER);
  return zip;
}

function writeFixture(zip, fileName) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-extract-test-'));
  const filePath = path.join(dir, fileName);
  zip.writeZip(filePath);
  return filePath;
}

test('isDocxFilePath 识别 .docx（大小写不敏感）', () => {
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.DOCX'), true);
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.docx'), true);
  assert.strictEqual(isDocxFilePath('C:/a/b/投标文件.pdf'), false);
});

test('提取 inline/anchor/VML 引用并保留重复图片的每个出现位置', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'bid.docx');
  const { occurrences, mediaFiles } = await extractDocxVisualAssets(filePath);

  // rId11 出现两次（inline + anchor），rId12 出现一次（VML）。
  assert.strictEqual(occurrences.filter((item) => item.mediaName === 'media/seal-a.png').length, 2);
  assert.strictEqual(occurrences.filter((item) => item.mediaName === 'media/logo-b.png').length, 1);

  const inline = occurrences.find((item) => item.placement === 'inline');
  const anchor = occurrences.find((item) => item.placement === 'anchor');
  assert.ok(inline, '缺少 inline 引用');
  assert.ok(anchor, '缺少 anchor 引用');

  // EMU→px：1905000/9525=200，635000/9525≈66。
  assert.strictEqual(inline.width, 200);
  // VML pt→px：60pt×12700 EMU/9525=80px。
  const vml = occurrences.find((item) => item.mediaName === 'media/logo-b.png');
  assert.strictEqual(vml.width, 80);

  // 大尺寸 anchor 判定 page-scan；小图 standalone。
  assert.strictEqual(anchor.assetKind, 'page-scan');
  assert.strictEqual(inline.assetKind, 'standalone');

  // 附近文字映射包含上下文线索。
  assert.ok(inline.nearbyText.includes('承诺') || inline.nearbyText.includes('投标函'), 'inline 附近文字应包含上下文');
  assert.ok(vml.nearbyText.length >= 0);

  // 媒体文件读取 + 内容哈希：同一 buffer 只按文件名区分，hash 相同。
  assert.strictEqual(mediaFiles.length, 2);
  const seal = mediaFiles.find((item) => item.mediaName === 'media/seal-a.png');
  assert.strictEqual(seal.mimeType, 'image/png');
  assert.strictEqual(seal.contentHash, mediaFiles.find((item) => item.mediaName === 'media/logo-b.png').contentHash);
});

test('页眉页脚中的图片也会被提取', async () => {
  const zip = buildMinimalDocx();
  zip.addFile('word/header1.xml', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:p><w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="952500"/>
    <a:graphic><a:graphicData><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
      <pic:blipFill><a:blip r:embed="rId21"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>
  </wp:inline></w:drawing></w:r></w:p>
</w:hdr>`, 'utf8'));
  zip.addFile('word/_rels/header1.xml.rels', Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId21" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/header-logo.png"/>
</Relationships>`, 'utf8'));
  zip.addFile('word/media/header-logo.png', PNG_BUFFER);
  const filePath = writeFixture(zip, 'bid-with-header.docx');
  const { occurrences, mediaFiles } = await extractDocxVisualAssets(filePath);
  assert.ok(occurrences.some((item) => item.mediaName === 'media/header-logo.png'));
  assert.ok(mediaFiles.some((item) => item.mediaName === 'media/header-logo.png'));
});

test('非 zip 或缺失关键 part 时抛错', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-extract-test-'));
  const notZip = path.join(dir, 'bad.docx');
  fs.writeFileSync(notZip, 'not a zip', 'utf8');
  await assert.rejects(() => extractDocxVisualAssets(notZip));
  const emptyZip = path.join(dir, 'empty.docx');
  new AdmZip().writeZip(emptyZip);
  await assert.rejects(() => extractDocxVisualAssets(emptyZip));
});

function withRewrittenDocumentXml(zip, rewrite) {
  const xml = zip.getEntry('word/document.xml').getData().toString('utf8');
  zip.updateFile('word/document.xml', Buffer.from(rewrite(xml), 'utf8'));
  return zip;
}

test('nearbyText 是可读文本，不含 XML 片段', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'nearby.docx');
  const { occurrences } = await extractDocxVisualAssets(filePath);
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.ok(inline.nearbyText.includes('承诺') || inline.nearbyText.includes('投标函'), inline.nearbyText);
  assert.strictEqual(
    /w:rsid|<wp:|w:val|w:drawing|effectExtent/.test(inline.nearbyText),
    false,
    `nearbyText 不应含 XML 片段：${inline.nearbyText}`,
  );
  assert.ok(inline.nearbyText.length <= 200, `nearbyText 应被截断，实际长度 ${inline.nearbyText.length}`);
});

test('章节标题取命中编号模式的最近段落', async () => {
  const filePath = writeFixture(buildMinimalDocx(), 'section-pattern.docx');
  const { occurrences } = await extractDocxVisualAssets(filePath);
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '第一章 投标函');
});

test('章节标题优先取标题样式段落（即使文本没有编号）', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>',
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>投标函</w:t></w:r></w:p>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-style.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '投标函');
});

test('无标题样式也无编号段落时不返回正文句子', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>',
    '<w:p><w:r><w:t>我方已认真阅读本项目招标文件的全部内容并愿意按照其要求参加本次投标活动</w:t></w:r></w:p>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-none.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, undefined, `不应把正文句子当章节：${inline.sectionTitle}`);
});

test('章节回溯不受 4000 字符距离限制', async () => {
  const filler = Array.from(
    { length: 60 },
    (_item, index) => `<w:p><w:r><w:t>填充段落${index}：本段用于拉长文档，说明我方完全响应招标文件的各项要求与条件。</w:t></w:r></w:p>`,
  ).join('');
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml
    .replace('<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>', '<w:p><w:r><w:t>第七章 售后服务</w:t></w:r></w:p>')
    .replace('<w:p><w:r><w:t>致：某某招标代理公司。我公司郑重承诺：</w:t></w:r></w:p>', `<w:p><w:r><w:t>致：某某招标代理公司。</w:t></w:r></w:p>${filler}`));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'section-far.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(inline.sectionTitle, '第七章 售后服务');
});

// 真实标书里 <wp:posOffset>1143000</wp:posOffset> 的**数字本身就是元素文本**，
// 整段剥标签会留下坐标残渣 —— 既污染喂给模型的上下文，又会被章节编号规则误判成章节。
test('坐标数字不会进入附近文本（只取 <w:t> 可见文本）', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<wp:inline distT="0" distB="0">',
    '<wp:inline distT="0" distB="0"><wp:positionH relativeFrom="column"><wp:posOffset>1143000</wp:posOffset></wp:positionH><wp:positionV relativeFrom="paragraph"><wp:posOffset>132715</wp:posOffset></wp:positionV>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'posoffset.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.strictEqual(/\d{4,}/.test(inline.nearbyText), false, `附近文本不应含坐标数字：${inline.nearbyText}`);
  assert.strictEqual(inline.nearbyText.includes('1143000'), false, inline.nearbyText);
  assert.ok(inline.nearbyText.includes('承诺') || inline.nearbyText.includes('投标函'), inline.nearbyText);
});

test('纯数字段不会被当成章节标题', async () => {
  const zip = withRewrittenDocumentXml(buildMinimalDocx(), (xml) => xml.replace(
    '<w:p><w:r><w:t>第一章 投标函</w:t></w:r></w:p>',
    '<w:p><w:r><w:t>1143000 132715 x</w:t></w:r></w:p>',
  ));
  const { occurrences } = await extractDocxVisualAssets(writeFixture(zip, 'numeric-section.docx'));
  const inline = occurrences.find((item) => item.placement === 'inline');
  assert.notStrictEqual(inline.sectionTitle, '1143000 132715 x');
  assert.strictEqual(inline.sectionTitle, undefined, `无中文的段落不该当章节：${inline.sectionTitle}`);
});