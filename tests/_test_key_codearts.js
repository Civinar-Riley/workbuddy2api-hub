/* API Key 的 CodeArts 出口：列表徽章 + 模型准入清单（#801 / #802）。
 *
 * 两个都出在「只看 intl / cn」上：
 *   1. 列表卡片的出口徽章只认 intl / cn，绑定 codearts 的 Key 落进兜底分支被画成
 *      「跟随面板切换」——绑定其实生效了，界面却说没绑（#801）。
 *   2. 模型准入的候选清单只遍历 intl / cn，而 `/v1/models?realm=codearts` 的目录
 *      与这两个区域完全不交集，于是绑定 CodeArts 出口的 Key 一个 CodeArts 模型都
 *      选不到，只能手填 id（#802）。
 *
 * 这里钉住：徽章覆盖三种出口、清单覆盖 codearts、绑定了出口的 Key 只取该出口、
 * 清单按出口分别缓存（以前只有一个全局缓存，先打开的 Key 决定了后面所有 Key）。
 *
 * Run with Node: node tests/_test_key_codearts.js
 */
'use strict';
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const script = dashboardScript();

/* ---- 结构：徽章与清单都覆盖 codearts ---------------------------------- */

assert.ok(/function keyRealmBadge\(realm, px\)/.test(script),
  '出口徽章应统一走 keyRealmBadge()，避免两处写法再次不一致');
assert.ok(/realm === 'codearts'\) return '<span class="realm-badge-codearts"/.test(script),
  'keyRealmBadge 必须有 codearts 分支（并复用已有的 .realm-badge-codearts 样式）');
assert.ok(/const realmBadge = keyRealmBadge\(row\.realm\);/.test(script),
  'Key 列表卡片应使用 keyRealmBadge，而不是自己写三目');

assert.ok(/async function loadKeyModelCatalog\(row\)/.test(script),
  'loadKeyModelCatalog 应收 Key（按绑定出口取目录）');
assert.ok(/if\(!bound && codeartsOn\) realms\.push\('codearts'\)/.test(script),
  '跟随面板切换的 Key 在 CodeArts 启用时也要取 codearts 目录');
assert.ok(/KEY_MODEL_CATALOG_CACHE/.test(script),
  '模型清单应按出口分别缓存，不能一个全局缓存决定所有 Key');
assert.ok(!/window\.KEY_MODEL_CATALOG\b/.test(script),
  '旧的单一缓存 window.KEY_MODEL_CATALOG 应已移除');

/* ---- 行为：徽章文案 ---------------------------------------------------- */

dom.installDom();
const calls = [];
global.fetch = function(url, options){
  calls.push(String(url));
  let payload = {object: 'list', data: []};
  if(String(url).indexOf('/v1/models?realm=codearts') === 0){
    payload = {object: 'list', realm: 'codearts',
               data: [{id: 'GLM-5.2'}, {id: 'kimi-k3.1-pro'}]};
  } else if(String(url).indexOf('/v1/models?realm=intl') === 0){
    payload = {object: 'list', realm: 'intl', data: [{id: 'deepseek-v4.1-flash'}]};
  } else if(String(url).indexOf('/v1/models?realm=cn') === 0){
    payload = {object: 'list', realm: 'cn', data: [{id: 'deepseek-v4-flash'}]};
  }
  return Promise.resolve({
    status: 200, ok: true,
    json: function(){ return Promise.resolve(payload); },
    text: function(){ return Promise.resolve(JSON.stringify(payload)); },
  });
};

const realLog = console.log;
console.log = function(){};
let api;
try {
  api = new Function(script + `
    return {
      keyRealmBadge: keyRealmBadge,
      loadKeyModelCatalog: loadKeyModelCatalog,
      cache: () => window.KEY_MODEL_CATALOG_CACHE,
      setRealms: function(list){ window.REALM_OPTIONS = list; },
      clearCache: function(){
        window.KEY_MODEL_CATALOG_CACHE = {};
        try { delete window.KEY_MODEL_CATALOG; } catch(e) { window.KEY_MODEL_CATALOG = undefined; }
      },
    };`)();
} catch(e){
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
console.log = realLog;

/* 1. 徽章：三种出口各回各的，未绑定才是「跟随面板切换」。 */
assert.ok(/realm-badge-codearts[^>]*>固定 CodeArts 出口</.test(api.keyRealmBadge('codearts')),
  '绑定 CodeArts 出口的 Key 必须显示「固定 CodeArts 出口」，不能落到兜底分支');
assert.ok(/realm-badge-intl[^>]*>固定国际版出口</.test(api.keyRealmBadge('intl')),
  'intl 徽章保持原样');
assert.ok(/realm-badge-cn[^>]*>固定国内版出口</.test(api.keyRealmBadge('cn')),
  'cn 徽章保持原样');
assert.ok(/跟随面板切换/.test(api.keyRealmBadge('')), '未绑定出口才显示「跟随面板切换」');
assert.ok(!/固定 CodeArts 出口/.test(api.keyRealmBadge('')),
  '未绑定出口不能被画成固定出口');

/* 2. 绑定 codearts 的 Key：只取 codearts 目录，且条目不混入 intl / cn。 */
(async function(){
  api.clearCache();
  api.setRealms(['intl', 'cn', 'codearts']);
  calls.length = 0;
  let models = await api.loadKeyModelCatalog({realm: 'codearts'});
  const ids = models.map(m => m.id).sort();
  assert.deepStrictEqual(ids, ['GLM-5.2', 'kimi-k3.1-pro'],
    '绑定 CodeArts 出口的 Key 应只列 CodeArts 的模型');
  assert.deepStrictEqual(calls, ['/v1/models?realm=codearts'],
    '绑定出口的 Key 只应请求该出口的目录');
  assert.strictEqual(models[0]._realm, 'codearts',
    '清单条目要带上来源出口，picker 才能标出 CodeArts');

  /* 3. 另一个出口的 Key 不应命中 codearts 的缓存（以前是全局缓存）。 */
  calls.length = 0;
  await api.loadKeyModelCatalog({realm: 'intl'});
  assert.deepStrictEqual(calls, ['/v1/models?realm=intl'],
    '换一个出口的 Key 要重新取目录，不能复用上一个 Key 的清单');

  /* 4. 跟随面板切换的 Key：intl / cn + codearts 都取，合并去重。 */
  calls.length = 0;
  const all = await api.loadKeyModelCatalog({});
  assert.deepStrictEqual(calls,
    ['/v1/models?realm=intl', '/v1/models?realm=cn', '/v1/models?realm=codearts'],
    '未绑定出口的 Key 应取 intl / cn / codearts 三份目录');
  assert.deepStrictEqual(all.map(m => m.id).sort(),
    ['GLM-5.2', 'deepseek-v4-flash', 'deepseek-v4.1-flash', 'kimi-k3.1-pro'],
    '未绑定出口的 Key 应能看到三个区域的模型');

  /* 5. CodeArts 未启用时不去请求它（服务端会回 503）。 */
  api.clearCache();
  api.setRealms(['intl', 'cn']);
  calls.length = 0;
  await api.loadKeyModelCatalog({});
  assert.ok(calls.indexOf('/v1/models?realm=codearts') < 0,
    'CodeArts 未启用时不应去取 codearts 目录');

  console.log('_test_key_codearts.js: all assertions passed');
})().catch(function(e){
  console.error(e && e.stack || e);
  process.exit(1);
});
