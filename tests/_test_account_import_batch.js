/* 批量导入账号 JSON 的前端契约。
 *
 * 账号导入一次只能选一个文件时，运营要导入十几个导出文件就得点十几轮；
 * 每轮都重新预览、确认。这里钉住批量入口与「全有或全无」的校验顺序：
 *
 *   1. 文件选择器带 multiple；
 *   2. 多个文件按选择顺序解析，单个对象 / accounts 数组都接受；
 *   3. 只发一次 dryRun，data 是合并后的裸数组；
 *   4. 任一文件非法时，不保留任何待导入行，也不发 dryRun；
 *   5. 预览按钮统计合并后的新增 / 覆盖数量。
 *
 * Run with Node: node tests/_test_account_import_batch.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const html = dashboardHtml();
const script = dashboardScript();

// 1. 结构：入口在账号工具栏，且 picker 支持多选。
assert.ok(/<button class="sec" onclick="openImport\(\)">导入<\/button>/.test(html),
  '账号工具栏缺少「导入」入口');
assert.ok(/picker\.multiple = true;/.test(script), '文件选择器必须启用 multiple');
assert.ok(/readImportFiles\(files\)/.test(script), '选择器应把整组文件交给 readImportFiles');

const calls = [];
let dryRunReply = {result: {added: ['a', 'b'], updated: [], skipped: [], invalid: []}};

dom.installDom({
  fetch: (url, options) => {
    const body = options && options.body ? JSON.parse(options.body) : {};
    calls.push({url: String(url), method: (options && options.method) || 'GET', body: body});
    if(String(url) === '/accounts/import') {
      const payload = {result: dryRunReply.result, count: body.data ? body.data.length : 0};
      return Promise.resolve({status: 200, ok: true,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload))});
    }
    return Promise.resolve({status: 200, ok: true,
      json: () => Promise.resolve({}), text: () => Promise.resolve('{}')});
  },
});

const realLog = console.log;
console.log = () => {};
let api;
try {
  api = new Function(script + `
    return {
      openImport: openImport,
      readImportFiles: readImportFiles,
      renderImportPreview: renderImportPreview,
      rows: () => importRows,
      fileCount: () => importFileCount,
      parseImportFile: parseImportFile,
    };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
console.log = realLog;

const file = (name, value) => ({name: name, text: () => Promise.resolve(
  typeof value === 'string' ? value : JSON.stringify(value))});
const byId = id => document.getElementById(id);

(async () => {
  let checks = 0;
  const check = (label, cond, extra) => {
    checks += 1;
    assert.ok(cond, label + (extra !== undefined ? '  [' + extra + ']' : ''));
  };

  // 2. 多个文件按顺序合并：一个导出文档 + 一个裸单账号对象。
  calls.length = 0;
  byId('importGroup').value = 'Batch A';
  await api.readImportFiles([
    file('first.json', {accounts: [{uid: 'a', accessToken: 'x.y.z'}]}),
    file('second.json', {uid: 'b', accessToken: 'x.y.z'}),
  ]);
  check('合并后保留全部行', api.rows() && api.rows().length === 2,
    api.rows() && api.rows().length);
  check('记录文件数', api.fileCount() === 2, api.fileCount());
  check('只发一次 dryRun', calls.length === 1 &&
    calls[0].url === '/accounts/import' && calls[0].body.dryRun === true,
    JSON.stringify(calls));
  check('dryRun 收到合并后的裸数组',
    Array.isArray(calls[0].body.data) && calls[0].body.data.length === 2 &&
    calls[0].body.data[0].uid === 'a' && calls[0].body.data[1].uid === 'b',
    JSON.stringify(calls[0].body.data));
  check('导入分组随 dryRun 提交', calls[0].body.group === 'Batch A',
    calls[0].body.group);
  check('预览显示批量文件与合并条数',
    byId('importBody').innerHTML.includes('2 个文件') &&
    byId('importBody').innerHTML.includes('共 2 条'),
    byId('importBody').innerHTML);
  check('预览按钮按合并后的数量启用',
    byId('btnImportCommit').disabled === false &&
    byId('btnImportCommit').textContent.includes('2'),
    byId('btnImportCommit').textContent);

  // 3. 一个文件坏掉：整体中止，不清掉错误提示，也不发请求。
  calls.length = 0;
  await api.readImportFiles([
    file('good.json', {uid: 'a', accessToken: 'x.y.z'}),
    file('bad.json', '{not json'),
  ]);
  check('非法文件时没有待导入行', api.rows() === null, api.rows());
  check('非法文件时不发 dryRun', calls.length === 0, JSON.stringify(calls));
  check('错误提示点名具体文件',
    byId('importBody').innerHTML.includes('bad.json') &&
    byId('importBody').innerHTML.includes('不是合法 JSON'),
    byId('importBody').innerHTML);
  check('非法文件时确认按钮禁用', byId('btnImportCommit').disabled === true);

  // 4. 单文件仍按旧路径工作，文件名显示在预览里。
  calls.length = 0;
  await api.readImportFiles([file('single.json', [{uid: 'a', accessToken: 'x.y.z'}])]);
  check('单文件仍可导入', api.rows() && api.rows().length === 1);
  check('单文件预览显示文件名',
    byId('importBody').innerHTML.includes('single.json'),
    byId('importBody').innerHTML);

  // 5. 空文档被拒绝。
  calls.length = 0;
  await api.readImportFiles([file('empty.json', {accounts: []})]);
  check('空账号列表被拒绝', api.rows() === null && calls.length === 0,
    byId('importBody').innerHTML);

  console.log('account import batch assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.error(e && e.stack || e);
  process.exit(1);
});
