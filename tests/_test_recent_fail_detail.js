/* 请求列表：失败看得进去，筛得出来。

   背景：usage.jsonl 里的错误行本来就带 status（HTTP 码）与 message（上游原话，
   截断 200 字），但面板只画了一个「失败」徽章，把这两个字段整条丢掉——排查时
   只能回去翻日志文件。

   这里钉住三件事：
     1. 表格不多开一列（失败原因放在点开的详情里，行上只留结果徽章）；
     2. 非完成的徽章是可点的按钮，点开 #recentFailModal，弹窗里给出完整信息；
     3. 结果 / 账号 / 模型 / Key 四个下拉是服务端过滤：改了条件之后取数 URL
        必须带上对应参数（1M 行日志不可能只筛当前页）。

   Run with Node.
*/
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();
const html = dashboardHtml();

const dom = require('./_dom_stub.js');
const REQUESTS = [];

const ROWS = [
  {iso: '2026-11-12T17:48:24', model: 'step-5-preview', stream: false,
   outcome: 'failed', status: 429, message: 'HTTP 429 rate limit exceeded for account 166****8472',
   elapsed_ms: 621, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
   account: 'uid-aaaa1111', key: 'k-1', key_name: 'ops-key'},
  {iso: '2026-11-12T17:48:25', model: 'hy3', stream: false, outcome: 'completed',
   elapsed_ms: 900, prompt_tokens: 4, completion_tokens: 2, total_tokens: 6,
   account: 'uid-bbbb2222', key: 'k-2', key_name: 'laptop'},
  {iso: '2026-11-12T17:48:26', model: 'deepseek-v4.1-flash', stream: true,
   outcome: 'client_aborted', elapsed_ms: 30, prompt_tokens: 0, completion_tokens: 0,
   total_tokens: 0, account: 'uid-aaaa1111', key: 'k-1', key_name: 'ops-key'},
];

const {window: domWindow} = dom.installDom({
  fetch: url => {
    REQUESTS.push(String(url));
    const payload = String(url).includes('/usage/recent')
      ? {rows: ROWS, total: ROWS.length, page: 1, total_pages: 1, scanned: 3,
         total_exact: true, filters: null, accounts_map: {}}
      : {rows: [], total: 0, accounts_map: {}, accounts: [], slots: [], data: [],
         results: [], byAccount: []};
    return Promise.resolve({
      status: 200, ok: true,
      json: () => Promise.resolve(payload),
      text: () => Promise.resolve(JSON.stringify(payload)),
    });
  },
});
domWindow.ACCOUNTS = [];
global.ACCOUNTS = domWindow.ACCOUNTS;

