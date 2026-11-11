/* Exercise the account row renderer shipped in dashboard.html. Run with Node. */
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();
// One shared fake DOM for every dashboard suite: tests/_dom_stub.js.
const dom = require('./_dom_stub.js');
dom.installDom({
  fetch: () => Promise.resolve({ok: true, status: 200, json: () => Promise.resolve({})}),
});

const {accountRow, coolRow, limitRow, fmtCoolAt, disabledRows, renderDisabled,
       applyTableSort, SORT_STATE} = new Function(script + `
  return {accountRow, coolRow, limitRow, fmtCoolAt, disabledRows, renderDisabled,
          applyTableSort, SORT_STATE};`)();
const base = overrides => Object.assign({
  uid: 'uid-cn-0001', nickname: 'synthetic', realm: 'cn', enabled: true,
  source: 'oauth', expiresIn: '24 hours', lastError: '', inCooldown: false,
  cooldownFor: null, machineId: '', credits: null, product: 'cli',
}, overrides);
const until = Math.floor(Date.now() / 1000) + 600;
const d = new Date(until * 1000);
const pad = n => String(n).padStart(2, '0');
const fmtAt = epoch => {
  const dd = new Date(epoch * 1000);
  return dd.getFullYear() + '-' + pad(dd.getMonth() + 1) + '-' + pad(dd.getDate())
    + ' ' + pad(dd.getHours()) + ':' + pad(dd.getMinutes());
};
const localTime = fmtAt(until);

// 胶囊现在独占一整行（coolRow），不再挤在账号列里——账号列只有 180px，长模型名
// 必然被省略号切掉，整行才放得下完整文本。count 类断言改用整段渲染。
assert.equal(coolRow(base({})), '');
assert.equal(coolRow(base({modelCooldowns: []})), '');
assert.equal(fmtCoolAt(until), localTime);
assert.equal(fmtCoolAt('bad-value'), '');

const row = accountRow(base({modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(row.includes('glm-5.3 · 恢复时刻 ' + localTime));
// 可见文本要交代时间口径，并且把模型名与恢复时刻写全（不再靠 title 兜底）。
assert(row.includes('时间为本地时间'));
assert(row.includes('>可用</span>')); // The account still serves other models.
assert(!row.includes('冷却 600s'));
const throttled = accountRow(base({lastError: 'HTTP 429 (model throttled)',
  modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(!throttled.includes('HTTP 429 (model throttled)'));
const otherError = accountRow(base({lastError: 'HTTP 401',
  modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(otherError.includes('HTTP 401'));

const multi = accountRow(base({modelCooldowns: [
  {model: 'glm-5.2', expiresAt: until}, {model: 'glm-5.3', expiresAt: until + 60},
]}));
assert(multi.indexOf('glm-5.2') < multi.indexOf('glm-5.3'));
assert.equal((multi.match(/class="cool-pill"/g) || []).length, 2);

// 6004 撞线（cap 持久化，重启加载后仍在）的 pill：撞线时间 + 恢复时间都要画。
// 文案里「撞线」这个词本身就区分了两类：临时限流没有撞线时刻。
const hit = until - 7200;
const capPill = accountRow(base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: hit},
]}));
assert(capPill.includes('hy3 · 撞线 ' + fmtAt(hit) + ' · 恢复 ' + localTime));
assert(capPill.includes('时间为本地时间'));
assert(capPill.includes('>可用</span>'));  // 同账号其他模型照常。
// 撞线时间坏值时回退成普通 pill，不能画成「撞线 」。
const badCap = accountRow(base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: 'bad-value'},
]}));
assert(badCap.includes('hy3 · 恢复时刻 ' + localTime));
assert(!badCap.includes('撞线'));

// 「当前禁用账号与模型」：两类模型级限流都有真实的起始时刻——6004 用上游给的
// 撞线时刻（cappedAt），临时 429 用 hub 记下的限流时刻（startedAt）。旧实现
// 只把 cappedAt 画进「撞线时间」列，临时 429 那行永远是个「—」，把 60s 的短时
// parking 和几小时的 6004 画成同一副样子。
const started = until - 300;   // 这次 429 是何时发生的
window.ACCOUNTS = [base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: hit, startedAt: hit},
  {model: 'glm-5.3', expiresAt: until, startedAt: started},
]})];
window.VIEW_REALM = 'cn';
const disabled = disabledRows();
const capEntry = disabled.find(r => r.scope === 'hy3');
const plainEntry = disabled.find(r => r.scope === 'glm-5.3');
assert.equal(capEntry.cappedAt, fmtAt(hit));
assert.equal(capEntry.until, localTime);
assert.equal(plainEntry.cappedAt, '');
assert.equal(plainEntry.until, localTime);
assert.equal(capEntry.startedAt, fmtAt(hit), '6004 行的起始时刻就是撞线时刻');
assert.equal(plainEntry.startedAt, fmtAt(started), '429 行的起始时刻是限流时刻');

