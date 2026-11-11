/* 模型清单的来源横幅与扩充列。

面板必须把一批清单的出处说得醒目：通过上游 API 拿到的 → 绿色状态条
「当前为通过 API 获取的最新数据」+ 刷新时间；不是 API 拿的（本地配置
文件/内置快照）→ 黄色警示「当前为项目默认模型信息（未经 API 刷新）：
模型、倍率数据可能不一致」。数据来自 /v1/models 响应的 catalog 字段
（见 wb_proxy.catalog_meta），由 renderModelsSourceLine() 渲染。模型表
本身也要带上名称、默认采样参数与思考细节（默认档位、能否关闭思考），
而不是只有 id 和倍率。

另外钉住一个给兄弟套件用的契约：模型 ID 单元格里只放 id 本身，别的列
的内容不许混进来——_test_model_probes.js 用该单元格的纯文本当行键，
一旦把它变成 "id 名称" 拼接，那边按行断言会整体错位。

Run with Node.
*/
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();

// One shared fake DOM for every dashboard suite: tests/_dom_stub.js.
const dom = require('./_dom_stub.js');
const tbody = dom.makeElement('tbody');
dom.installDom({
  querySelector: {'#modelsTable tbody': tbody},
  fetch: () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({data: [], catalog: null}),
    text: () => Promise.resolve('{}'),
  }),
});

const api = new Function(script + `
  ; return { renderAvailableModels, renderModelsSourceLine };`)();

let checks = 0;
const check = (label, cond, extra) => {
  checks += 1;
  assert.ok(cond, label + (extra ? '  [' + extra + ']' : ''));
};

// ---- 来源横幅 ----
const line = document.getElementById('modelsSourceLine');
const renderLine = meta => {
  line.innerHTML = '';
  line.title = '';
  line.style.cssText = '';
  api.renderModelsSourceLine(meta);
  return {html: line.innerHTML, style: line.style.cssText, title: line.title};
};

const stamp = '\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}';

// 1. 上游 API：绿色状态条，写「通过 API 获取的最新数据」+ 刷新时间；
//    不许出现警示语气。
let out = renderLine({source: 'remote', fetched_at: 1700000000, file_mtime: null});
check('api banner acclaims the API data', out.html.includes('当前为通过 API 获取的最新数据'), out.html);
check('api banner shows the refresh time',
      new RegExp('刷新时间 ' + stamp).test(out.html), out.html);
check('api banner carries the ok marker', out.html.includes('✓'), out.html);
check('api banner never warns about stale data',
      !out.html.includes('可能不一致') && !out.html.includes('项目默认'), out.html);
check('api banner is the green tone', out.style.includes('--accent2-soft'), out.style);

// 2. 配置文件：黄色警示「当前为项目默认模型信息」「模型、倍率数据可能不一致」，
//    出处小字保留文件写入/读取时间。
out = renderLine({source: 'cache', fetched_at: 1700000000, file_mtime: 1600000000});
check('config-file banner names the default info wording',
      out.html.includes('当前为项目默认模型信息'), out.html);
check('config-file banner warns about inconsistent data',
      out.html.includes('模型、倍率数据可能不一致'), out.html);
check('config-file banner carries the warning marker', out.html.includes('⚠'), out.html);
check('config-file banner keeps the real origin',
      out.html.includes('本地配置文件'), out.html);
check('config-file banner shows the file write time',
      new RegExp('文件写入于 ' + stamp).test(out.html), out.html);
check('config-file banner shows the gateway read time',
      new RegExp('网关读取于 ' + stamp).test(out.html), out.html);
check('config-file banner never claims API freshness',
      !out.html.includes('通过 API 获取的最新数据'), out.html);
check('config-file banner is the warning tone', out.style.includes('--warn-soft'), out.style);

// legacy 是同一种文件、同一句警示。
out = renderLine({source: 'legacy', fetched_at: 1700000000, file_mtime: null});
check('legacy warns like a config file too',
      out.html.includes('当前为项目默认模型信息') && out.html.includes('本地配置文件'), out.html);

// 3. 内置快照：同样是警示，但出处如实写内置快照。
out = renderLine({source: 'bundled', fetched_at: 1700000000, file_mtime: null});
check('bundled snapshot shows the warning banner',
      out.html.includes('当前为项目默认模型信息'), out.html);
check('bundled snapshot keeps the real origin', out.html.includes('内置快照'), out.html);
check('bundled snapshot never claims API freshness',
      !out.html.includes('通过 API 获取的最新数据'), out.html);

// 4. 旧版服务端没有 catalog 字段：如实提示未知，而不是猜一个来源。
out = renderLine(null);
check('missing meta is reported as unknown', out.html.includes('未知'), out.html);
check('missing meta does not invent a source',
      !out.html.includes('通过 API 获取的最新数据') && !out.html.includes('项目默认'), out.html);

// ---- 表格扩充列 ----
function render(models){
  tbody.innerHTML = '';
  global.MODELS_DATA = models;
  window.MODELS_DATA = models;
  api.renderAvailableModels();
  return tbody.innerHTML;
}

