/* 自定义下拉：原生 <select> 被自绘控件接管，但语义必须原样保留。
 *
 * 回归背景：原生 select 的弹出层由浏览器绘制，CSS 够不着，深色主题下是
 * 直角白框 + 系统蓝高亮。这里把每个 select 包进 .cs-wrap，用触发器 + 自绘
 * 面板替换视觉。风险不在"好不好看"，而在**增强过程本身会碰坏原生语义**：
 *   - 把 select 移出原父节点、或 display:none，会让 value / 表单提交失效；
 *   - 忘了同步 selectedIndex，面板高亮会和真实值脱节；
 *   - 动态插入的 select（Key 编辑器按行生成）没被接管，会退回原生样式。
 *
 * 这里钉住四类契约：
 *   1. 结构：CSS 定义齐全、JS 关键分支存在、原生 select 不被删除；
 *   2. 渐进增强：CSS 默认不隐藏原生控件，只有 JS 加上 .cs-native 后才隐藏；
 *   3. 交互：键盘（↑↓ / Enter / Esc）与 aria 角色齐备；
 *   4. 动态接管：MutationObserver 覆盖后插入的 select。
 *
 * Run with Node: node tests/_test_custom_select.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const html = dashboardHtml();
const script = dashboardScript();

let passed = 0;
function ok(name, fn) {
  fn();
  passed += 1;
  console.log('  ok ' + name);
}

/* ── 1. CSS 契约 ─────────────────────────────────────────────── */

ok('CSS 定义了触发器 / 面板 / 选项 / 箭头', () => {
  for (const cls of ['.cs-wrap', '.cs-trigger', '.cs-panel', '.cs-opt', '.cs-arrow', '.cs-value']) {
    assert.ok(html.includes(cls + '{') || html.includes(cls + ' '), 'missing CSS rule ' + cls);
  }
});

ok('CSS 用主题变量而不是硬编码颜色', () => {
  const block = html.slice(html.indexOf('.cs-wrap{'), html.indexOf('[data-theme="dark"] select'));
  assert.ok(block.length > 200, 'custom-select CSS block not found');
  for (const v of ['var(--panel2)', 'var(--line)', 'var(--fg)', 'var(--accent)']) {
    assert.ok(block.includes(v), 'CSS should use theme variable ' + v);
  }
  // 面板底色必须是主题色，不能写死白色
  assert.ok(!/\.cs-panel\{[^}]*background:\s*#fff/i.test(block), 'panel must not hardcode #fff');
});

ok('原生控件只在 .cs-native 存在时才被视觉隐藏', () => {
  const rule = html.match(/\.cs-native\{([^}]*)\}/);
  assert.ok(rule, '.cs-native rule missing');
  assert.ok(/opacity:\s*0/.test(rule[1]), 'native select must be opacity:0');
  // 不能用 display:none / visibility:hidden，否则表单与无障碍失效
  assert.ok(!/display:\s*none/.test(rule[1]), '.cs-native must not use display:none');
  assert.ok(!/visibility:\s*hidden/.test(rule[1]), '.cs-native must not use visibility:hidden');
});

ok('默认（无 JS）不隐藏任何 select', () => {
  // 只有 .cs-native 类隐藏；裸 select 没有任何 opacity/display 归零的规则
  const bare = html.match(/(^|\})\s*select\s*\{([^}]*)\}/);
  if (bare) {
    assert.ok(!/opacity:\s*0/.test(bare[2]), 'bare select must stay visible without JS');
    assert.ok(!/display:\s*none/.test(bare[2]), 'bare select must stay visible without JS');
  }
});

ok('面板支持向上展开与滚动上限', () => {
  assert.ok(html.includes('.cs-wrap.cs-drop-up'), 'missing drop-up rule');
  const panel = html.match(/\.cs-panel\{([^}]*)\}/);
  assert.ok(panel && /max-height:/.test(panel[1]), 'panel needs max-height');
  assert.ok(panel && /overflow:auto/.test(panel[1]), 'panel needs overflow:auto');
});

ok('选中态与禁用态有独立样式', () => {
  assert.ok(html.includes('.cs-opt.cs-selected'), 'missing selected style');
  assert.ok(html.includes('.cs-opt.cs-disabled'), 'missing disabled style');
});

/* ── 2. JS 结构契约 ─────────────────────────────────────────── */