// 两类模型级限流的「原因」不能共用一句：带 cappedAt 的是上游 6004 配额耗尽
// （恢复时间以小时计），不带 cappedAt 的只是一次 429 临时限流（60s~10m 的短
// 窗口）。旧文案把后者也写成「配额耗尽（code 6004）」，会让人以为要等几小时。
assert.ok(/配额耗尽/.test(capEntry.reason) && /6004/.test(capEntry.reason),
  '6004 撞线行如实说配额耗尽：' + capEntry.reason);
assert.ok(!/6004/.test(plainEntry.reason),
  '429 临时限流行不得提 code 6004：' + plainEntry.reason);
assert.ok(/临时限流/.test(plainEntry.reason) && /429/.test(plainEntry.reason),
  '429 临时限流行如实说临时限流：' + plainEntry.reason);

renderDisabled();
const table = dom.byId('disabledList').innerHTML;
assert(table.includes('开始时间'), '禁用总览表头带开始时间列');
assert(!table.includes('撞线时间'), '旧的「撞线时间」列头已被中性措辞取代');
assert(table.includes('hy3') && table.includes(fmtAt(hit)), '6004 行画出撞线时刻');
assert(table.includes(fmtAt(started)), '429 行画出限流时刻（不再是「—」）');
assert(table.includes('该模型配额耗尽（code 6004）'), '6004 行按配额耗尽呈现');
assert(table.includes('该模型被上游临时限流（429）'), '429 行按临时限流呈现');

// 起始时刻缺失时（老数据 / 后端还没回填）该列回落成「—」，不得编造。
window.ACCOUNTS = [base({modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]})];
const noStart = disabledRows().find(r => r.scope === 'glm-5.3');
assert.equal(noStart.startedAt, '');
renderDisabled();
assert(dom.byId('disabledList').innerHTML.includes('—'),
  '没有起始时刻时该列留「—」而不是假值');

const unsafe = accountRow(base({modelCooldowns: [
  {model: '"><img src=x onerror=alert(1)>', expiresAt: until},
  {model: 'invalid', expiresAt: 'bad-value'},
]}));
assert(!unsafe.includes('<img src=x'));
assert(unsafe.includes('&quot;&gt;&lt;img'));
assert(!unsafe.includes('invalid ·'));

/* 排序后胶囊必须还是紧跟「它自己那行」账号。accountRow() 把 cool-row 作为独立
   的兄弟行追加，applyTableSort() 早先按「一行 = 一个排序单元」摊平重排，胶囊
   就被留在原地：点过任意可排序表头后变成「账号 账号 胶囊 胶囊」（按版本列排时
   账号行与空胶囊行各自扎堆）。这里按页面自己的排序入口跑一遍。 */
const mkTr = html => {
  const tr = dom.makeElement('tr');
  const openTag = /^<tr([^>]*)>/.exec(html);
  const cls = openTag && /class="([^"]*)"/.exec(openTag[1]);
  if(cls) tr.className = cls[1];
  const body = html.replace(/^<tr[^>]*>/, '').replace(/<\/tr>$/, '');
  body.split(/<td/).slice(1).forEach(src => {
    const td = dom.makeElement('td');
    const open = src.indexOf('>');
    const attr = open < 0 ? '' : src.slice(0, open);
    const forced = /\bdata-sort="([^"]*)"/.exec(attr);
    if(forced) td.setAttribute('data-sort', forced[1]);
    td.textContent = src.slice(open + 1).replace(/<\/td>$/, '')
      .replace(/<[^>]*>/g, '').trim();
    tr.appendChild(td);
  });
  return tr;
};

// 1 / 3 / 5 有冷却、2 / 4 没有，交错分布：排错时胶囊会聚成一块。
const accounts = [1, 2, 3, 4, 5].map(i => (i % 2 ? base({
  uid: 'uid-sort-' + i, nickname: 'acct' + i,
  modelCooldowns: [{model: 'model-acct' + i, expiresAt: until}],
}) : base({uid: 'uid-sort-' + i, nickname: 'acct' + i})));

