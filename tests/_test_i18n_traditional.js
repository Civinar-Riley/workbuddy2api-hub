/* 正體中文（台灣）介面：轉換、切換與持久化。
 *
 * 上游原本只有「简体中文 ⇄ English」兩態。這個套件釘住第三態：
 *   1. `WB_I18N.translateHant()` 把簡體字串轉成正體台灣用語；
 *   2. 語言切換是 简 → 繁 → English → 简，按鈕文字與 <html lang> 跟著走；
 *   3. 偏好寫進 localStorage，重新載入時直接以正體中文啟動；
 *   4. 切回簡中時要還原原文，不能把 DOM 留在上一種語言。
 *
 * Run with Node: node tests/_test_i18n_traditional.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml} = require('./_dashboard_source.js');

const html = dashboardHtml();
const langIndex = html.indexOf("var LANG_KEY = 'wb-lang';");
assert.ok(langIndex > 0, 'dashboard.html 裡找不到 i18n 區塊');
const start = html.lastIndexOf('(function(){', langIndex);
const end = html.indexOf('})();', langIndex) + 5;
assert.ok(start > 0 && end > start, 'i18n 區塊的起訖抓不到');
const source = html.slice(start, end);

function makeElement(tag, id) {
  const attrs = {};
  const kids = [];
  return {
    nodeType: 1,
    tagName: tag,
    id: id || '',
    parentNode: null,
    childNodes: kids,
    textContent: '',
    title: '',
    classList: {
      add() {}, remove() {},
      toggle() {}, contains() { return false; },
    },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    // 面板的 doAttrs() 会先问一次 hasAttributes() 来跳过完全没属性的元素
    // （整树 walk 的固定开销），假元素也得按真 DOM 把成员补齐。
    hasAttributes() { return Object.keys(attrs).length > 0; },
    getAttribute(k) { return attrs[k] == null ? null : attrs[k]; },
    setAttribute(k, v) { attrs[k] = String(v); },
  };
}

function makeText(data) {
  return {nodeType: 3, data, parentNode: null, __wbSrc: null, __wbOut: null};
}

function makeHarness(stored, instanceLang, urlLang) {
  const htmlEl = makeElement('HTML', 'html');
  if (instanceLang) htmlEl.setAttribute('data-ui-language', instanceLang);
  const text = makeText('网关设置');
  text.parentNode = htmlEl;
  htmlEl.childNodes.push(text);
  const label = makeElement('SPAN', 'langToggleLabel');
  const btn = makeElement('BUTTON', 'langToggleBtn');
  const elements = {langToggleLabel: label, langToggleBtn: btn};
  const store = new Map();
  if (stored) store.set('wb-lang', stored);
  const doc = {
    readyState: 'complete',
    documentElement: htmlEl,
    getElementById: (id) => elements[id] || null,
    addEventListener() {},
  };
  const win = {confirm() { return true; }, alert() {}, location: {search: urlLang ? '?lang=' + encodeURIComponent(urlLang) : ''}};
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
  };
  const MO = function() { this.observe = () => {}; this.disconnect = () => {}; };
  new Function('window', 'document', 'localStorage', 'MutationObserver', source)(
    win, doc, storage, MO);
  return {win, doc, htmlEl, label, btn, text, storage, store};
}

let checks = 0;
function check(ok, msg) {
  assert.ok(ok, msg);
  checks += 1;
}

// 1. 預設簡中；按鈕提示下一個語言。
const zh = makeHarness();
check(zh.win.WB_I18N.current() === 'zh', '預設語言應為 zh');
check(zh.htmlEl.getAttribute('lang') === 'zh-CN', '預設 html lang 應為 zh-CN');
check(zh.label.textContent === '繁', '簡中時按鈕應提示「繁」');
check(zh.btn.title.includes('简体中文'), '簡中時 title 應提到簡體中文');

// 2. 台灣用語轉換。
const terms = [
  ['网关设置', '閘道設定'],
  ['刷新凭证', '重新整理憑證'],
  ['内存', '記憶體'],
  ['网络', '網路'],
  ['软件', '軟體'],
  ['默认', '預設'],
  ['登录', '登入'],
  ['设备 Token 文件', '裝置 Token 檔案'],
  ['桌面 chat 兼容头', '桌面 chat 相容標頭'],
  ['重新加载', '重新載入'],
  // 签到用「簽」而不是「籤」：字表里 签 → 籤 是错的，签到、签名都该是簽。
  ['签到与活跃记录', '簽到與活躍記錄'],
];
for (const [src, want] of terms) {
  check(zh.win.WB_I18N.translateHant(src) === want,
        `translateHant(${src}) 應為 ${want}，實得 ${zh.win.WB_I18N.translateHant(src)}`);
}

// 3. 切到正體中文：狀態、DOM、按鈕與 localStorage 都要跟著走。
zh.win.WB_I18N.set('zh-Hant');
check(zh.win.WB_I18N.current() === 'zh-Hant', '切換後應為 zh-Hant');
check(zh.htmlEl.getAttribute('data-lang') === 'zh-Hant', 'data-lang 應為 zh-Hant');
check(zh.htmlEl.getAttribute('lang') === 'zh-Hant-TW', 'html lang 應為 zh-Hant-TW');
check(zh.text.data === '閘道設定', 'DOM 文字應轉成正體中文');
check(zh.label.textContent === 'EN', '正體中文時按鈕應提示 EN');
check(zh.btn.title.includes('正體中文'), '正體中文時 title 應提到正體中文');
check(zh.storage.getItem('wb-lang') === 'zh-Hant', '偏好應寫入 localStorage');

// 4. 正體 → English → 簡中，一輪循環。
zh.win.toggleLang();
check(zh.win.WB_I18N.current() === 'en', '正體後 toggle 應到 en');
check(zh.htmlEl.getAttribute('lang') === 'en', 'en 時 html lang 應為 en');
check(zh.storage.getItem('wb-lang') === 'en', 'en 偏好應寫入 localStorage');
check(zh.label.textContent === '简', 'en 時按鈕應提示「简」');

zh.win.toggleLang();
check(zh.win.WB_I18N.current() === 'zh', 'en 後 toggle 應回到 zh');
check(zh.htmlEl.getAttribute('lang') === 'zh-CN', '回到簡中時 html lang 應為 zh-CN');
check(zh.storage.getItem('wb-lang') === 'zh', '回到簡中時偏好應為 zh');
check(zh.text.data === '网关设置', '回到簡中時 DOM 應還原原文');
check(zh.label.textContent === '繁', '回到簡中時按鈕應提示「繁」');

zh.win.toggleLang();
check(zh.win.WB_I18N.current() === 'zh-Hant', '簡中後 toggle 應再回到 zh-Hant');
check(zh.text.data === '閘道設定', '再次進入正體時 DOM 應再次轉換');

// 5. 重新載入時直接以正體中文啟動。
const boot = makeHarness('zh-Hant');
check(boot.win.WB_I18N.current() === 'zh-Hant', '已存偏好時啟動應為 zh-Hant');
check(boot.htmlEl.getAttribute('lang') === 'zh-Hant-TW', '啟動時 html lang 應為 zh-Hant-TW');
check(boot.text.data === '閘道設定', '啟動時 DOM 應直接轉成正體中文');

// 6. 三層優先序：URL > localStorage > 實例預設。
const instOnly = makeHarness(null, 'zh-Hant');
check(instOnly.win.WB_I18N.current() === 'zh-Hant', '沒有本機偏好時應使用實例預設 zh-Hant');
check(instOnly.text.data === '閘道設定', '實例預設正體時 DOM 應轉換');

const localWins = makeHarness('zh', 'zh-Hant');
check(localWins.win.WB_I18N.current() === 'zh', '本機偏好 zh 應覆蓋實例預設 zh-Hant');
check(localWins.text.data === '网关设置', '本機偏好簡中時 DOM 應維持原文');

const urlWins = makeHarness('zh', 'zh-Hant', 'en');
check(urlWins.win.WB_I18N.current() === 'en', 'URL ?lang=en 應覆蓋本機與實例預設');
check(urlWins.htmlEl.getAttribute('lang') === 'en', 'URL 覆蓋時 html lang 應為 en');

const instEn = makeHarness(null, 'en');
check(instEn.win.WB_I18N.current() === 'en', '沒有本機偏好時應使用實例預設 en');

console.log(`traditional chinese i18n assertions passed (${checks} checks)`);