// The rendered table as one object per row: data-label -> cell HTML, keyed by
// the model ID text (same reader the probe suite uses - see the header note).
const text = s => String(s || '').replace(/<[^>]*>/g, '').trim();
function rowsById(html){
  const rows = {};
  for(const row of html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)){
    const cells = {};
    for(const cell of row[1].matchAll(/<td([^>]*)>([\s\S]*?)<\/td>/g)){
      const label = (cell[1].match(/data-label="([^"]*)"/) || [])[1];
      if(label) cells[label] = cell[2];
    }
    const id = text(cells['模型 ID']);
    if(id) rows[id] = cells;
  }
  return rows;
}

// 5. 全字段模型：名称（星标 + 描述 tooltip）、采样参数、思考细节。
let rows = rowsById(render([{
  id: 'hy3', name: '混元 3', description: '推理旗舰', is_default: true,
  credits: 'x0.50', supports_reasoning: true,
  reasoning_efforts: ['low', 'high'], reasoning_default_effort: 'high',
  reasoning_can_disable: false, temperature: 0.9, top_p: 1,
}]));
let r = rows['hy3'];
check('the reader finds the row by its bare id', !!r, JSON.stringify(Object.keys(rows)));
check('the id cell stays the bare id', text(r['模型 ID']) === 'hy3', r['模型 ID']);
check('name column shows the display name', r['名称'].includes('混元 3'), r['名称']);
check('default model carries the star', r['名称'].includes('★'), r['名称']);
check('description rides the name tooltip',
      r['名称'].includes('推理旗舰'), r['名称']);
check('sampling column shows temperature and top_p',
      r['默认采样参数'].includes('温度 0.9') && r['默认采样参数'].includes('top_p 1'),
      r['默认采样参数']);
check('thinking column shows the default level',
      r['思考档位'].includes('默认 high'), r['思考档位']);
check('thinking column says thinking cannot be disabled',
      r['思考档位'].includes('不可关闭思考'), r['思考档位']);
check('thinking levels are still listed as badges',
      r['思考档位'].includes('low') && r['思考档位'].includes('high'),
      r['思考档位']);

// 6. 稀疏模型：没名称、没采样、能关闭思考——各自的空缺如实呈现。
rows = rowsById(render([{
  id: 'm2', credits: 'x0.20', temperature: 1,
  reasoning_efforts: ['low'], reasoning_can_disable: true,
}]));
r = rows['m2'];
check('no name renders a dash', text(r['名称']) === '—', r['名称']);
check('no star without is_default', !r['名称'].includes('★'), r['名称']);
check('temperature renders raw', r['默认采样参数'].includes('温度 1'), r['默认采样参数']);
check('no top_p listed when absent',
      !r['默认采样参数'].includes('top_p'), r['默认采样参数']);
check('thinking can be disabled when upstream says so',
      r['思考档位'].includes('可关闭思考'), r['思考档位']);

// 7. 全缺模型：两个新列都是破折号，不凭空造值。
rows = rowsById(render([{ id: 'm3' }]));
r = rows['m3'];
check('empty sampling renders a dash', text(r['默认采样参数']) === '—', r['默认采样参数']);
check('empty thinking renders a dash', text(r['思考档位']) === '—', r['思考档位']);

// 8. 「对外名称」列：只在模型别名开关打开时出现；开关关着整列隐藏
//    （表头 + 单元格），不给人「填了却没生效」的错觉。
window.MODEL_ALIASES = {'glm-5.2': 'deepseek-v4.1-flash'};
window.MODEL_ALIASES_ENABLED = false;
const offHtml = render([{ id: 'hy3' }, { id: 'deepseek-v4.1-flash' }]);
check('alias switch off: the external-name cell is hidden',
      /data-label="对外名称" hidden/.test(offHtml) && !/<input[^>]*class="model-map-input"/.test(offHtml),
      offHtml.slice(0, 400));

window.MODEL_ALIASES_ENABLED = true;
rows = rowsById(render([{ id: 'hy3' }, { id: 'deepseek-v4.1-flash' }]));
check('every row gets an external-name cell',
      !!rows['hy3']['对外名称'] && !!rows['deepseek-v4.1-flash']['对外名称'],
      JSON.stringify(Object.keys(rows)));
check('the external-name control is a free-text input',
      /<input[^>]*class="model-map-input"/.test(rows['hy3']['对外名称'])
      && !/<select/.test(rows['hy3']['对外名称']), rows['hy3']['对外名称']);
check('a model with no external name renders an empty input',
      /value=""/.test(rows['hy3']['对外名称']), rows['hy3']['对外名称']);
check('an existing external name is pre-filled on the model it points at',
      /value="glm-5\.2"/.test(rows['deepseek-v4.1-flash']['对外名称']),
      rows['deepseek-v4.1-flash']['对外名称']);
check('the input carries the model id it renames',
      /data-model-id="deepseek-v4\.1-flash"/.test(rows['deepseek-v4.1-flash']['对外名称']),
      rows['deepseek-v4.1-flash']['对外名称']);
check('the external name input offers no candidate list',
      !/\blist=/.test(rows['hy3']['对外名称']), rows['hy3']['对外名称']);
check('the external name is not restricted by pattern/readonly',
      !/pattern=|readonly/.test(rows['hy3']['对外名称']), rows['hy3']['对外名称']);

console.log('models catalog source-line assertions passed (' + checks + ' checks)');
