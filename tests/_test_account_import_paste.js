/* 导入账号弹窗的两个入口：选文件 / 粘贴 JSON。
 *
 * 运营手上常常是一段从别处复制来的账号 JSON（聊天记录、面板导出的片段），
 * 为了导入它先存成一个文件纯属多余。两个入口必须共用同一份预检与预览，
 * 否则「贴进来的」和「选进来的」会走出两套行为：
 *
 *   1. 弹窗里两个入口都在，切换只换面板，不换语义；
 *   2. 粘贴文本与文件走同一个 /accounts/import?dryRun 预检；
 *   3. 导出文档 / 账号数组 / 单个账号对象都能贴；
 *   4. 坏 JSON、空文本都不发请求，并清空待导入行；
 *   5. 切换入口会作废上一份预览，免得按错「确认导入」；
 *   6. 关窗与开窗都清空粘贴框（里面就是凭证）。
 *
 * Run with Node: node tests/_test_account_import_paste.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const html = dashboardHtml();
const script = dashboardScript();

// 1. 结构：两个入口按钮、粘贴框、解析按钮、两块面板。
assert.ok(/id="importModeFile"[^>]*onclick="setImportMode\('file'\)"[^>]*>打开文件</.test(html),
  '弹窗缺少「打开文件」入口');
assert.ok(/id="importModePaste"[^>]*onclick="setImportMode\('paste'\)"[^>]*>粘贴 JSON</.test(html),
  '弹窗缺少「粘贴 JSON」入口');
assert.ok(/<textarea id="importPasteInput"/.test(html), '弹窗缺少粘贴框');
assert.ok(/id="btnImportParse" onclick="parseImportPaste\(\)"/.test(html),
  '粘贴入口缺少「解析并预览」按钮');
assert.ok(/<div id="importFilePane"/.test(html) && /<div id="importPastePane"/.test(html),
  '两块面板必须是独立容器');
assert.ok(/id="btnImportPick" onclick="pickImportFiles\(\)"/.test(html),
  '文件面板要有重新选文件的按钮');
// 两个入口共用一个预览区。
assert.ok((html.match(/id="importBody"/g) || []).length === 1, '预览区应当只有一份');

const calls = [];
let dryRunReply = {result: {added: ['a'], updated: [], skipped: [], invalid: []}};

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
      closeImport: closeImport,
      setImportMode: setImportMode,
      pickImportFiles: pickImportFiles,
      parseImportPaste: parseImportPaste,
      readImportFiles: readImportFiles,
      renderImportPreview: renderImportPreview,
      rows: () => importRows,
      fileCount: () => importFileCount,
      mode: () => importMode,
    };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
console.log = realLog;

const byId = id => dom.byId(id);
// 元素是懒创建的：先按 id 摸一遍，断言里读到的才是面板写过的那个实例。
['importGroup', 'importBody', 'btnImportCommit', 'importPasteInput',
 'importFilePane', 'importPastePane', 'importModeFile', 'importModePaste',
 'importModal', 'importOverwrite'].forEach(id => document.getElementById(id));

(async () => {
  let checks = 0;
  const check = (label, cond, extra) => {
    checks += 1;
    assert.ok(cond, label + (extra !== undefined ? '  [' + extra + ']' : ''));
  };

  // 2. 粘贴账号数组：一次预检，载荷是贴进来的行。
  calls.length = 0;
  byId('importGroup').value = '';
  byId('importPasteInput').value = JSON.stringify([
    {uid: 'p1', accessToken: 'x.y.z'},
    {uid: 'p2', accessToken: 'x.y.z'},
  ]);
  await api.parseImportPaste();
  check('粘贴后只发一次预检', calls.length === 1 && calls[0].url === '/accounts/import'
    && calls[0].body.dryRun === true, JSON.stringify(calls));
  check('预检载荷就是贴进来的两行',
    Array.isArray(calls[0].body.data) && calls[0].body.data.length === 2
    && calls[0].body.data[0].uid === 'p1' && calls[0].body.data[1].uid === 'p2',
    JSON.stringify(calls[0].body.data));
  check('待导入行来自粘贴内容', api.rows() && api.rows().length === 2);
  check('粘贴导入不伪装成文件条数', api.fileCount() === 0, api.fileCount());
  check('预览按来源而非文件名标注',
    byId('importBody').innerHTML.includes('粘贴') &&
    !byId('importBody').innerHTML.includes('文件：'),
    byId('importBody').innerHTML);
  check('预览后确认按钮可用',
    byId('btnImportCommit').disabled === false &&
    byId('btnImportCommit').textContent.includes('1'),
    byId('btnImportCommit').textContent);

  // 3. 导出文档与单账号对象同样能吃。
  calls.length = 0;
  byId('importPasteInput').value = JSON.stringify({accounts: [{uid: 'p3', accessToken: 'x.y.z'}]});
  await api.parseImportPaste();
  check('导出文档格式可用于粘贴',
    api.rows() && api.rows().length === 1 && api.rows()[0].uid === 'p3',
    JSON.stringify(api.rows()));

  calls.length = 0;
  byId('importPasteInput').value = JSON.stringify({uid: 'p4', accessToken: 'x.y.z'});
  await api.parseImportPaste();
  check('单个账号对象可用于粘贴',
    api.rows() && api.rows().length === 1 && api.rows()[0].uid === 'p4',
    JSON.stringify(api.rows()));

  // 4. 坏 JSON：不发请求、清空待导入行、按钮禁用、错误写在预览区。
  calls.length = 0;
  byId('importPasteInput').value = '{"accounts": [';
  await api.parseImportPaste();
  check('坏 JSON 不发请求', calls.length === 0, JSON.stringify(calls));
  check('坏 JSON 清空待导入行', api.rows() === null, api.rows());
  check('坏 JSON 禁用确认按钮', byId('btnImportCommit').disabled === true);
  check('坏 JSON 说明是 JSON 问题',
    byId('importBody').innerHTML.includes('不是合法 JSON'),
    byId('importBody').innerHTML);
  check('坏 JSON 后预览区仍留在粘贴面板',
    byId('importPastePane').style.display !== 'none' || api.mode() === 'paste');

  // 5. 空文本：同样不发请求。
  calls.length = 0;
  byId('importPasteInput').value = '   \n  ';
  await api.parseImportPaste();
  check('空粘贴不发请求', calls.length === 0, JSON.stringify(calls));
  check('空粘贴清空待导入行', api.rows() === null);
  check('空粘贴说明要先贴内容',
    byId('importBody').innerHTML.includes('空'), byId('importBody').innerHTML);

  // 6. 空账号数组也被拦下。
  calls.length = 0;
  byId('importPasteInput').value = '[]';
  await api.parseImportPaste();
  check('空数组不发请求', calls.length === 0, JSON.stringify(calls));
  check('空数组清空待导入行', api.rows() === null);

  // 7. 切换入口作废上一份预览：先让文件入口留下一份成功预览。
  calls.length = 0;
  dryRunReply = {result: {added: ['f1'], updated: [], skipped: [], invalid: []}};
  await api.readImportFiles([{name: 'file.json',
    text: () => Promise.resolve(JSON.stringify([{uid: 'f1', accessToken: 'x.y.z'}]))}]);
  check('文件入口预览可用', api.rows() && api.rows().length === 1
    && byId('btnImportCommit').disabled === false);

  api.setImportMode('paste');
  check('切到粘贴面板后文件面板隐藏', byId('importFilePane').style.display === 'none',
    byId('importFilePane').style.display);
  check('切到粘贴面板后粘贴面板可见', byId('importPastePane').style.display !== 'none',
    byId('importPastePane').style.display);
  check('切换入口作废待导入行', api.rows() === null, api.rows());
  check('切换入口禁用确认按钮', byId('btnImportCommit').disabled === true);
  check('切换入口后预览区提示重新解析',
    byId('importBody').innerHTML.includes('尚未'), byId('importBody').innerHTML);
  check('当前入口按钮高亮跟着走',
    byId('importModePaste').classList.contains('active')
    && !byId('importModeFile').classList.contains('active'),
    byId('importModePaste').className + ' / ' + byId('importModeFile').className);
  check('aria-selected 跟着入口走',
    byId('importModePaste').getAttribute('aria-selected') === 'true'
    && byId('importModeFile').getAttribute('aria-selected') === 'false');

  api.setImportMode('file');
  check('切回文件入口', api.mode() === 'file'
    && byId('importFilePane').style.display !== 'none'
    && byId('importPastePane').style.display === 'none');

  // 8. 开窗默认文件入口，并抹掉上一次贴进来的内容。
  byId('importPasteInput').value = JSON.stringify([{uid: 'stale', accessToken: 'x.y.z'}]);
  api.openImport();
  check('开窗默认回到文件入口', api.mode() === 'file', api.mode());
  check('开窗清空粘贴框', byId('importPasteInput').value === '', byId('importPasteInput').value);
  check('开窗清空待导入行', api.rows() === null
    && byId('btnImportCommit').disabled === true);

  byId('importPasteInput').value = JSON.stringify([{uid: 'stale', accessToken: 'x.y.z'}]);
  api.closeImport();
  check('关窗清空粘贴框', byId('importPasteInput').value === '');

  // 9. 真导入仍然读待导入行与分组，和入口无关。
  check('确认导入的载荷来自待导入行',
    /const rows = importRows\.slice\(\);/.test(script)
    && /data: rows, overwrite: overwrite, group: group/.test(script));

  console.log('account import paste assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.error(e && e.stack || e);
  process.exit(1);
});