const tbody = dom.makeElement('tbody');  // 先建，好让 table.querySelector 拿到它
/* applyTableSort() 用「对已在册的子节点 appendChild = 移动」把行按新顺序摆一遍。
   假 DOM 的 appendChild 只在**换父节点**时摘除，同父重复 append 会留成两份——那
   是桩与真 DOM 的差异，不是被测代码的问题，这里给 tbody 补上移动语义。 */
const nativeAppend = tbody.appendChild;
tbody.appendChild = child => {
  const i = tbody.children.indexOf(child);
  if(i >= 0) tbody.children.splice(i, 1);
  return nativeAppend(child);
};
const sortTable = dom.makeElement('table', {querySelector: {tbody}});
const ths = [];
const thead = dom.makeElement('thead', {querySelectorAll: {th: ths}});
const headRow = dom.makeElement('tr');
// 与 dashboard.html 里 account-cards 的表头逐列对齐：列数与文字决定 colKind()
// 走登记表还是按内容推断，错一列就测不到真实列。
['', '版本', '账号', '状态', '今日活动', '出口', '权重', '积分', '请求', '总 token', '缓存', '操作']
  .forEach(label => {
    const th = dom.makeElement('th');
    th.textContent = label;
    ths.push(th);
    headRow.appendChild(th);
  });
thead.appendChild(headRow);
sortTable.appendChild(thead);
sortTable.appendChild(tbody);
/* 假 DOM 的 querySelector / querySelectorAll 默认返回空：table 找不到 thead /
   tbody 会在「不是数据表」那一步直接返回，thead 找不到 th 会让 kinds 为空、在
   「这一列不可排」那一步返回——两种情况都测不到排序，必须先接上。 */
sortTable._options.querySelector.thead = thead;
const realById = document.getElementById;
document.getElementById = id => (id === 'accounts' ? sortTable : realById(id));
sortTable.id = 'accounts';

// mkTr 只摊平一行；accountRow() 的 HTML 里账号行 + 胶囊行要分别落成 TR。
const fill = () => {
  tbody.children.slice().forEach(r => tbody.removeChild(r));
  accounts.forEach(a => accountRow(a)
    .split(/(?=<tr)/).filter(Boolean)
    .forEach(chunk => tbody.appendChild(mkTr(chunk))));
};

const layout = () => tbody.children
  .filter(r => r.tagName === 'TR')
  .map(r => (r.classList.contains('cool-row') ? 'C' : 'A'));

/* 5 个账号里 1 / 3 / 5 有胶囊：初始形态就是 AC A A C A A C（没有胶囊的账号行
   只有 A）。排序要保住的是「每个 C 都紧跟在它自己的 A 后面」，不是 A/C 交替。 */
const EXPECTED = 'ACAACAAC';
const checkPairs = (label, shape) => {
  assert.equal(shape, EXPECTED, label + ' 把胶囊排散了：' + shape);
  for(let i = 0; i < tbody.children.length; i++){
    const row = tbody.children[i];
    if(!row.classList.contains('cool-row')) continue;
    const acctRow = tbody.children[i - 1];
    assert.ok(acctRow && !acctRow.classList.contains('cool-row'),
      label + '：胶囊行前面没有账号行（行形态 ' + shape + '）');
    // 账号列除了昵称还有来源、到期、按钮文案，只取 acctN 这一段。
    const name = (/acct\d+/.exec(acctRow.children[2].textContent) || [''])[0];
    assert.ok(name, '账号列里找不到昵称：' + acctRow.children[2].textContent);
    assert.ok(row.children[0].textContent.includes('model-' + name),
      label + '：胶囊跟错了账号——期望 model-' + name + '，实际「'
      + row.children[0].textContent + '」（行形态 ' + shape + '）');
  }
};

fill();
checkPairs('未排序', layout().join(''));
assert.equal(tbody.children.length, 8, '5 个账号行 + 3 个胶囊行');

/* 每个可排序列、两个方向都点一次：胶囊里的模型名必须还属于它上面那个账号，
   这才是「胶囊跟没跟错账号」的直接判据。 */
for(const col of [0, 1, 2, 3, 5, 7, 8, 9]){
  for(const dir of ['desc', 'asc']){
    fill();
    SORT_STATE.accounts = {col: col, dir: dir};
    applyTableSort('accounts');
    checkPairs('第 ' + col + ' 列 ' + dir + ' 排序', layout().join(''));
  }
}

console.log('model cooldown dashboard assertions passed');
