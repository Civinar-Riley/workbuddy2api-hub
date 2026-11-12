/* 模型冷却 / 达日限额的胶囊：必须整行完整可见，不再靠悬停补内容。
 *
 * 由来：账号列只有 180px（`td:nth-child(2){width:180px}`），胶囊文案
 * 「模型 · 撞线 X · 恢复 Y」在长模型名（deepseek-v4.1-flash 有 19 字符）下必然
 * 被省略号切掉。第一版把细节塞进 title——但 title 只在鼠标正好停在被切一半的
 * 胶囊上时才出来，触摸屏根本唤不出来；而真正需要这个时间的场景（判断要不要
 * 等、要不要换号）反而读不到，用户只能去翻运行日志。
 *
 * 所以改成：胶囊从账号列拆出来，紧跟账号行另起一整行（colspan 跨满表头
 * 列数），文本自己完整，悬停不再承担"把被切掉的内容读回来"这件事。
 *
 * 这里钉住四件事：
 *   1. 胶囊在独立的一整行里，colspan 跨满表头列数（helpers / 结构都对）；
 *   2. 可见文本本身完整：模型名 + 撞线时刻 + 恢复时刻 + 本地时间口径，一个
 *      不少；达限胶囊还要有已用 / 上限数值与 0 点恢复；
 *   3. 胶囊上不再挂 title（内容已在可见文本里，再挂就是重复）；
 *   4. 账号列本身不再出现胶囊，账号行 ↔ tbody 行的对应关系没被破坏
 *      （applyTableSort 只重排 TR 的先后）。
 *
 * Run with Node: node tests/_test_model_limit_tip.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const html = dashboardHtml();
const script = dashboardScript();
const dom = require('./_dom_stub.js');
dom.installDom({
  fetch: () => Promise.resolve({ok: true, status: 200, json: () => Promise.resolve({})}),
});

const api = new Function(script + `
  return {
    accountRow, coolRow, limitRow, fmtCoolAt,
    COOL_ROW_COLS, WB_I18N: window.WB_I18N,
  };`)();

const MODEL = 'deepseek-v4.1-flash';   // 19 字符：账号列里必然被切
function check(label, ok, detail){
  assert.ok(ok, label + (detail === undefined ? '' : '  -> ' + detail));
}

const until = Math.floor(Date.now() / 1000) + 3600;
const capped = Math.floor(Date.now() / 1000) - 7200;
const pad = n => String(n).padStart(2, '0');
const fmtAt = e => { const d = new Date(e * 1000);
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
    + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()); };

/* ---- 1. 独立整行：colspan 跨满表头列数 ------------------------------- */

