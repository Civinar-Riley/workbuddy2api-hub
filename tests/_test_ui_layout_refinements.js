/* 本轮 UI 修正的结构契约：账号告警/积分、用量明细列配置与单一滚动上下文。 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const html = dashboardHtml();
const script = dashboardScript();

assert.ok(/\.cool-list\{display:flex;flex-direction:column/.test(html), '账号告警应纵向排列');
assert.ok(/table\.account-cards td\[data-label="账号"\] \.cool-pill\{display:block;width:100%/.test(html), '告警 pill 应在账号列内收敛');
assert.ok(/\.acct-actions-row\{flex-wrap:nowrap;max-width:none\}/.test(html), '桌面操作区应保持原来的两行紧凑布局');
// 表格宽度跟随容器（width:100%），只有窄到放不下才横向滚动；列宽按内容定，
// 不再写死一个大 min-width 让右侧被裁掉。
assert.ok(/table\.account-cards\{table-layout:fixed;width:100%;min-width:1096px\}/.test(html),
  '桌面账号表应跟随容器宽度，min-width 只作横向滚动的兜底');
// 勾选列始终渲染，所以「勾选 / 版本 / 账号」三列常驻吸附，偏移固定为 0 / 32 / 100。
assert.ok(/table\.account-cards th:nth-child\(1\),\s*table\.account-cards tbody tr:not\(\.cool-row\) td:nth-child\(1\)\{[^}]*position:sticky;left:0/.test(html),
  '勾选列应固定在左侧');
assert.ok(/table\.account-cards th:nth-child\(2\),\s*table\.account-cards tbody tr:not\(\.cool-row\) td:nth-child\(2\)\{[^}]*position:sticky;left:32px/.test(html),
  '版本列应固定在勾选列右侧');
assert.ok(/table\.account-cards th:nth-child\(3\),\s*table\.account-cards tbody tr:not\(\.cool-row\) td:nth-child\(3\)\{[^}]*position:sticky;left:100px/.test(html),
  '账号列应固定在版本列右侧');
assert.ok(/table\.account-cards tbody tr:not\(\.cool-row\):hover td:nth-child\(1\),\s*table\.account-cards tbody tr:not\(\.cool-row\):hover td:nth-child\(2\),\s*table\.account-cards tbody tr:not\(\.cool-row\):hover td:nth-child\(3\)\{background:var\(--panel2\)\}/.test(html),
  '左侧固定列悬停时应跟随行背景，不能露出白块');
assert.ok(/td\.acct-actions\{white-space:normal;min-width:252px;width:252px;position:sticky/.test(html), '操作列应固定在可视区域右侧');
assert.ok(/@media \(max-width:640px\)[\s\S]*?td\.acct-actions\{position:static/.test(html), '移动端应取消操作列 sticky');
assert.ok(/@media \(max-width:640px\)[\s\S]*?table\.account-cards td:nth-child\(1\),\s*table\.account-cards td:nth-child\(2\)\{position:static;left:auto/.test(html),
  '移动端卡片布局应取消左侧固定列');
// 勾选：最左侧独立复选框列 + 表头全选框（全选只在表头，没有独立按钮）。
// 表头与行都由 renderAccounts() 用字符串拼出，所以查 script 而不是 html。
assert.ok(/class="acct-check"/.test(script)
      && /id="acctSelAll"[^>]*onchange="toggleAcctSelAll\(this\)"/.test(script),
  '账号表头缺少全选勾选框');
assert.ok(/<td class="acct-check"[^>]*>/.test(script), '账号行缺少勾选列单元格');
assert.ok(!/id="btnSelectFiltered"/.test(html),
  '「全选筛选结果」独立按钮应已移除（全选只在表头复选框）');
assert.ok(/\.acct-name-row\{[^}]*flex-wrap:wrap/.test(html), '账号名行应允许徽章换行，不能把昵称挤成 0 宽');
assert.ok(/\.acct-name\{[^}]*flex:1 1 80px;[^}]*min-width:72px/.test(html), '昵称应保留最小可见宽度，不能被备注/分组徽章压没');

assert.ok(html.includes('class="credit-stack"'), '积分应使用分层容器');
assert.ok(html.includes('class="credit-main"'), '积分主值应单独一行');
assert.ok(html.includes('class="credit-meta"'), '到期/明细应位于积分下一层');
assert.ok(script.indexOf('credit-main') < script.indexOf('credit-meta'), '积分主值应先于明细层渲染');
assert.ok(script.includes('7天内到期'), '保留七天到期徽章');
assert.ok(script.includes('openCreditsDetail(&quot;'), '保留积分明细入口');

assert.ok(script.includes("const DIM_LAYOUT_KEY = 'wb-analytics-dim-layout-v1'"), '列配置应使用版本化本地存储 key');
assert.ok(script.includes("localStorage.getItem(DIM_LAYOUT_KEY + ':' + dim)"), '列配置应按维度读取');
assert.ok(script.includes('function visibleDimColumns(dim)'), '应按稳定字段生成可见列');
assert.ok(script.includes('data-dim-col'), '表头/列配置应使用稳定字段 id');
assert.ok(script.includes('dimColumnsMenu'), '应存在列设置菜单');
assert.ok(script.includes('hidden.delete(\'who\')'), '账号/首列不可隐藏');
assert.ok(!/section\.panel \.pbody\{overflow-y:auto;max-height:calc/.test(html), '不应再有 pbody 纵向嵌套滚动');
assert.ok(/#pageAnalytics \.recent-list\{display:flex;flex-direction:column;max-height:260px;overflow-y:auto[^}]*\}/.test(html), '最近请求应带 max-height + 内部纵向滚动');
assert.ok(/#pageAnalytics \.maingrid\{[^}]*align-items:stretch\}/.test(html), '主区两栏底部对齐');
assert.ok(/#modelsTable\{table-layout:fixed\}/.test(html), '网关模型清单应固定布局占满宽度');
assert.ok(/#modelsTable th,#modelsTable td\{white-space:nowrap;overflow:hidden;text-overflow:ellipsis\}/.test(html), '模型清单单元格不应换行');

console.log('_test_ui_layout_refinements.js: all assertions passed');
