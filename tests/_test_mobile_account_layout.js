/* 手机端账号区的三处布局回归（都只在 ≤640px 生效，桌面不受影响）。
 *
 * 1) 批量操作那一组有 6 个按钮，一行摆不下时每个按钮被压成竖排文字
 *    （「一 / 键 / 分 / 配 / 代 / 理」）。窄屏改成两列网格 → 3 行 2 列，
 *    按钮文字保持横排可读。
 * 2) 账号卡片第一行塞了昵称、备注、分组、任务开关、版本、状态、账户类型，
 *    长昵称时整行顶出卡片宽度（超屏）。现在第一行是「勾选 + 昵称 + 版本 /
 *    状态 / 账户类型」，第二行是「号池 + 任务开关 + 备注 + 分组」。
 * 3) 操作区两层 flex 容器各自换行时，出站身份那组会整块另起一行、中间空出
 *    一段。两行容器拆平（display:contents），按钮按 DOM 顺序自然流动。
 *
 * 桌面布局：任务开关 / 备注 / 分组同排一行（.acct-chip-row），号池标签仍在
 * 昵称前面；两行操作容器在桌面上仍是两行。
 *
 * Run with Node: node tests/_test_mobile_account_layout.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml} = require('./_dashboard_source.js');
const html = dashboardHtml();

/* ---- 1. 批量操作组：两列网格，文字不竖排 ---------------------------- */

assert.ok(/class="tb-group tb-bulk"/.test(html),
  '批量操作那一组缺少 .tb-bulk 标记，窄屏网格规则会落到所有 tb-group 上');
// 选择器必须带 .tb-advanced：那条 .tb-group{display:flex} 更具体，少了前缀
// 会把网格压回 flex，每行只剩一个按钮。
assert.ok(/#accountsBody \.toolbar \.tb-advanced \.tb-bulk\{display:grid;\s*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/
    .test(html),
  '窄屏下 .tb-bulk 应改成两列网格（6 个按钮排成 3 行 2 列）');
assert.ok(/#accountsBody \.toolbar \.tb-advanced \.tb-bulk button\.sec\{[^}]*white-space:nowrap/.test(html),
  '网格里的按钮必须 white-space:nowrap，否则名称又被压成竖排');

// 6 个按钮都在这一组里：分配代理 / 切换所有出站身份 / 启用 / 停用 /
// 当前分组启用 / 当前分组停用。
const bulkAt = html.indexOf('class="tb-group tb-bulk"');
assert.ok(bulkAt > 0, '找不到 .tb-bulk 组');
const bulkBlock = html.slice(bulkAt, html.indexOf('</div>', bulkAt));
for (const label of ['分配代理', '切换所有出站身份', '启用', '停用',
                     '当前分组启用', '当前分组停用']) {
  assert.ok(bulkBlock.includes(label), '批量操作组里缺少「' + label + '」');
}

/* ---- 1b. 号池组：三个控件同一行 ------------------------------------ */

// 号池筛选 + 两个「移入」按钮原来排两行（下拉独占一行），账号列表被多推
// 下一行。三个都是窄控件，窄屏改成三列网格放一行；自定义下拉的真正上屏元素
// 是 .cs-trigger（原生 select 被视觉隐藏），它的默认高度比按钮矮 9px，必须
// 显式对齐 36px，否则同一行里三个控件的基线是错开的。
assert.ok(/class="tb-group tb-pool"/.test(html), '号池组缺少 .tb-pool 标记');
assert.ok(/#accountsBody \.toolbar \.tb-pool\{display:grid;\s*grid-template-columns:minmax\(0,1\.1fr\) minmax\(0,1fr\) minmax\(0,1fr\)/
    .test(html),
  '窄屏下号池组应是三列网格（下拉 + 两个按钮同一行）');
assert.ok(/#accountsBody \.toolbar \.tb-pool \.cs-wrap\{display:block;width:100%;min-width:0\}/.test(html),
  '号池下拉的自定义包装层要拉满格宽，否则仍按内容宽排在行首');
