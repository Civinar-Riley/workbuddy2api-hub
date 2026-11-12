/* 积分获取历史区块（数据看板 → /accounts/credits/grants）。
 *
 * 上游给每个账号一份积分包清单（免费套餐、每日活跃奖励、活动包…），每个包带面额
 * 与发放时间；这个区块把全部账号的包摊平成一张表、新的在前。套件钉住：
 *
 *   1. 区块、表体与整行表头真的在页面里，且外层滚动容器的表头是 sticky（CSS 与
 *      markup 是两处一起改的，缺一处滚起来就看不出哪列是哪列）；
 *   2. renderCreditGrants() 对四种状态各画出正确的行与徽章：生效中 / 可用 /
 *      已用完 / 已过期，不过期的包显示「不过期」；
 *   3. 摘要行给出笔数、合计、剩余与快照时刻；空载荷给空状态而不是白板；
 *   4. loadCreditGrants() 打的就是 /accounts/credits/grants。
 *
 * Run with Node: node tests/_test_credit_grants.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

// One shared fake DOM for every dashboard suite: tests/_dom_stub.js.
const dom = require('./_dom_stub.js');

let checks = 0;
const check = (label, cond, extra) => {
  checks += 1;
  assert.ok(cond, label + (extra ? '  [' + extra + ']' : ''));
};

// ---- 1. 页面里真的有这个区块与整行表头 -------------------------------------
const html = dashboardHtml();
check('区块容器在页面里', html.includes('id="creditGrantsSection"'));
check('表体在页面里', html.includes('id="creditGrantsTbody"'));
check('整行表头与列序在页面里',
      html.includes('<thead><tr><th>时间</th><th>账号</th><th>区域</th><th>名称</th><th>来源</th><th>积分</th><th>剩余</th><th>到期</th><th>状态</th></tr></thead>'));
check('滚动容器的表头是 sticky（与扣减历史同款）', html.includes('#creditGrantsTable thead th'));
check('说明写清了数据是快照', html.includes('数据是最近一次「刷新积分」的快照'));

// ---- 2. 渲染每种状态 -------------------------------------------------------
const requested = [];
dom.installDom({
  fetch: (url) => {
    requested.push(url);
    const payload = {ok: true, rows: [], summary: {}, fetched_iso: null};
    return Promise.resolve({
      status: 200, ok: true,
      json: () => Promise.resolve(payload),
      text: () => Promise.resolve(JSON.stringify(payload)),
    });
  },
});

const script = dashboardScript();
const api = new Function(script + `
  return { renderCreditGrants, loadCreditGrants };`)();

const tbody = document.getElementById('creditGrantsTbody');
const summaryEl = document.getElementById('creditGrantsSummary');

const row = (over) => Object.assign({
  uid: 'uid-1', nickname: 'meyadi', realm: 'intl', name: 'Bonus Pack',
  product: 'Tencent Cloud CodeBuddy', grant_reason: '',
  size: 30, remain: 12.54, used: 17.46, unit: 'credit',
  create_at: 1791607292, create_iso: '2026-10-10 00:42:45',
  expire_at: 1794200000, expire_iso: '2026-11-10 00:42:44',
  no_expiry: false, days_left: 30.5, status: 'active',
}, over);

api.renderCreditGrants({
  ok: true,
  generated_iso: '2026-10-10 13:55:00',
  fetched_iso: '2026-10-10 12:41:32',
  summary: {count: 4, size: 190, remain: 120.54, used: 69.46, accounts: 2},
  rows: [
    row({}),
    row({uid: 'uid-5', nickname: 'hades', realm: 'cn', size: 100, remain: 100,
         name: 'CodeBuddy个人版国内运营裂变包', status: 'available',
         grant_reason: 'Buddy 加油站签到',
         action: {task: 'checkin', ts: '2026-10-10T00:07:34+08:00', ok: true, delta_seconds: 1}}),
    row({uid: 'uid-2', nickname: 'hades', realm: 'cn', name: 'Free Plan Subscription',
         size: 100, remain: 100, used: 0, status: 'available', no_expiry: true,
         expire_iso: '2034-12-18 21:27:52', days_left: null}),
    row({uid: 'uid-3', nickname: 'old', name: 'Bonus Pack', size: 30, remain: 0,
         used: 30, status: 'used_up', create_iso: '2026-09-19 02:53:28',
         expire_iso: '2026-10-19 02:53:26'}),
    row({uid: 'uid-4', nickname: 'gone', name: 'Campaign Pack', size: 30, remain: 8,
         used: 22, status: 'expired', create_iso: '2026-09-20 03:00:00',
         expire_iso: '2026-10-01 03:00:00'}),
  ],
});

const out = tbody.innerHTML;
check('正常行画出昵称与包名', out.includes('meyadi') && out.includes('Bonus Pack'), out);
check('面额与剩余都画出来', out.includes('>30<') && out.includes('12.54'), out);
check('生效中的包带「生效中」徽章', out.includes('生效中'), out);
check('可用但未在扣减的包带「可用」徽章', out.includes('可用'), out);
check('用完的包带「已用完」徽章', out.includes('已用完'), out);
check('过期的包带「已过期」徽章', out.includes('已过期'), out);
check('不过期的包显示「不过期」', out.includes('不过期'), out);
check('上游给了发放原因就原样显示在来源列', out.includes('Buddy 加油站签到'), out);
check('本机动作关联进来源列的悬停提示',
      out.includes('本机动作：每日签到 10-10 00:07 成功'), out);
check('上游发放原因也在悬停提示里', out.includes('上游发放原因：Buddy 加油站签到'), out);
check('国际版 Bonus Pack 30 按规则推断为每日活跃奖励',
      out.includes('每日活跃奖励') && out.includes('按包名与面额推断'), out);
check('既没有原因也推断不出时来源显示 —', /<td[^>]*>—<\/td>/.test(out), out);
check('区域列按中英文界面词画出', out.includes('国内版') && out.includes('国际版'), out);
check('摘要给出笔数、合计、剩余与快照时刻',
      summaryEl.textContent.includes('共 4 笔') && summaryEl.textContent.includes('合计 190')
      && summaryEl.textContent.includes('剩余 120.54')
      && summaryEl.textContent.includes('数据截至 10-10 12:41'), summaryEl.textContent);

// grant_reason 有值时挂到名称列的 title 上
api.renderCreditGrants({ok: true, summary: {count: 1, size: 30, remain: 30},
  rows: [row({grant_reason: '官方活动发放', status: 'available'})]});
check('发放原因显示在名称列的悬停提示里',
      tbody.innerHTML.includes('title="官方活动发放"'), tbody.innerHTML);

// 空载荷：不是白板，给一句能看懂的空状态
api.renderCreditGrants({ok: true, rows: [], summary: {}, fetched_iso: null});
check('空载荷给空状态', tbody.innerHTML.includes('暂无积分获取记录'), tbody.innerHTML);
check('空载荷时摘要说明怎么办',
      summaryEl.textContent.includes('刷新积分'), summaryEl.textContent);

// ---- 3. loadCreditGrants() 打的就是这个接口 --------------------------------
requested.length = 0;
api.loadCreditGrants().then(() => {
  check('loadCreditGrants 请求 /accounts/credits/grants',
        requested.some(u => String(u).includes('/accounts/credits/grants')), requested.join(','));
  console.log('credit grants panel assertions passed (' + checks + ' checks)');
}).catch(err => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