ok('增强逻辑只包一次，重复注入不会双层包裹', () => {
  assert.ok(script.includes('window.__csEnhanced'), 'missing idempotence guard');
  assert.ok(script.includes("sel.dataset.csDone === '1'"), 'missing per-element guard');
});

ok('原生 select 被移动而非删除', () => {
  const fn = script.slice(script.indexOf('function enhance(sel)'));
  assert.ok(fn.includes('wrap.appendChild(sel)'), 'native select must be moved into the wrapper');
  assert.ok(!/sel\.remove\(\)|removeChild\(sel\)/.test(fn), 'native select must never be removed');
});

ok('选中值变化会派发 change 事件', () => {
  assert.ok(script.includes("new Event('change', { bubbles: true })"),
    'picking an option must dispatch a bubbling change event');
});

ok('触发器与面板同步 selectedIndex', () => {
  const fn = script.slice(script.indexOf('function syncLabel'));
  assert.ok(fn.includes('sel.selectedIndex'), 'syncLabel must read selectedIndex');
  assert.ok(fn.includes('cs-selected'), 'syncLabel must mark the selected row');
});

ok('面板按 options 逐项重建', () => {
  const fn = script.slice(script.indexOf('function buildPanel'));
  assert.ok(fn.includes('sel.options.length'), 'buildPanel must iterate sel.options');
  assert.ok(fn.includes('disabled'), 'buildPanel must carry the disabled flag');
});

/* ── 3. 交互与无障碍 ───────────────────────────────────────── */

ok('键盘：上下键 / Enter / Esc 都有处理', () => {
  for (const key of ['ArrowDown', 'ArrowUp', 'Enter', 'Escape', 'Tab']) {
    assert.ok(script.includes("'" + key + "'"), 'missing keyboard handling for ' + key);
  }
});

ok('aria 角色：listbox / option / aria-selected / aria-expanded', () => {
  for (const attr of ['aria-haspopup', 'aria-expanded', 'aria-selected', 'role']) {
    assert.ok(script.includes(attr), 'missing ' + attr);
  }
  assert.ok(script.includes("'listbox'"), 'panel must declare role=listbox');
  assert.ok(script.includes("'option'"), 'rows must declare role=option');
});

ok('点击外部关闭，Esc 全局关闭', () => {
  assert.ok(script.includes("document.addEventListener('click'"), 'missing outside-click close');
  assert.ok(script.includes("if (e.key === 'Escape' && openWrap)"), 'missing global Esc close');
});

ok('同时只允许一个面板打开', () => {
  assert.ok(script.includes('function closeAll'), 'missing closeAll');
  assert.ok(script.includes('closeAll(wrap)'), 'opening must close the previous panel');
});

ok('滚动只重定位、不关闭面板', () => {
  // 曾经在这里写成 closeWrap，导致 scrollIntoView 之后刚打开就消失
  const marker = "window.addEventListener('scroll', function(){";
  const idx = script.lastIndexOf(marker);
  assert.ok(idx > 0, 'missing scroll handler');
  const body = script.slice(idx, idx + 200);
  assert.ok(body.includes('positionPanel'), 'scroll should reposition');
  assert.ok(!body.includes('closeWrap'), 'scroll must not close the panel');
});

/* ── 4. 动态接管 ───────────────────────────────────────────── */

ok('MutationObserver 接管后插入的 select', () => {
  const idx = script.lastIndexOf('function boot(){');
  assert.ok(idx > 0, 'missing boot()');
  const body = script.slice(idx, idx + 900);
  assert.ok(body.includes('MutationObserver'), 'boot must observe later DOM insertions');
  assert.ok(body.includes("n.tagName === 'SELECT'"), 'must enhance directly inserted selects');
});

ok('options 变化时重建面板', () => {
  const idx = script.indexOf('/* 外部数据改了 options');
  assert.ok(idx > 0, 'missing options MutationObserver');
  const body = script.slice(idx, idx + 320);
  assert.ok(body.includes('buildPanel'), 'options change must rebuild the panel');
});

ok('程序化改 value 也能同步显示', () => {
  assert.ok(script.includes('HTMLSelectElement.prototype'), 'missing value setter hook');
});

