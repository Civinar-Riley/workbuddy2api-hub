/* 账号卡的两个可见性回归：出站身份标签、账户名完整显示。
 *
 * 1) 「出站身份：」标签曾被当成冗余删掉（WB / VSC / CLI 三个缩写单独出现时
 *    看不出这组在切什么）。现在标签必须在桌面与手机都显示。
 * 2) 账户名列宽 180px + .acct-name 强制 nowrap + ellipsis，长昵称在桌面只剩
 *    1px 宽（被旁边两个 chip 挤掉），手机上长昵称则把卡片顶出屏。现在列宽
 *    放宽到 300px，昵称允许换行按字符折断，任何宽度下都能完整读出。
 *
 * Run with Node: node tests/_test_account_card_visibility.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml} = require('./_dashboard_source.js');

const html = dashboardHtml();

let checks = 0;
function check(label, ok, detail) {
  checks++;
  assert.ok(ok, label + (detail === undefined ? '' : '  -> ' + detail));
}

/* ---- 1. 出站身份标签 --------------------------------------------------- */

// 标签在 DOM 里，且没有任何规则把它藏掉（以前是两条 display:none）。
check(html.includes('<span class="id-btn-label">出站身份：</span>'),
  '账号卡操作区缺少「出站身份：」标签');
check(!/\.acct-actions \.id-btn-label\{display:none/.test(html),
  '还有规则把「出站身份：」标签藏掉');
check(/\.id-btn-label\{color:var\(--dim\);font-size:11px;white-space:nowrap\}/.test(html),
  '标签的基础样式被改掉了');

/* ---- 2. 账户名完整显示 ------------------------------------------------- */

// 桌面：列放宽 + 昵称允许换行。
check(/table\.account-cards\{table-layout:fixed;width:100%;min-width:1096px\}/.test(html),
  '桌面账号表要跟随容器宽度，min-width 只作兜底');
check(/@media \(min-width:641px\)\{\s*\.acct-name\{flex:0 1 auto;max-width:100%;white-space:normal;overflow:visible;\s*text-overflow:clip;word-break:break-word\}/.test(html),
  '桌面昵称要能换行完整显示（基础规则里的 nowrap 会把它裁成省略号）');
check(/\.acct-name\{flex:0 1 auto;max-width:100%;white-space:normal;overflow:visible;\s*text-overflow:clip;word-break:break-word\}/.test(html),
  '昵称要能换行完整显示，而不是 nowrap + ellipsis 裁掉');
check(/\.acct-name-row\{flex-wrap:wrap;row-gap:2px\}/.test(html),
  '昵称行要允许换行，否则长昵称无法折到第二行');

// 操作列要放得下恢复显示的标签，否则操作区横向溢出。
check(/table\.account-cards td\.acct-actions\{white-space:normal;min-width:252px;width:252px;position:sticky/.test(html),
  '操作列宽度要放得下「出站身份：」标签 + 三个按钮 + 删除');
check(/table\.account-cards th:last-child\{min-width:252px;width:252px\}/.test(html),
  '表头最后一列要与操作列同宽，否则表头与表体错位');
check(/table\.account-cards td\.acct-actions\{[^}]*padding-left:3px!important;padding-right:3px!important\}/.test(html),
  '操作列内边距要收到 3px，五个按钮才摆得下且不顶出格子');

// 手机：同一套换行规则，且不产生横向溢出。
check(/table\.account-cards td:nth-child\(3\) \.acct-name\{flex:0 1 auto;min-width:0;max-width:100%;\s*white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word\}/.test(html),
  '手机上昵称也要能换行完整显示');

/* ---- 3. 桌面布局其余部分没被改动 --------------------------------------- */

check(/table\.account-cards \.acct-actions-col\{min-width:max-content;max-width:none\}/.test(html),
  '桌面操作列的内容宽度规则被改掉了');
check(/table\.account-cards \.acct-actions-row\{flex-wrap:nowrap;max-width:none\}/.test(html),
  '桌面两行紧凑布局被改掉了');

process.stdout.write('account card visibility: ' + checks + ' checks passed\n');
