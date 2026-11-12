/* 账号分組檢視：把平表切成可折疊的分組小節。
 *
 * 回归背景：账号本来就带 group（Team A / 免费池 / 备用…），但面板只有一条
 * 「按分组筛选」的下拉，而那条筛选条挂在 console 布局的样式表里——布局一关，
 * 分组下拉连同整条筛选条一起消失，默认界面里没有任何「按分组看」的入口。
 *
 * 这里钉住四件事：
 *   1. 筛选条在默认布局下可见且有基线样式（不依赖 console 样式表）；
 *   2. 「按分组查看」开关存在、状态写进 localStorage、切换时接管排序开关；
 *   3. 分组渲染：组标题带计数与动作，未分组收在最后一节，折叠只隐藏本组行；
 *   4. 分组动作走服务端的 group 作用域，未分组不发请求。
 *
 * Run with Node: node tests/_test_account_group_view.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const html = dashboardHtml();
const script = dashboardScript();

let passed = 0;
function ok(name, fn) {
  fn();
  passed += 1;
  console.log('  ok ' + name);
}

/* ── 1. 静态契约 ─────────────────────────────────────────────── */

ok('筛选条在默认布局下可见，且不依赖 console 样式表', () => {
  const rule = html.match(/html:not\(\[data-console-layout="on"\]\):not\(\[data-ui="next"\]\) \.console-filterbar[^{]*\{([^}]*)\}/);
  assert.ok(rule, 'missing the console-layout hiding rule');
  assert.ok(/:not\(\.acct-filterbar\)/.test(rule[0]),
    '账号筛选条必须被排除在隐藏规则之外');
  const baseline = html.match(/:is\(\.acct-filterbar,\s*\.recent-filterbar\)\{([^}]*)\}/);
  assert.ok(baseline, 'missing the shared filterbar baseline styles');
  assert.ok(/display:flex/.test(baseline[1]), 'baseline must lay the bar out as flex');
  assert.ok(/var\(--panel2\)/.test(baseline[1]), 'baseline must use theme variables');
});

ok('账号页筛选条同时挂 console-filterbar 与 acct-filterbar', () => {
  assert.ok(/<div class="console-filterbar acct-filterbar" aria-label="账号列表筛选">/.test(html),
    'accounts filter bar must carry the acct-filterbar class');
  assert.ok(/id="accountGroupFilter"/.test(html), 'group filter select missing');
});

ok('工具栏有分組檢視开关', () => {
  assert.ok(/<button class="sec" id="btnAcctGroupView" onclick="toggleAccountGroupView\(this\)" aria-pressed="false"/.test(html),
    'missing the group-view toggle button');
});

ok('分組檢視接管排序：整表登进 SORT_DENY_TABLES', () => {
  const fn = script.slice(script.indexOf('function toggleAccountGroupView'),
                          script.indexOf('function toggleAccountGroupCollapse'));
  assert.ok(/SORT_DENY_TABLES\.add\('accounts'\)/.test(fn), 'entering group view must deny sorting');
  assert.ok(/SORT_DENY_TABLES\.delete\('accounts'\)/.test(fn), 'leaving group view must restore sorting');
});

ok('renderAccounts 在分組模式下走分组渲染、不排序、不分页', () => {
  const body = script.slice(script.indexOf('function renderAccounts()'),
                            script.indexOf('function autoFetchMissingCredits'));
  assert.ok(body.includes('accountRowsGrouped(list)'), 'grouped body builder is not wired in');
  assert.ok(/if\(!ACCT_GROUP_MODE\) applyTableSort\('accounts'\)/.test(body),
    'group view must skip the cross-table sort');
  assert.ok(/ACCT_TOTAL_PAGES = ACCT_GROUP_MODE \? 1 :/.test(body),
    'group view must not paginate');
  assert.ok(body.includes('syncAccountGroupButton()'), 'toggle label must follow the mode');
});

ok('渲染指纹包含分組模式与折叠状态', () => {
  const fp = script.slice(script.indexOf('function accountsRenderFp'),
                          script.indexOf('function changeAcctPage'));
  assert.ok(fp.includes('ACCT_GROUP_MODE'), 'fingerprint must react to the view mode');
  assert.ok(fp.includes('ACCT_GROUP_COLLAPSED'), 'fingerprint must react to collapsed groups');
});

/* ── 2. 行为：把分组函数单独拿出来跑 ─────────────────────────── */

const start = script.indexOf('function acctGroupKey');
const end = script.indexOf('// 第一个参数严格等于 true');
assert.ok(start > 0 && end > start, 'cannot slice the group-view helpers');

dom.installDom();
const collapsed = new Set();
const posts = [];
const toasts = [];
const filters = {query: '', status: '', group: '', errorCode: ''};
const deny = new Set(['recent']);
let renders = 0;

const api = new Function(
  'esc', 'accountRow', 'ACCT_GROUP_COLLAPSED', 'ACCT_GROUP_MODE', 'document',
  'toast', 'confirm', 'postJSON', 'loadAccounts', 'ACCOUNT_FILTERS',
  'renderAccounts', 'SORT_DENY_TABLES', 'ACCT_PAGE', 'localStorage',
  script.slice(start, end) + `
  return {acctGroupKey, accountGroupHeaderRow, accountRowsGrouped,
          focusAccountGroup, toggleAccountGroupCollapse,
          setGroupAccountsEnabled, toggleAccountGroupView,
          mode: function(){ return ACCT_GROUP_MODE; }};`
)(
  s => String(s == null ? '' : s),
  a => '[row:' + a.uid + (a.group ? '/' + a.group : '') + ']',
  collapsed, false, document,
  t => toasts.push(t), () => true,
  (url, body) => { posts.push({url: url, body: body}); return Promise.resolve({}); },
  () => Promise.resolve(),
  filters, () => { renders += 1; }, deny, 1,
  {getItem: () => null, setItem: () => {}},
);