ok('全宽判定只认显式 width:100%，不信 computed display', () => {
  // 回归背景：flex 子项与隐藏页签里的 select，computed display 都会被浏览器
  // 规范化成 block，曾被当成「该占满一行」的证据，把工具栏里并排的下拉全部
  // 顶成竖排（账号页筛选条、任务页、设置页、历史弹窗、积分包排序都中招）。
  // 真正想全宽的控件都有显式 style.width:100%，就只认它，别再猜布局。
  const idx = script.indexOf('function enhance(sel)');
  assert.ok(idx > 0, 'missing enhance()');
  const body = script.slice(idx, script.indexOf('function buildPanel', idx));
  assert.ok(!/cs\.display\s*===\s*'block'/.test(body),
    'enhance must not read computed display (flex children normalize to block)');
  assert.ok(body.includes("sel.style.width === '100%'"),
    'enhance must check the explicit inline width');
  // 原生控件的 max-width 上限要搬进 wrapper，否则增强会吞掉宽度约束
  assert.ok(body.includes('wrap.style.maxWidth'),
    'enhance must carry the native max-width onto the wrapper');
});

/* ── 5. 长列表搜索 ───────────────────────────────────────────
 * 账号下拉一眼几十条，逐项翻找比打字慢。搜索框只做「隐藏不匹配的行」，
 * data-idx 始终指向真实 options 下标——这样选中逻辑不必区分有没有过滤，
 * 也不会因为过滤把 value 换成可见行的序号。
 */

ok('搜索的 CSS 与隐藏规则齐备', () => {
  for (const cls of ['.cs-search{', '.cs-search-input{', '.cs-opt.cs-hidden{']) {
    assert.ok(html.includes(cls), 'missing CSS rule ' + cls);
  }
  // 面板顶部吸顶，滚动选项时搜索框不跟着走
  assert.ok(/\.cs-search\{[^}]*position:sticky/.test(html), 'search box must be sticky');
});

ok('账号下拉支持显式搜索开关', () => {
  assert.ok(/data-cs-search/.test(script), 'the custom select must expose a search switch');
  assert.ok(/flag === 'on'/.test(script), 'data-cs-search="on" must force search');
});

ok('搜索开关：阈值 + 显式 on/off', () => {
  const fn = script.slice(script.indexOf('function wantsSearch'),
                          script.indexOf('function visibleRows'));
  assert.ok(fn.includes('data-cs-search'), 'wantsSearch must read the opt-in attribute');
  assert.ok(/flag === 'off'/.test(fn), 'data-cs-search="off" must disable search');
  assert.ok(/flag === 'on'/.test(fn), 'data-cs-search="on" must force search');
  assert.ok(fn.includes('SEARCH_MIN'), 'plain selects fall back to an option-count threshold');
});

ok('面板重建时先放搜索框，行上带可搜索的标签', () => {
  const fn = script.slice(script.indexOf('function buildPanel'),
                          script.indexOf('function positionPanel'));
  assert.ok(fn.includes('wantsSearch(sel)'), 'buildPanel must ask whether search is wanted');
  assert.ok(fn.includes('buildSearch('), 'buildPanel must prepend the search box');
  assert.ok(fn.includes("setAttribute('data-label'"), 'rows need data-label for filtering');
});

ok('过滤按标签做子串匹配，并给出空结果提示', () => {
  const fn = script.slice(script.indexOf('function applyFilter'),
                          script.indexOf('function buildSearch'));
  assert.ok(fn.includes('cs-hidden'), 'applyFilter must toggle cs-hidden');
  assert.ok(fn.includes('toLowerCase'), 'filter must be case-insensitive');
  assert.ok(fn.includes('没有匹配项'), 'a filtered-to-nothing panel needs an empty hint');
});

ok('键盘只在可见行里移动，Enter 按 data-idx 选中', () => {
  assert.ok(script.includes('function visibleRows'), 'missing visibleRows()');
  const fn = script.slice(script.indexOf('function buildSearch'),
                          script.indexOf('function buildPanel'));
  assert.ok(fn.includes('visibleRows('), 'search keyboard nav must skip hidden rows');
  assert.ok(fn.includes("getAttribute('data-idx')"), 'Enter must pick by data-idx');
  const trigger = script.slice(script.indexOf("trg.addEventListener('keydown'"),
                               script.indexOf("wrap.addEventListener('click'"));
  assert.ok(trigger.includes('visibleRows('), 'trigger keyboard nav must skip hidden rows');
  assert.ok(!/pick\(sel, wrap, j\)/.test(trigger),
    'trigger Enter must not pass the visible-row index as the option index');
});