function headerLabels(markup) {
  const head = markup.slice(markup.indexOf('<thead>'), markup.indexOf('</thead>'));
  return [...head.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map(m => m[1].trim());
}

function bodyRows(markup) {
  const body = markup.slice(markup.indexOf('<tbody>'), markup.indexOf('</tbody>'));
  return [...body.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(row => {
    const cells = {};
    for (const cell of row[1].matchAll(/<td([^>]*)>([\s\S]*?)<\/td>/g)) {
      const label = (cell[1].match(/data-label="([^"]*)"/) || [])[1];
      if (label) cells[label] = cell[2];
    }
    return cells;
  });
}

const api = new Function(script + `
  window.updateUI = updateUI;
  window.toast = toast;
  // refresh() 自己会去重：撞上正在飞的一轮就排队补跑并立刻返回，所以测试不能
  // 靠 await refresh() 等取数——这里等「没有在飞、也没有排队」再断言。
  // 让路只用微任务：_dom_stub 的 setTimeout 是排队不自动触发的，用它等会一直挂住。
  async function waitRefreshIdle(){
    for(let i = 0; i < 5000 && (REFRESH_RUNNING || REFRESH_QUEUED); i++){
      await Promise.resolve();
    }
    return true;
  }
  return { refresh, waitRefreshIdle, openRecentFailDetail, closeRecentFailDetail,
           setRecentFilter, clearRecentFilters, RECENT_FILTERS };`)();

(async () => {
  window.VIEW_REALM = 'intl';
  await api.refresh();

  const rendered = dom.byId('recent').innerHTML;
  let checks = 0;
  const check = (label, cond, extra) => {
    checks += 1;
    assert.ok(cond, label + (extra ? '  [' + extra + ']' : ''));
  };

  const headers = headerLabels(rendered);
  const rows = bodyRows(rendered);
  check('少了那条「失败原因」列（详情只在点开的弹窗里）',
        !headers.includes('失败原因'), headers.join(' | '));
  check('结果列还在，紧跟其后就是耗时',
        headers.indexOf('结果') + 1 === headers.indexOf('耗时'), headers.join(' | '));

  const failed = rows.find(r => /失败/.test(r['结果'] || ''));
  const okRow = rows.find(r => /完成/.test(r['结果'] || ''));
  const aborted = rows.find(r => /客户端取消/.test(r['结果'] || ''));

  check('成功行只画一个「完成」徽章，不可点',
        okRow && /<span class="badge ok">完成<\/span>/.test(okRow['结果']) && !/onclick/.test(okRow['结果']),
        okRow && okRow['结果']);
  check('失败行是可点的按钮（不再是死标签）',
        failed && /<button/.test(failed['结果'])
        && /class="badge [^"]*badge-btn"/.test(failed['结果'])
        && /onclick="openRecentFailDetail\(\d+\)"/.test(failed['结果']),
        failed && failed['结果']);
  check('客户端取消同样可点，方便看它为什么被中断',
        aborted && /badge-btn/.test(aborted['结果']), aborted && aborted['结果']);

  const idx = Number((failed['结果'].match(/openRecentFailDetail\((\d+)\)/) || [])[1]);

  // 点开详情：弹窗要 show，内容要带上码与上游原话，元信息要带模型与账号。
  api.openRecentFailDetail(idx);
  const mask = dom.byId('recentFailModal');
  check('弹窗被打开（.modal-mask 加 show）', mask.classList.contains('show'), mask.className);
  const msg = dom.byId('recentFailMsg').textContent;
  const meta = dom.byId('recentFailMeta').innerHTML;
  check('弹窗里有 HTTP 状态码', /HTTP 429/.test(msg), msg);
  check('弹窗里有上游原话', /rate limit exceeded/.test(msg), msg);
  check('弹窗元信息带模型', /step-5-preview/.test(meta), meta.slice(0, 120));
  check('弹窗元信息带账号', /uid-aaaa/.test(meta), meta.slice(0, 200));
  check('弹窗元信息带 API Key 显示名', /ops-key/.test(meta), meta.slice(0, 200));

  api.closeRecentFailDetail();
  check('关闭后 .show 被摘掉', !mask.classList.contains('show'), mask.className);

  // 四个服务端过滤：改条件后取数 URL 必须带上参数。
  const acctSel = dom.byId('recentAccountFilter');
  const modelSel = dom.byId('recentModelSelect');
  const keySel = dom.byId('recentKeySelect');
  const optValues = sel => [...String(sel.innerHTML).matchAll(/<option value="([^"]*)"/g)].map(m => m[1]);
  check('账号下拉里出现了本次响应里的账号',
        optValues(acctSel).includes('uid-aaaa1111'), optValues(acctSel).join(','));
  check('模型下拉里出现了本次响应里的模型',
        optValues(modelSel).includes('step-5-preview'), optValues(modelSel).join(','));
  check('Key 下拉用显示名当标签、id 当值',
        optValues(keySel).includes('k-1') && /ops-key/.test(keySel.innerHTML), keySel.innerHTML.slice(0, 160));

  // 结果下拉的选项写在静态标记里（DOM stub 不解析 HTML），从源码读。
  const outcomeBlock = (html.match(/<select id="recentOutcomeFilter"[\s\S]*?<\/select>/) || [''])[0];
  const outcomeOpts = (outcomeBlock.match(/<option/g) || []).length;
  check('结果下拉只有 全部 / 成功 / 失败 三项',
        outcomeOpts === 3 && /全部结果/.test(outcomeBlock) && /成功/.test(outcomeBlock) && /失败/.test(outcomeBlock),
        outcomeBlock);

  REQUESTS.length = 0;
  api.setRecentFilter('account', 'uid-aaaa1111');
  await api.waitRefreshIdle();
  const recentUrls = REQUESTS.filter(u => u.includes('/usage/recent'));
  check('改账号过滤后重取数据带上 account 参数',
        recentUrls.some(u => u.includes('account=uid-aaaa1111')), recentUrls.join(' | '));
  check('过滤后回到第一页', recentUrls.every(u => /[?&]page=1(&|$)/.test(u)), recentUrls.join(' | '));

  REQUESTS.length = 0;
  api.setRecentFilter('outcome', 'failed');
  await api.waitRefreshIdle();
  check('结果过滤走服务端（outcome=failed）',
        REQUESTS.some(u => u.includes('outcome=failed') && u.includes('account=uid-aaaa1111')),
        REQUESTS.join(' | '));

  REQUESTS.length = 0;
  api.clearRecentFilters();
  await api.waitRefreshIdle();
  check('清除筛选后取数 URL 不再带过滤参数',
        REQUESTS.some(u => u.includes('/usage/recent')
          && !/account=|model=|key=|outcome=/.test(u)), REQUESTS.join(' | '));

  console.log('recent failure-detail / filter assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.error('assertions failed:', (e && e.stack) || e);
  process.exitCode = 1;
});