assert.ok(/#accountsBody \.toolbar \.tb-pool \.cs-trigger\{height:36px/.test(html),
  '号池下拉触发器要钉 36px，与两个「移入」按钮同高');
assert.ok(/#accountsBody \.toolbar \.tb-pool button\.sec\{[^}]*white-space:nowrap/.test(html),
  '号池按钮必须 white-space:nowrap，窄屏下不能被压成竖排文字');

/* ---- 2. 账号卡两行：昵称行 / chip 行 -------------------------------- */

assert.ok(html.includes('<div class="acct-chip-row">'),
  '账号卡缺少 .acct-chip-row 容器，任务开关与备注分组无法同排');
assert.ok(/<span class="acct-mobile-badges">/.test(html),
  '账号卡缺少移动端徽章组（版本 / 状态 / 类型）');
// 桌面：chip 行是一整行 flex，任务开关 / 备注 / 分组同排。
assert.ok(/\.acct-chip-row\{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:2px\}/
    .test(html),
  '桌面 chip 行应是一行可换行的 flex（任务开关 / 备注 / 分组同排）');
// 窄屏：徽章组与昵称同一行，chip 行整行独占。
assert.ok(/table\.account-cards td:nth-child\(3\) \.acct-name-row\{flex-wrap:wrap\}/.test(html),
  '窄屏账号名那行要允许换行，徽章组才能落到下面');
// 只允许换行还不够：这一格的直接子项默认 min-width:auto（= 内容宽），长昵称
// 仍会把整格撑到卡片之外（390px 视口实测单元格 271px、名字行 434px，右侧被
// 裁掉且没有省略号）。必须钉住 min-width:0 + max-width:100%。
assert.ok(/table\.account-cards td:nth-child\(3\) > \*\{min-width:0;max-width:100%\}/.test(html),
  '账号名那格的直接子项要钉 min-width:0 + max-width:100%，否则长昵称会撑出卡片');