ok('每次打开重置查询并聚焦搜索框', () => {
  const fn = script.slice(script.indexOf('function openWrap_'),
                          script.indexOf('function pick('));
  assert.ok(fn.includes('cs-search-input'), 'openWrap_ must find the search input');
  assert.ok(fn.includes("input.value = ''"), 'openWrap_ must clear the previous query');
  assert.ok(fn.includes('input.focus()'), 'openWrap_ should focus the search box');
  assert.ok(fn.includes("querySelectorAll('.cs-opt')"),
    'openWrap_ must scroll the selected ROW, not children[selectedIndex] (search box shifted it)');
});

/* 行为：直接跑过滤函数，确认隐藏 / 空态 / 大小写都按契约来。 */
ok('过滤行为：命中、全不中、清空', () => {
  const dom = require('./_dom_stub.js');
  dom.installDom();
  const sliceFrom = script.indexOf('var SEARCH_MIN');
  const sliceTo = script.indexOf('function buildSearch');
  assert.ok(sliceFrom > 0 && sliceTo > sliceFrom, 'cannot slice the search helpers');
  const api = new Function('document',
    script.slice(sliceFrom, sliceTo)
    + '\nreturn {wantsSearch: wantsSearch, visibleRows: visibleRows, applyFilter: applyFilter};'
  )(document);

  const labels = ['全部国内账号 (批量)', '130****1913 (009960)', 'Codex CLI'];
  const rows = labels.map((label, i) => {
    const row = dom.makeElement('div');
    row.className = 'cs-opt';
    row.setAttribute('data-label', label);
    row.setAttribute('data-idx', String(i));
    return row;
  });
  // 桩的 querySelector 是查表：真实 DOM 会找到刚 append 的空态节点，这里在
  // 节点建出来之后把表补上，行为与浏览器一致。
  const found = {'.cs-filter-empty': null};
  const panel = dom.makeElement('div', {
    querySelector: found,
    querySelectorAll: {'.cs-opt': rows},
  });
  const wrap = dom.makeElement('span', {querySelector: {'.cs-panel': panel}});

  api.applyFilter(wrap, '1913');
  assert.ok(!rows[1].classList.contains('cs-hidden'), 'hit row stays visible');
  assert.ok(rows[0].classList.contains('cs-hidden'), 'miss row is hidden');
  assert.deepStrictEqual(api.visibleRows(panel), [rows[1]], 'visibleRows follows the filter');

  api.applyFilter(wrap, '  codex  ');
  assert.strictEqual(api.visibleRows(panel).length, 1, 'query is trimmed and case-insensitive');
  assert.ok(rows[2].classList.contains('cs-hidden') === false, 'Codex matches CODE/ codex');

  api.applyFilter(wrap, 'zzzz');
  assert.strictEqual(api.visibleRows(panel).length, 0, 'no match leaves no visible rows');
  const empty = panel.children[panel.children.length - 1];
  assert.ok(empty && empty.className.includes('cs-filter-empty'), 'empty hint is appended');
  assert.ok(empty.textContent.includes('没有匹配项'), 'empty hint explains the state');
  assert.ok(empty.style.display === '', 'empty hint is shown');
  found['.cs-filter-empty'] = empty;

  api.applyFilter(wrap, '');
  assert.strictEqual(api.visibleRows(panel).length, 3, 'clearing the query restores every row');
  assert.ok(empty.style.display === 'none', 'empty hint hides again');

  // 阈值与显式开关
  const mk = (flag, n) => ({
    getAttribute: (k) => (k === 'data-cs-search' ? flag : null),
    options: {length: n},
  });
  assert.strictEqual(api.wantsSearch(mk('on', 2)), true, 'explicit on wins over a short list');
  assert.strictEqual(api.wantsSearch(mk('off', 40)), false, 'explicit off wins over a long list');
  assert.strictEqual(api.wantsSearch(mk(null, 3)), false, 'short list gets no search box');
  assert.strictEqual(api.wantsSearch(mk(null, 12)), true, 'long list gets a search box');
});

console.log('\n  ' + passed + ' assertions passed (custom select)');
