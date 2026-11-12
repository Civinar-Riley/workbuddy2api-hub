/* 控制台骨架与筛选的契约：新导航结构、筛选语义与既有行为的边界。
 *
 * 这一轮把界面从「顶部页签」改成「左侧分组菜单」，动的是导航的呈现层，
 * 所以这里钉住三件事：
 *
 *   1. 骨架：左侧菜单里那七个按钮仍然是原来那七个（id、顺序、onclick 都不变），
 *      书签与首屏选页的那份清单也仍然认它们；新加的「模型管理 / 请求记录」
 *      是内容入口，不是新页面，所以不进入页面清单，也改不了 ?tab=。
 *   2. 筛选：账号、模型、最近请求三处筛选只改显示，不改批量操作的原始范围；
 *      模型被筛掉时勾选状态不能丢——全选/反选必须仍按整份目录算。
 *   3. 概览：健康度与 Token 分布只用已经加载过的数据，不发新请求，也不把
 *      当前页的分布说成历史趋势。
 *
 * Run with Node: node tests/_test_console_shell.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const html = dashboardHtml();
const script = dashboardScript();

let checks = 0;
function check(ok, label, extra) {
  assert.ok(ok, label + (extra === undefined ? '' : '  -> ' + extra));
  checks += 1;
}

/* ---- 1. 结构：导航被搬进左侧菜单，但七个按钮本身没变 ------------------- */

check(/class="console-sidebar" id="consoleSidebar"/.test(html),
  '缺少左侧控制台菜单容器 #consoleSidebar');
check(/class="console-shell"/.test(html), '缺少控制台外层 .console-shell');
check(/class="console-contextbar"/.test(html), '缺少内容区上下文条 .console-contextbar');