// 表头列数是硬约定：改表头不同步这里，胶囊就会只跨一半、重新被切。
// 表头由 renderAccounts() 用字符串拼出来，所以从那段源码里数，而不是整页数
// （整页有 35 个 th，会把别的表一起算进来）。
const theadSrc = script.match(/table class="data-cards account-cards"><thead><tr>'([\s\S]*?)<\/tr><\/thead>/);
check('抓到账号表的表头源码', !!theadSrc, 'renderAccounts 的表头条目变了？');
const thCount = theadSrc ? (theadSrc[1].match(/<th /g) || []).length : -1;
check('COOL_ROW_COLS 与表头列数一致（12）', api.COOL_ROW_COLS === 12, api.COOL_ROW_COLS);
check('账号表确有 12 个 th', thCount === 12, thCount);

const coolHtml = api.coolRow({modelCooldowns: [
  {model: MODEL, expiresAt: until, cappedAt: capped},
]});
check('冷却胶囊渲染成独立的一整行', /^<tr class="cool-row"><td colspan="12">/.test(coolHtml), coolHtml);
check('这一行只装胶囊、不再带 data-label 装饰', !/data-label/.test(coolHtml), coolHtml);

const limitHtml = api.limitRow({
  modelDailyTokenLimit: 200000000,
  modelDailyTokens: {[MODEL]: 201000000},
});
check('达限胶囊也渲染成独立的一整行', /^<tr class="cool-row"><td colspan="12">/.test(limitHtml), limitHtml);

/* ---- 2. 可见文本本身完整（不再靠 title 兜底） ------------------------- */

const visibleCapped = coolHtml.match(/>([^<>]*撞线[^<>]*)</);
check('6004 胶囊的可见文本完整：模型名',
  !!visibleCapped && visibleCapped[1].includes(MODEL), visibleCapped && visibleCapped[1]);
check('6004 胶囊的可见文本完整：撞线时刻',
  !!visibleCapped && visibleCapped[1].includes(fmtAt(capped)), visibleCapped && visibleCapped[1]);
check('6004 胶囊的可见文本完整：恢复时刻',
  !!visibleCapped && visibleCapped[1].includes(fmtAt(until)), visibleCapped && visibleCapped[1]);
check('6004 胶囊的可见文本完整：本地时间口径',
  !!visibleCapped && visibleCapped[1].includes('时间为本地时间'), visibleCapped && visibleCapped[1]);

const plainHtml = api.coolRow({modelCooldowns: [{model: MODEL, expiresAt: until}]});
const visiblePlain = plainHtml.match(/>([^<>]*恢复[^<>]*)</);
check('临时限流胶囊的可见文本完整',
  !!visiblePlain && visiblePlain[1] === MODEL + ' · 恢复时刻 ' + fmtAt(until) + '（时间为本地时间）',
  visiblePlain && visiblePlain[1]);

const visibleLimit = limitHtml.match(/>([^<>]*达限[^<>]*)</);
check('达限胶囊的可见文本完整（已用 / 上限 / 0 点恢复）',
  !!visibleLimit && visibleLimit[1] === MODEL + ' · 今日已用 201,000,000'
    + ' / 上限 200,000,000 token 已达限额（本地 0 点恢复）',
  visibleLimit && visibleLimit[1]);

/* ---- 3. 悬停提示只补「可见文本没说的部分」 --------------------------- */

// 用户要的是「去掉悬停里与徽章重复的内容」，不是删掉整个悬停。所以这里两边
// 都要钉：title 必须在（原因 + 影响范围），且不得重复可见文本已有的东西
// （模型名、时刻、本地时间口径）——重复了就等于同一句话说两遍。
function pillTitle(htmlText){
  const m = htmlText.match(/class="cool-pill" title="([^"]*)"/);
  return m ? m[1] : '';
}
const hitTitle = pillTitle(coolHtml);
const plainTitle = pillTitle(plainHtml);
const limitTitle = pillTitle(limitHtml);

check('撞线胶囊带悬停提示', !!hitTitle, coolHtml.slice(0, 120));
check('临时限流胶囊带悬停提示', !!plainTitle, plainHtml.slice(0, 120));
check('达限胶囊带悬停提示', !!limitTitle, limitHtml.slice(0, 120));

// 不重复：模型名与时刻都已在胶囊上，title 里不该再出现。
for(const [label, title, at] of [
  ['撞线', hitTitle, fmtAt(capped)],
  ['临时限流', plainTitle, fmtAt(until)],
  ['达限', limitTitle, fmtAt(until)],
]){
  check(label + '：title 不重复模型名', !title.includes(MODEL), title);
  check(label + '：title 不重复时刻', !title.includes(at), title);
  check(label + '：title 不重复「时间为本地时间」', !title.includes('时间为本地时间'), title);
}
check('撞线：title 不重复「撞线」「恢复」措辞',
  !/撞线|恢复/.test(hitTitle), hitTitle);
check('达限：title 不重复已用 / 上限数值',
  !title_has_numbers(limitTitle), limitTitle);
function title_has_numbers(t){ return /\d{3},\d{3}/.test(t); }

// 补的是原因与影响范围：这些可见文本里没有。
check('撞线：title 说明上游判定额度用满（含 code 6004）',
  /额度用满/.test(hitTitle) && /6004/.test(hitTitle), hitTitle);
check('临时限流：title 说明是 429 临时限流、到点自动恢复',
  /临时限流/.test(plainTitle) && /429/.test(plainTitle) && /自动恢复/.test(plainTitle),
  plainTitle);
check('达限：title 说明触发的是「按模型每日 Token 限额」',
  /按模型每日 Token 限额/.test(limitTitle), limitTitle);
for(const [label, title] of [['撞线', hitTitle], ['临时限流', plainTitle], ['达限', limitTitle]]){
  check(label + '：title 说明影响范围（仅此模型 / 其他模型照常）',
    /仅此模型受限/.test(title) && /同账号其他模型照常接单/.test(title), title);
}
// 旧的 tip* 拼接函数不要再回来：那版把模型名与时刻又拼了一遍，正是重复的来源。
check('旧的 tipCap* 拼接函数没有再引入',
  !/\bfunction tipCapHit|\bfunction tipCapPlain|\bfunction tipModelLimit/.test(script));

