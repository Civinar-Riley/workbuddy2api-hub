/* Key 级日 token 限量的面板接线。
 *
 * 后端已经能存能查（`daily_token_limit`，单位 M），这里钉住面板确实把它接全了：
 *
 *   1. 编辑 Key 的弹窗里有独立的「日 token 限量 (M)」输入框，id 与保存函数对得上；
 *   2. Key 行上有「日限 NM」徽标，0（不限）时不画；
 *   3. 保存时把输入框的值写回该行，再随 `api_keys` 一起提交；
 *   4. 新建 Key 的默认值是 0（不限），不是空字符串或 undefined；
 *   5. 从 `/settings` 读回的行里带着这个字段，否则一进页面就丢。
 *
 * Run with Node: node tests/_test_api_key_daily_limit_ui.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml} = require('./_dashboard_source.js');

const html = dashboardHtml();
let checks = 0;
function check(ok, msg){ assert.ok(ok, msg); checks += 1; }

// 1. 编辑弹窗里的输入框
check(html.includes("id=\"editKeyDaily_"), '缺少日限量输入框 #editKeyDaily_');
check(/日 ?token 限量 \(M\)/.test(html), '输入框上方应有「日 token 限量 (M)」标签');
check(/0 = 不限/.test(html), '标签上应说明 0 代表不限');
check(/editKeyDaily_' \+ index/.test(html), '保存时应按行号读取 #editKeyDaily_');
check(/id="editKeyConc_/.test(html), '同一块里应保留并发上限输入框');

// 2. 行徽标
check(/dailyBadge/.test(html), '缺少 dailyBadge 变量');
check(/row\.daily_token_limit && row\.daily_token_limit > 0/.test(html),
      '日限徽标应只在 > 0 时出现');
check(/日限 ' \+ row\.daily_token_limit \+ 'M/.test(html), '徽标文案应为「日限 NM」');
check(/\+\s+concBadge\s*\n\s*\+\s+dailyBadge/.test(html), '两个徽标都应渲染进行模板');

// 3. 保存路径
check(/row\.daily_token_limit = Math\.max\(0, parseInt/.test(html),
      '保存时应把输入框的值归一成非负整数写回该行');
check(/daily_token_limit: parseInt\(row\.daily_token_limit \|\| 0, 10\) \|\| 0,/.test(html),
      '提交 payload 里应带 daily_token_limit');

// 4. 新建 Key 默认 0
check(/daily_token_limit: 0,/.test(html), '新建 Key 的默认日限量应为 0');

// 5. 读回
check(/daily_token_limit: parseInt\(k\.daily_token_limit \|\| 0, 10\) \|\| 0,/.test(html),
      '从 /settings 读回的行里应带 daily_token_limit');

console.log('OK ' + checks + ' checks');