const acct = (uid, group, enabled) => ({uid: uid, group: group, enabled: enabled !== false});

ok('分组键做 trim，空分组归到未分组', () => {
  assert.strictEqual(api.acctGroupKey({group: '  Team A  '}), 'Team A');
  assert.strictEqual(api.acctGroupKey({}), '');
  assert.strictEqual(api.acctGroupKey(null), '');
});

ok('分组渲染：组标题在前、未分组垫底、行跟在标题后面', () => {
  const out = api.accountRowsGrouped([
    acct('u1', 'Team B'), acct('u2', 'Team A'), acct('u3', 'Team A', false), acct('u4', ''),
  ]);
  const keys = ['Team A', 'Team B', ''];
  const order = keys.map(k => out.indexOf('data-group="' + k + '"'));
  assert.ok(order.every(i => i >= 0), 'each section header must exist: ' + out);
  assert.ok(order[0] < order[1] && order[1] < order[2], 'headers must be ordered A, B, ungrouped');
  assert.ok(out.indexOf('[row:u2/Team A]') > order[0], 'Team A rows belong to its own section');
  assert.ok(out.indexOf('[row:u2/Team A]') < order[1], 'Team A rows must not leak past the next header');
  assert.ok(out.indexOf('[row:u4]') > order[2], 'ungrouped rows sit in the last section');
  assert.ok(/2 个/.test(out), 'header carries the section count');
  assert.ok(out.indexOf('未分组') > order[2], 'ungrouped section is labelled');
});

ok('组标题带计数与动作；未分组不给批量启停', () => {
  const named = api.accountGroupHeaderRow('Team A', [acct('a', 'Team A'), acct('b', 'Team A', false)]);
  assert.ok(/colspan="11"/.test(named), 'header row must span the whole table');
  assert.ok(/2 个/.test(named) && /启用 1 个/.test(named), 'counts missing: ' + named);
  for (const label of ['本组启用', '本组停用', '只看此组']) {
    assert.ok(named.includes(label), 'missing group action ' + label);
  }
  const none = api.accountGroupHeaderRow('', [acct('c', '')]);
  assert.ok(!/本组启用/.test(none), 'ungrouped section must not offer server-side bulk enable');
  assert.ok(/未归组/.test(none), 'ungrouped section needs an explanation');
});

ok('折叠只隐藏本组行，标题仍在', () => {
  collapsed.clear();
  const open = api.accountRowsGrouped([acct('u1', 'Team A'), acct('u2', 'Team B')]);
  assert.ok(open.includes('[row:u1/Team A]'));
  collapsed.add('Team A');
  const folded = api.accountRowsGrouped([acct('u1', 'Team A'), acct('u2', 'Team B')]);
  assert.ok(!folded.includes('[row:u1/Team A]'), 'collapsed section must hide its rows');
  assert.ok(/data-group="Team A"/.test(folded), 'collapsed section keeps its header');
  assert.ok(/aria-expanded="false"/.test(folded), 'collapsed header must announce the state');
  assert.ok(folded.includes('[row:u2/Team B]'), 'other sections stay visible');
  collapsed.clear();
});

ok('只看此组 = 切筛选条上的分组下拉', () => {
  const sel = document.getElementById('accountGroupFilter');
  api.focusAccountGroup('Team B');
  assert.strictEqual(filters.group, 'Team B');
  assert.strictEqual(sel.value, 'Team B');
  assert.ok(renders > 0, 'changing the group filter must repaint the table');
});

ok('本组启停走服务端的 group 作用域', () => {
  posts.length = 0;
  // 请求在第一个 await 之前就发出去了，后面的回读不参与断言。
  api.setGroupAccountsEnabled('Team A', true, null);
  assert.strictEqual(posts.length, 1, 'exactly one request for one click');
  assert.strictEqual(posts[0].url, '/accounts/set-all');
  assert.deepStrictEqual(posts[0].body, {enabled: true, group: 'Team A'});
});

ok('未分组不做批量启停（空分组不能退化成全池操作）', () => {
  posts.length = 0;
  toasts.length = 0;
  api.setGroupAccountsEnabled('', true, null);
  assert.strictEqual(posts.length, 0, 'ungrouped bulk action must not call the API');
  assert.ok(toasts.some(t => /未分组/.test(t)), 'user needs to be told why: ' + toasts);
});

ok('开关切换会接管 / 归还排序开关', () => {
  assert.strictEqual(api.mode(), false);
  api.toggleAccountGroupView();
  assert.strictEqual(api.mode(), true);
  assert.ok(deny.has('accounts'), 'group view must deny the table sort');
  api.toggleAccountGroupView();
  assert.strictEqual(api.mode(), false);
  assert.ok(!deny.has('accounts'), 'flat view must restore the table sort');
});

console.log('\n  ' + passed + ' assertions passed (account group view)');