// 七个主菜单按钮：id 不变、指向的页面不变、顺序不变。
const NAV = [
  ['btnNavGateway', 'gateway', 'switchMainTab(\'gateway\')'],
  ['btnNavAccounts', 'accounts', 'switchMainTab(\'accounts\')'],
  ['btnNavTasks', 'tasks', 'switchMainTab(\'tasks\')'],
  ['btnNavTasks', 'tasks', null],
];
const navButtons = [...html.matchAll(/<button class="main-nav-btn[^"]*" id="(btnNav\w+)"[^>]*>/g)].map(m => m[1]);
const EXPECTED = ['btnNavGateway', 'btnNavAccounts', 'btnNavTasks', 'btnNavMedia', 'btnNavAnalytics',
                  'btnNavLogs', 'btnNavAgents', 'btnNavSettings'];
check(JSON.stringify(navButtons) === JSON.stringify(EXPECTED),
  '左侧菜单里的主导航按钮应当就是原来那七个且顺序不变，实得 ' + navButtons.join(','));

// 每个按钮仍指向原来的页面。
['gateway', 'accounts', 'tasks', 'media', 'analytics', 'agents', 'settings', 'logs'].forEach(key => {
  const id = 'btnNav' + key.charAt(0).toUpperCase() + key.slice(1);
  check(new RegExp('id="' + id + '"[^>]*onclick="switchMainTab\\(\'' + key + '\'\\)"').test(html),
    id + ' 应当仍然切到 ' + key + ' 页');
});

// 新增的两个入口是内容入口，不是新页面：不进页面清单，也不带 btnNav 前缀
// （页面清单按 id 前缀推导，带上前缀就会被当成可隐藏/可切换的页面）。
check(!/class="main-nav-btn[^\"]*" id="btnNav(?:Models|Requests)"/.test(html),
  '模型管理 / 请求记录不应被当成主页面');
check(/id="btnNavModels"[^>]*onclick="openModelCatalog\(\)"/.test(html),
  '模型管理应当是跳到模型清单的内容入口');
check(/id="btnNavRequests"[^>]*onclick="openRecentRequests\(\)"/.test(html),
  '请求记录应当是跳到最近请求的内容入口');
check(html.includes('id="gatewayModelsSection"') && html.includes('id="recentRequestsSection"'),
  '两个内容入口需要稳定锚点');

// 内容入口是独立「页面」而不只是滚动定位：切到模型管理 / 请求记录时，网关页里
// 其它区块要整块收起，左侧也只保留一个选中态。
check(/function applyConsoleView\(view\)/.test(script), '缺少内容入口的视图切换');
const view = script.slice(script.indexOf('function setConsoleNavActive('),
                          script.indexOf('function openModelCatalog('));
check(/console-view-hidden/.test(view), '切换视图要把非当前区块收起');
// 选中内容入口时要先清掉父级主导航的选中态。断言的是「清」这个动作，不是某个
// 具体选择器：原先这里匹配 `.console-sidebar .main-nav-btn.active`，而那条选择器
// 会连带命中侧栏里的设置/日志等一级按钮，网关视图每重算一次就把它们的高亮摘掉
// （详见 _test_nav_aria_current.js）。现在收窄成三个视图入口的 id，语义没变。
check(/(?:main-nav-btn|#btnNav(?:Gateway|Models|Requests))[\s\S]{0,200}classList\.remove\('active'\)/.test(view),
  '选中内容入口时必须先清掉父级主导航的选中态');
check(/classList\.toggle\('active', on\)/.test(view), '内容入口之间应当互斥，只保留一个选中');
check(/openModelCatalog\(\)\{[\s\S]{0,200}applyConsoleView\('models'\)/.test(script),
  '模型管理要切到自己的视图');
check(/openRecentRequests\(\)\{[\s\S]{0,200}applyConsoleView\('requests'\)/.test(script),
  '请求记录要切到自己的视图');
check(/if\(!window\.__NEXT_UI__ && tab === 'gateway'\) applyConsoleView\('overview'\)/.test(script),
  '从左侧菜单回到网关页时应回到概览视图');
check(/\.console-view-hidden\{display:none!important\}/.test(html),
  '缺少视图收起的样式');

// 页面清单没变：书签与首屏选页仍按原来的七页走。
const headTabs = html.match(/var TABS = \[([^\]]*)\];/);
const mainTabs = html.match(/const MAIN_TABS = \[([^\]]*)\];/);
const names = s => s.split(',').map(x => x.trim().replace(/['"]/g, '')).filter(Boolean);
check(headTabs && mainTabs && JSON.stringify(names(headTabs[1])) === JSON.stringify(names(mainTabs[1])),
  'head 的 TABS 与主脚本的 MAIN_TABS 必须一致');
check(names(mainTabs[1]).length === 9 && names(mainTabs[1]).includes('overview') && names(mainTabs[1]).includes('media') && !names(mainTabs[1]).includes('models'),
  '主页面清单包含新导航总览和媒体页且不含内容入口');

// 侧栏折叠与手机抽屉的入口都在。
check(/function toggleConsoleCollapse\(\)/.test(script), '缺少侧栏折叠');
check(/function setConsoleNavOpen\(open\)/.test(script), '缺少手机端抽屉开关');
check(/onclick="toggleConsoleNav\(\)"/.test(html), '手机端缺少功能菜单按钮');

// ---- 2. 筛选：只改显示，不改批量操作的原始范围 -----------------------

check(/const ACCOUNT_FILTERS = \{query: '', status: '', group: '', errorCode: ''\}/.test(script),
  '账号筛选状态应当是独立的一份');
check(/function accountMatchesFilters\(a\)/.test(script), '缺少账号筛选判定');
check(/function clearAccountFilters\(\)/.test(script), '缺少账号筛选清除');
check(/function accountErrorCode\(a\)/.test(script), '缺少账号错误码提取');
check(/function filteredAccountsForBulk\(\)/.test(script), '缺少筛选结果批量操作范围');
check(/function setAccountErrorFilter\(code\)/.test(script),
  '选择错误码后应自动勾选命中账号');
check(/function deleteAccounts\(btn\)/.test(script), '缺少批量删除入口');
// 启用 / 停用 / 删除 / 导出 共用一套作用域（有勾选按勾选，没勾选按筛选结果），
// 不再有「xx 已选」这种独立按钮。
for(const [id, fn, label] of [
  ['btnBulkEnable', 'setAll', '启用'],
  ['btnBulkDisable', 'setAll', '停用'],
  ['btnBulkDelete', 'deleteAccounts', '删除'],
  ['btnExport', 'exportAccounts', '导出'],
]){
  check(new RegExp('id="' + id + '"[^>]*onclick="' + fn + '\\(').test(html),
    '账号工具栏缺少「' + label + '」按钮或接线错误');
}
for(const gone of ['btnExportSelected', 'btnEnableSelected',
                   'btnDisableSelected', 'btnDeleteSelected',
                   'btnSelectFiltered']){
  check(!html.includes('id="' + gone + '"'),
    '旧的「已选」独立按钮应已移除：' + gone);
}
check(/function acctScopeUids\(\)[\s\S]{0,400}filteredAccountsForBulk/.test(script),
  '批量作用域必须回落到当前筛选结果');
check(/postJSON\('\/accounts\/set-all', \{enabled: enable, uids: scope\.uids\}\)/.test(script),
  '启用/停用必须提交显式 uids，不能退化成全量启停');
check(html.includes('11140 · 账号封禁') && !html.includes('11140 · 风控限制'),
  '11140 必须显示为账号封禁，而不是风控限流');

// 账号筛选作用在渲染的那一份列表上，而 accountsInView()（账号池、禁用汇总、
// 批量操作的输入）不能被筛选改掉。
const renderAccounts = script.slice(script.indexOf('function renderAccounts()'),
                                  script.indexOf('async function loadAccounts()'));
check(/let list = accountsInView\(\)/.test(renderAccounts),
  'renderAccounts 仍要从 accountsInView() 取全量');
check(/list = list\.filter\(accountMatchesFilters\)/.test(renderAccounts),
  '账号筛选应当在渲染前作用于这份列表');
check(!/function accountsInView\(\)[\s\S]{0,400}?ACCOUNT_FILTERS/.test(script),
  'accountsInView 不该被展示筛选污染：批量操作仍按原账号范围执行');

// 空结果与「没有账号」要分得开。
check(/没有符合当前条件的账号/.test(script), '筛选无结果时应有专门提示');
check(/当前.*还没有账号/.test(script), '真正的空账号池应保留原有引导');
check(script.includes('const bodyCode = text.match(/"code"\\s*:\\s*"?(\\d+)"?/i)'),
  '错误码筛选必须优先读上游正文里的 "code" 字段');
check(/ACCOUNT_FILTERS\.errorCode && accountErrorCode\(a\)/.test(script),
  '错误码筛选必须参与账号匹配');
// 批量删除与启停 / 导出共用作用域：有勾选按勾选，没有勾选按当前筛选结果。
check(/ACCOUNT_SELECTED\.clear\(\)/.test(script) &&
      /function deleteAccounts\(btn\)/.test(script) &&
      /const scope = acctScopeUids\(\);/.test(script),
  '批量删除必须走 acctScopeUids() 的作用域（勾选优先，回落筛选结果）');

// 模型筛选：只改渲染，批量勾选按整份目录。
check(/let MODEL_CATALOG_FILTER = ''/.test(script), '缺少模型筛选状态');
const allPick = script.slice(script.indexOf('function allModelPickIds()'),
                            script.indexOf('function updateAvailModelCount()'));
check(/MODELS_DATA/.test(allPick) && !/querySelectorAll\('#modelsTable input\.model-pick'\)/.test(allPick),
  '全选/反选必须按整份目录取 id，不能按当前可见行取（筛选会把行藏掉）');

// 请求列表筛选：关键词 + 结果 / 账号 / 模型 / Key 四个服务端过滤。
check(/const RECENT_FILTERS = \{query: '', outcome: '', account: '', model: '', key: ''\}/.test(script),
  '缺少请求列表筛选状态');
check(/function renderRecentView\(\)/.test(script), '缺少请求列表筛选渲染');
check(/function clearRecentFilters\(\)/.test(script), '缺少请求列表筛选清除');

// 筛选条在三个区块里各有一个。
 ['accountSearchFilter', 'accountStatusFilter', 'accountErrorFilter',
 'acctSelAll',
 'recentSearchFilter', 'recentOutcomeFilter', 'recentAccountFilter',
 'recentModelSelect', 'recentKeySelect',
 'modelCatalogFilter'].forEach(id => {
  check(html.includes('id="' + id + '"'), '缺少筛选控件 #' + id);
});
// 新导航没有 data-console-layout=on；旧布局的隐藏规则不能连带藏掉筛选栏，
// 否则控件不可见但 ACCOUNT_FILTERS 等状态仍会把列表筛空（issue #850）。
check(/html:not\(\[data-console-layout="on"\]\):not\(\[data-ui="next"\]\) \.console-filterbar/.test(html),
  '新导航下筛选栏必须保持可见');
check(!/html:not\(\[data-console-layout="on"\]\) \.console-filterbar\s*\{display:none!important\}/.test(html),
  '旧布局的筛选栏隐藏规则不应误伤新导航');

// 批量作用范围不再跟号池隔离开关走：勾选本身就是明确的选择，没有勾选时按
// 当前筛选结果，两者都与「隔离是否开启」无关。
check(/function acctScopeUids\(\)/.test(script),
  '缺少统一的批量作用范围函数');
check(/function acctSelUids\(\)\{\s*return selectedAccountsForBulk\(\)/.test(script),
  '勾选集合应读 ACCOUNT_SELECTED，不能读 DOM 的 :checked');
check(/acctScopeUids\(\)[\s\S]{0,200}filteredAccountsForBulk\(\)/.test(script),
  '没有勾选时作用范围应是当前筛选结果');
check(!/if\(!POOL_SEPARATION_EFFECTIVE\) return \[\];/.test(script),
  'acctSelUids 不应再受号池隔离开关限制');

// 号池筛选与「移入」按钮始终上屏：poolScope 是账号归属，隔离关着时只是不
// 限制派发，不代表这个字段不可见 / 不可改。筛选状态也不再跟开关走——否则
// 会出现「控件藏了、POOL_FILTER 还在过滤」的 #850 同类问题。
check(!/id="poolToolsGroup"[^>]*\bhidden\b/.test(html),
  '号池工具组不应默认隐藏');
check(!/POOL_SEPARATION_EFFECTIVE/.test(script),
  '号池筛选与批量作用范围不应再依赖隔离开关');
check(/<select id="poolFilterSelect"[^>]*onchange="poolFilter\(this\.value\)"/.test(html),
  '号池筛选应是下拉框，选择后立即生效');
check(/<option value="all">全部号池<\/option>\s*<option value="text">文本号池<\/option>\s*<option value="media">媒体号池<\/option>/.test(html),
  '号池下拉应提供全部 / 文本 / 媒体三项，且默认第一项是全部号池');
check(!/id="pfAll"/.test(html) && !/id="pfText"/.test(html) && !/id="pfMedia"/.test(html),
  '旧的三个号池按钮应已移除');
check(/>移入媒体池</.test(html) && />移入文本池</.test(html),
  '批量按钮应叫「移入媒体池」「移入文本池」');
check(!/勾选移入媒体池/.test(html) && !/勾选移回文本池/.test(html),
  '旧按钮文案应已移除');
check(!/id="batchPoolCount"/.test(html), '「未勾选」计数标签应已移除');

// 勾选列：上游 v2.1.0 用 ACCOUNT_SELECTED 作真源，行内复选框 + 「全选筛选结果」。
check(/class="acct-sel" data-uid="' \+ uidAttr/.test(script),
  '账号行缺少勾选列复选框');
check(/const ACCOUNT_SELECTED = new Set\(\)/.test(script),
  '勾选状态应有独立集合，整表重画后不丢');
check(/function toggleAccountSelection\(box\)/.test(script)
      && /function clearAccountSelection\(\)/.test(script),
  '缺少勾选 / 清空勾选的状态回写函数');
// 全选只在表头有一个复选框，不再有独立的「全选筛选结果」按钮。
check(/id="acctSelAll"[^>]*onchange="toggleAcctSelAll\(this\)"/.test(html),
  '表头缺少全选勾选框');
check(!/id="btnSelectFiltered"/.test(html),
  '「全选筛选结果」独立按钮应已移除（全选只在表头复选框）');

// ---- 3. 概览：用已有数据，不发新请求，也不冒充时间序列 ---------------

check(/function renderConsoleInsights\(rows, accts\)/.test(script), '缺少概览附加图渲染');
check(/function renderConsoleAccountHealth\(accts\)/.test(script), '缺少账号健康度渲染');
check(/function renderConsoleUsageChart\(rows\)/.test(script), '缺少 Token 分布图渲染');

// 健康度必须复用账号的统一过滤函数，不能再写一份区域过滤（两处会漂开）。
const health = script.slice(script.indexOf('function renderConsoleAccountHealth('),
                            script.indexOf('function renderConsoleUsageChart('));
check(/accountsInView\(\)/.test(health), '健康度应复用 accountsInView()');
check(!/\.filter\(a => !a\.realm \|\| a\.realm ===/.test(health),
  '健康度不该自己再写一份区域过滤表达式');

// 全页只允许存在一处区域过滤表达式（账号卡片与禁用汇总共用）。
const realmFilters = script.match(/\.filter\(a => !a\.realm \|\| a\.realm ===/g) || [];
check(realmFilters.length === 1,
  '区域过滤表达式应当只有一处，实得 ' + realmFilters.length + ' 处');

// 概览图不新增请求，也不把当前页说成趋势。
check(/当前页.*非时间序列|非时间序列/.test(script), '概览图应标明它不是时间序列');
const insight = script.slice(script.indexOf('function renderConsoleInsights('),
                            script.indexOf('/* ------------------------------- dashboard'));
check(!/getJSON\(|api\(|fetch\(/.test(insight),
  '概览图只能读已加载的数据，不该自己发请求');

console.log('console shell assertions passed (' + checks + ' checks)');