// 号池标签两处各渲染一份，按断点只显示其中一份：桌面在昵称前，窄屏在 chip 行。
assert.ok(/function poolChip\(cls, a, uidAttr\)\{/.test(html),
  '号池标签应由 poolChip() 统一生成，避免两处文案漂移');
assert.ok(/poolChip\('acct-pool-inline', a, uidAttr\)/.test(html),
  '桌面那份号池标签缺失');
assert.ok(/poolChip\('acct-pool-mobile', a, uidAttr\)/.test(html),
  '窄屏那份号池标签缺失');
assert.ok(/\.acct-pool-mobile\{display:none\}/.test(html),
  '桌面必须隐藏窄屏那份号池标签，否则同一行出现两个');
assert.ok(/\.acct-pool-inline\{display:none\}/.test(html),
  '窄屏必须隐藏桌面那份号池标签，否则卡片里出现两个号池标签');
assert.ok(/\.acct-pool-mobile\{display:inline-block\}/.test(html),
  '窄屏要显式放回窄屏那份号池标签');

// 任务开关：桌面在 chip 行，窄屏挪到「今日活动」行；两处共用 taskChip。
assert.ok(/const taskChip = \(a\.apiKey \? ''/.test(html),
  '任务开关应由 taskChip 统一生成，桌面与窄屏共用一份');
assert.ok(/class="acct-task-inline"/.test(html) && /class="acct-task-mobile"/.test(html),
  '任务开关需要桌面 / 窄屏两个挂载点');
assert.ok(/\.acct-task-mobile\{display:none\}/.test(html),
  '桌面必须隐藏窄屏那份任务开关');
assert.ok(/\.acct-task-inline\{display:none\}/.test(html),
  '窄屏必须隐藏桌面那份任务开关');
assert.ok(/class="acct-task-mobile"/.test(html)
      && html.indexOf('class="acct-task-mobile"') > html.indexOf('activityStatusChip(a)'),
  '窄屏任务开关要挂在「今日活动」那一格');

// 昵称右侧只留版本 + 账户类型，可用挪到 chip 行。
assert.ok(/\.acct-state-mobile\{display:none\}/.test(html)
      && /\.acct-state-mobile\{display:inline-flex\}/.test(html),
  '状态徽章需要「桌面隐藏 / 窄屏显示」两条规则');
assert.ok(/class="acct-state-mobile"/.test(html),
  '状态徽章缺少窄屏挂载点');

// 到期：桌面在「版本」格第三行，窄屏回到次要信息行。
assert.ok(/class="acct-exp acct-exp-inline"/.test(html),
  '桌面「版本」格缺少到期行');
assert.ok(/\.acct-exp-meta\{display:none\}/.test(html)
      && /\.acct-exp-meta\{display:inline\}/.test(html),
  '到期在窄屏要有独立副本（桌面隐藏 / 窄屏显示）');

// 勾选格与账号格同一行：两格都 span 整行、都在 grid 第 1 行，账号格靠
// 30px 左内边距给勾选框让位（勾选框只占第一行最左边一小块）。
assert.ok(/table\.account-cards td:nth-child\(1\)\{order:0;grid-column:1\/-1;grid-row:1;/.test(html),
  '窄屏勾选格应与账号格同处第 1 行、贴左占位');
assert.ok(/table\.account-cards td:nth-child\(3\)\{order:0;grid-column:1\/-1;grid-row:1;/.test(html),
  '窄屏账号格应与勾选格同一行，并用左内边距让出勾选框');
assert.ok(/table\.account-cards td:nth-child\(3\)::before\{content:none\}/.test(html),
  '窄屏卡片里不该再出现「账号」这个列标签');

// 总 token 与缓存并排两列。
assert.ok(/table\.account-cards td:nth-child\(10\),\s*table\.account-cards td:nth-child\(11\)\{order:6;grid-column:span 3;/
    .test(html),
  '窄屏下总 token 与缓存应各占半行并排');

// 顺序契约：昵称行先渲染，chip 行在它之后。
const nameRowAt = html.indexOf('class="acct-name-row"');
const chipRowAt = html.indexOf('class="acct-chip-row"');
assert.ok(nameRowAt > 0 && chipRowAt > nameRowAt,
  'chip 行必须排在账号名那一行之后');

/* ---- 3. 操作区拆平：没有中间空块 ------------------------------------ */

assert.ok(/table\.account-cards \.acct-actions-row\{display:contents\}/.test(html),
  '窄屏下操作区的两行容器应拆平，避免出站身份整块另起一行留出空段');

// 按钮顺序：导出紧跟同步昵称，删除排在出站身份之后。
const syncAt = html.indexOf('>同步昵称<');
const exportAt = html.indexOf('>导出<', syncAt);
const idGroupAt = html.indexOf('id-btn-group', syncAt);
const deleteAt = html.indexOf('>删除<', idGroupAt);
assert.ok(syncAt > 0 && exportAt > syncAt,
  '「导出」应排在「同步昵称」之后（同一行按钮流里紧挨着）');
assert.ok(idGroupAt > exportAt,
  '出站身份组应排在「导出」之后');
assert.ok(deleteAt > idGroupAt,
  '「删除」应排在出站身份组之后');

/* ---- 4. 桌面布局没被改动 -------------------------------------------- */

// 桌面（≥641px）两行操作容器保持原样：.acct-actions-row 仍是 flex。
assert.ok(/table\.account-cards \.acct-actions-row\{flex-wrap:wrap;gap:3px\}/.test(html),
  '桌面操作区的两行紧凑布局被改掉了');
assert.ok(/table\.account-cards \.acct-actions-col\{min-width:max-content;max-width:none\}/.test(html),
  '桌面操作列宽度规则被改掉了');
// 移动端徽章组在桌面上仍隐藏（版本/状态有自己的两列）。
assert.ok(/\.acct-mobile-badges\{display:none\}/.test(html),
  '桌面必须隐藏移动端徽章组，否则版本/状态会重复显示');

console.log('_test_mobile_account_layout.js: all assertions passed');