/* ---- 4. 账号列不再出现胶囊；行 ↔ 账号的对应关系没坏 ------------------- */

const acctBase = {
  uid: 'uid-tip-0001', nickname: 'synthetic', realm: 'cn', enabled: true,
  source: 'oauth', expiresIn: '24 hours', lastError: '', inCooldown: false,
  cooldownFor: null, machineId: '', credits: null, product: 'cli',
};
const rowHtml = api.accountRow(Object.assign({}, acctBase, {
  modelCooldowns: [{model: MODEL, expiresAt: until, cappedAt: capped}],
  modelDailyTokenLimit: 200000000,
  modelDailyTokens: {[MODEL]: 201000000},
}));
const accountCell = rowHtml.match(/<td style="text-align:left" data-label="账号">([\s\S]*?)<\/td>\s*<td style="text-align:center" data-label="状态">/);
check('账号列里已无胶囊', accountCell && !/cool-pill/.test(accountCell[1]),
  accountCell && accountCell[1].slice(-120));
check('账号行仍以 </tr> 收尾，新行紧跟其后',
  /<\/td><\/tr><tr class="cool-row"><td colspan="12">/.test(rowHtml), rowHtml.slice(-260));
check('整段渲染只多出两行（冷却 + 达限），账号行本身仍是一行',
  (rowHtml.match(/<tr/g) || []).length === 3, (rowHtml.match(/<tr/g) || []).length);

// 「禁用的模型」行同理：可见文本没有 ellipsis，名单再长也会换行显示完整，
// 所以悬停里只留操作提示，不重复名单（重复即噪音）。
const denyRow = api.accountRow(Object.assign({}, acctBase, {
  disabledModels: [MODEL, 'gpt-6-astra'],
}));
const denyTitle = (denyRow.match(/title="(点击编辑该账号禁用的模型[^"]*)"/) || [])[1] || '';
check('禁用名单行带悬停提示', !!denyTitle, denyRow.slice(0, 300));
check('禁用名单的 title 不重复名单内容',
  !!denyTitle && !denyTitle.includes(MODEL) && !denyTitle.includes('gpt-6-astra'),
  denyTitle);
check('禁用名单的 title 只讲点击能干什么',
  /点击编辑/.test(denyTitle), denyTitle);

// 没有冷却、没有达限时不产生多余空行。
check('无冷却无达限时不新增行',
  (api.accountRow(Object.assign({}, acctBase)).match(/<tr/g) || []).length === 1,
  api.accountRow(Object.assign({}, acctBase)).match(/<tr/g));

/* ---- 4b. 续行与账号行之间不许有分割线 ------------------------------- */

// 续行是账号行的「同一块内容」。画一条线会让人以为胶囊不属于上面那个账号
// （实测截图里就是这样，徽章和所属模型看起来是分离的两块）。
// 两处都要处理，只做一处仍会漏出线来：
//   ① cool-row 自己不能画 border-top；
//   ② 账号行要让出 th,td 默认的 border-bottom（否则那条实线还在）。
check('有续行时账号行带 has-alert 标记',
  /^<tr class="has-alert">/.test(rowHtml), rowHtml.slice(0, 40));
check('无续行时账号行不带多余 class',
  /^<tr>/.test(api.accountRow(Object.assign({}, acctBase))),
  api.accountRow(Object.assign({}, acctBase)).slice(0, 40));

const seamStyle = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
  .map(m => m[1]).join('\n');
function seamDecl(selector){
  const at = seamStyle.indexOf(selector + '{');
  return at < 0 ? '' : seamStyle.slice(at + selector.length + 1, seamStyle.indexOf('}', at));
}
const coolTdDecl = seamDecl('table.account-cards tr.cool-row td');
check('续行 td 不画 border-top', /border-top\s*:\s*none/.test(coolTdDecl), coolTdDecl);
check('续行 td 不再有虚线分割', !/dashed/.test(coolTdDecl), coolTdDecl);
const hasAlertDecl = seamDecl('table.account-cards tr.has-alert td');
check('账号行交出 border-bottom（否则默认实线还在）',
  /border-bottom\s*:\s*none/.test(hasAlertDecl), hasAlertDecl);
// 悬停联动要用相邻选择器：~ 会一路点亮后面别的账号的续行。
check('悬停联动用相邻选择器而非 ~',
  !/tr\.has-alert:hover ~/.test(seamStyle)
  && /tr\.has-alert:hover \+ tr\.cool-row/.test(seamStyle));

/* ---- 5. 三语：可见文本照样要能翻译 ----------------------------------- */

// 胶囊文案变成了可见文本（i18n 会走文本节点路径），词典条目仍要在。
// 两种引号都算：DICT 用单引号、HANT_PHRASES 用双引号（值折行时只断言键出现）。
// 键的前后空格按文案实际拼法给：胶囊里「恢复（时间为本地时间）」是紧邻的，
// 所以这里断言的键不带前导空格。
for(const frag of [' · 撞线 ', ' · 恢复 ', ' · 恢复时刻 ', '（时间为本地时间）',
                   ' · 今日已用 ', ' / 上限 ',
                   ' token 已达限额（本地 0 点恢复）']){
  const inEn = script.includes("'" + frag + "'");
  const inHant = script.includes('"' + frag + '"');
  check('词典缺少片段「' + frag.trim() + '」', inEn && inHant,
        'EN=' + inEn + ' Hant=' + inHant);
}

/* ---- 6. 三语：胶囊是可见文本，翻译错就是摆在界面上的错 ---------------- */

// 实测踩过的两个坑，都钉在这里：
//   1. ' 恢复': ''（给「剩余用量」用的那条）会先手吃掉胶囊里的「X 恢复」，
//      英文变成「18:04(times are local)」——「back」整个丢了。所以临时限流那支
//      的措辞必须是「恢复时刻」，绕开这条既有规则。
//   2. 「为」逐字转繁体会出「時間爲本地時間」，台湾惯用「為」，得有整句条目。
//   3. 缺 ' 今日已用 ' / ' / 上限 ' 时会拼出「TodayUsed」「上cap」这种半英半中。
const stripTags = s => s.replace(/<[^>]*>/g, '');
const visibleTexts = [stripTags(coolHtml), stripTags(plainHtml), stripTags(limitHtml)];

const EN_LEFTOVER = ['模型', '撞线', '恢复', '限额', '本地', '上限', '今日已用',
                     '时间为本地时间', '已达', '时刻'];
const HANT_LEFTOVER = ['撞线', '恢复', '限额', '时间为本地时间', '爲'];
// 这些是「半英半中硬拼」的特征串，出现即说明词典条目缺失：
//   TodayUsed（' 今日已用 ' 缺条目时逐词硬拼）、上cap（' / 上限 ' 缺条目）。
const WEIRD_EN = ['TodayUsed', '上cap'];

for(const t of visibleTexts){
  const en = api.WB_I18N.translate(t);
  const enBad = EN_LEFTOVER.filter(w => en.includes(w));
  check('英文不得漏出中文[' + enBad.join('/') + ']', enBad.length === 0, en);
  const weird = WEIRD_EN.filter(w => en.includes(w));
  check('英文不得出现半英半中的怪译文[' + weird.join('/') + ']', weird.length === 0, en);
  const hant = api.WB_I18N.translateHant(t);
  const hantBad = HANT_LEFTOVER.filter(w => hant.includes(w));
  check('繁中不得漏出简体/误转[' + hantBad.join('/') + ']', hantBad.length === 0, hant);
}
// 临时限流那支必须带出「back at」：这是上面第 1 条坑的回归哨。
check('英文里临时限流要译出 back at',
  api.WB_I18N.translate(stripTags(plainHtml)).includes('back at'),
  api.WB_I18N.translate(stripTags(plainHtml)));

/* ---- 6b. 三语：悬停提示也要能翻译 ------------------------------------ */

// title 在 i18n 的 ATTRS 里，会被 translate()/translateHant() 处理。这三条是
// 整句常量（不含动态值），所以应当精确命中整句键——命不中就会退到逐字硬拼，
// 英文界面里冒出中文。
for(const [label, title] of [['撞线', hitTitle], ['临时限流', plainTitle], ['达限', limitTitle]]){
  check(label + '：title 的英文整句键在 DICT 里',
    script.includes("'" + title + "'"), title);
  const en = api.WB_I18N.translate(title);
  check(label + '：title 英文不得漏出中文',
    !/[\u4e00-\u9fff]/.test(en), en);
  check(label + '：title 英文不得整段原样返回（说明没命中键）',
    en !== title, en);
  const hant = api.WB_I18N.translateHant(title);
  check(label + '：title 繁体不得漏出简体', !/仅此模型|同账号|接单|额度用满|临时限流/.test(hant),
    hant);
  check(label + '：title 繁体不得整段原样返回（说明没命中整句键）',
    hant !== title, hant);
}

/* ---- 7. CSS：胶囊整行真的「完整可见」 -------------------------------- */

// 结构与 JS 对了不等于看得见：上一轮就在这里栽过——只改了 HTML，账号列的
// ellipsis 还在，胶囊仍旧被切成半句。所以 CSS 的关键属性链也要钉住。
// 注意页面里有多个窄屏 media 块，且 `max-width:` 后带不带空格两种写法都有，
// 收集时两种都要匹配，否则会漏掉 cool-row 所在那一块。
const cssStyle = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
  .map(m => m[1]).join('\n');
// 取某个 @media 块里指定选择器的声明体（按「选择器 + {」精确定位，
// 只 indexOf(选择器) 会命中注释里同名字符串而取错）。
function cssDeclInMedia(mediaQuery, selector){
  let from = 0;
  for(;;){
    const at = cssStyle.indexOf(mediaQuery, from);
    if(at < 0) return null;
    const open = cssStyle.indexOf('{', at);
    let depth = 1, i = open + 1;
    while(i < cssStyle.length && depth > 0){
      if(cssStyle[i] === '{') depth++;
      else if(cssStyle[i] === '}') depth--;
      i++;
    }
    const block = cssStyle.slice(open + 1, i - 1);
    const hit = block.indexOf(selector + '{');
    if(hit >= 0){
      return block.slice(hit + selector.length + 1, block.indexOf('}', hit));
    }
    from = i;
  }
}
const PILL = 'table.account-cards tr.cool-row .cool-pill';
// 桌面规则（写在 media 之外，对所有宽度生效）
const desktopAt = cssStyle.indexOf(PILL + '{');
check('桌面胶囊规则存在', desktopAt >= 0);
if(desktopAt >= 0){
  const decl = cssStyle.slice(desktopAt + PILL.length + 1,
                              cssStyle.indexOf('}', desktopAt));
  check('桌面胶囊不限宽', /max-width\s*:\s*none/.test(decl), decl);
  check('桌面胶囊不裁切', /overflow\s*:\s*visible/.test(decl), decl);
  check('桌面胶囊不用省略号', /text-overflow\s*:\s*clip/.test(decl), decl);
  check('桌面胶囊自然宽度', /width\s*:\s*auto/.test(decl), decl);
  check('桌面胶囊不换行（整行够宽）', /white-space\s*:\s*nowrap/.test(decl), decl);
}
// 窄屏：6 栏放不下长文案时允许换行，而不是横向溢出卡片
const narrowDecl = cssDeclInMedia('@media (max-width: 640px)', PILL)
                || cssDeclInMedia('@media (max-width:640px', PILL);
check('窄屏胶囊允许换行', !!narrowDecl && /white-space\s*:\s*normal/.test(narrowDecl),
      JSON.stringify(narrowDecl));
// 左对齐必须写在 td[colspan] 居中之后（CSS 后写者胜出），否则胶囊被摆到中间
const posCenter = cssStyle.indexOf('table.data-cards td[colspan]{');
const posLeft = cssStyle.indexOf('table.account-cards tr.cool-row td{justify-content:flex-start');
check('窄屏整行左对齐规则存在', posLeft > 0);
check('左对齐写在 td[colspan] 居中之后（次序对才生效）',
      posCenter > 0 && posLeft > posCenter, 'center=' + posCenter + ' left=' + posLeft);
// 账号列那列的 ellipsis 要留着：那一列确实放不下，别被顺手动坏
const acctPillAt = cssStyle.indexOf('table.account-cards td[data-label="账号"] .cool-pill{');
check('账号列胶囊仍保留 ellipsis（该列确实放不下）',
      acctPillAt >= 0 && /text-overflow\s*:\s*ellipsis/.test(
        cssStyle.slice(acctPillAt, cssStyle.indexOf('}', acctPillAt))));

console.log('model limit row assertions passed');
