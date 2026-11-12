/* 设置页「更新」卡片的契约：显示检查结果，安装必须由用户手动确认。
 *
 * 这一片是纯前端消费：后端 GET /updates、POST /updates/check 与
 * POST /updates/install 已经落地，
 * 面板负责把结论画出来、把两个动作接上去。这里钉住五件事：
 *
 *   1. 三个字段（当前版本 / 最新版本 / 状态）真的被填上，且状态随结论变化；
 *   2. 「立即检查更新」打到 POST /updates/check；
 *   3. **200 不等于成功**：响应体里 ok=false 或 last_error 非空时，卡片与提示
 *      都必须报失败，不能对着一次失败的检查说「已是最新版本」；
 *   4. 「每天自动检查更新」走既有 /settings/save 契约，不用浏览器本地存储；
 *   5. 安装按钮只在已配置更新服务且发现稳定新版本时可用；取消确认不得提交安装；
 *      页面只访问自己网关的相对路径，浏览器从不直接接触 GitHub。
 *
 * Run with Node: node tests/_test_settings_update_ui.js
 */
'use strict';

const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();

// One shared fake DOM for every dashboard suite: tests/_dom_stub.js.
const dom = require('./_dom_stub.js');
const element = id => dom.byId(id);

/* 每一次请求都记下来：这一片最重要的一条断言就是「面板只跟自己网关说话」。 */
const calls = [];
let updatesPayload = {};
let updatesStatus = 200;
let saveReply = {ok: true};
let saveStatus = 200;
let confirmCalls = 0;
let deviceTokenStatus = {
  source: 'file',
  configured_file: '/data/device-token',
  loaded: true,
  length: 48,
  cached_at: 1760000000,
};
let deviceTokenReloadStatus = deviceTokenStatus;

const SETTINGS = {
  panel_password_is_default: false,
  api_keys: [],
  deleted_api_keys: [],
  limits: {},
  pricing_enabled: false,
  pricing_refresh_minutes: 5,
  auto_switch_product: false,
  daily_chat_web: false,
  daily_chat_prefer_flash: false,
  local_web_tools: false,
  update_check_enabled: true,
  device_token_status: deviceTokenStatus,
  desktop_chat_compat: false,
  version: '1.6.17',
  accounts_dir: '/data/accounts',
  usage_dir: '/data/usage',
  settings_file: '/data/settings.json',
};

dom.installDom({
  confirm: () => { confirmCalls += 1; return false; },
  fetch: (url, options) => {
    const target = String(url);
    calls.push({url: target, method: (options && options.method) || 'GET',
                body: options && options.body});
    if(target === '/settings') {
      return Promise.resolve({status: 200, ok: true,
        json: () => Promise.resolve(SETTINGS), text: () => Promise.resolve('')});
    }
    if(target === '/updates') {
      return Promise.resolve({status: updatesStatus, ok: updatesStatus === 200,
        json: () => Promise.resolve(updatesPayload), text: () => Promise.resolve('')});
    }
    if(target === '/updates/check') {
      return Promise.resolve({status: 200, ok: true,
        json: () => Promise.resolve(updatesPayload),
        text: () => Promise.resolve(JSON.stringify(updatesPayload))});
    }
    if(target === '/settings/save') {
      const body = options && options.body ? JSON.parse(options.body) : {};
      const reply = Object.assign({}, saveReply);
    if(typeof body.daily_chat_prefer_flash === 'boolean'){
      SETTINGS.daily_chat_prefer_flash = body.daily_chat_prefer_flash;
    }
      if(body.upstream && typeof body.upstream.device_token_file === 'string'){
        deviceTokenStatus = {
          source: body.upstream.device_token_file ? 'file' : 'none',
          configured_file: body.upstream.device_token_file,
          loaded: !!body.upstream.device_token_file,
          length: body.upstream.device_token_file ? 17 : 0,
          cached_at: 1760000001,
        };
        reply.upstream = {device_token_file: deviceTokenStatus.configured_file};
        reply.device_token_status = deviceTokenStatus;
      }
      if(typeof body.desktop_chat_compat === 'boolean'){
        reply.desktop_chat_compat = body.desktop_chat_compat;
      }
      return Promise.resolve({status: saveStatus, ok: saveStatus === 200,
        json: () => Promise.resolve(reply),
        text: () => Promise.resolve(JSON.stringify(reply))});
    }
    if(target === '/settings/device-token/reload') {
      return Promise.resolve({status: 200, ok: true,
        json: () => Promise.resolve({ok: true,
          device_token_status: deviceTokenReloadStatus}),
        text: () => Promise.resolve(JSON.stringify({ok: true,
          device_token_status: deviceTokenReloadStatus}))});
    }
    return Promise.resolve({status: 200, ok: true,
      json: () => Promise.resolve({}), text: () => Promise.resolve('{}')});
  },
});

const toasts = [];
let api;
const realLog = console.log;
console.log = () => {};
try {
  api = new Function(script + `
    toast = function(msg, kind){ global.__toasts.push([msg, kind]); };
    return {
      loadSettings: loadSettings,
      loadUpdateStatus: loadUpdateStatus,
      checkUpdateNow: checkUpdateNow,
      installUpdateNow: installUpdateNow,
      saveUpdateCheckEnabled: saveUpdateCheckEnabled,
      saveUpdateProxySlot: saveUpdateProxySlot,
      applyUpdateStatus: applyUpdateStatus,
      saveDailyChatModule: saveDailyChatModule,
      resetDailyChatModule: resetDailyChatModule,
      saveDeviceTokenFile: saveDeviceTokenFile,
      reloadDeviceToken: reloadDeviceToken,
      saveDesktopChatCompat: saveDesktopChatCompat,
    };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
global.__toasts = toasts;

const settled = () => new Promise(resolve => process.nextTick(resolve));
const urls = () => calls.map(c => c.method + ' ' + c.url);
const posts = () => calls.filter(c => c.method === 'POST');

(async () => {
  let checks = 0;
  const check = (label, cond, extra) => {
    checks += 1;
    assert.ok(cond, label + (extra !== undefined ? '  [' + extra + ']' : ''));
  };

  // ---- 0. 每日活跃总开关：设置还没读到就保存，不能把总开关写成关闭 ----
  document.getElementById('setDailyActivity').checked = false;   // 未回填的勾选框（首次取用即建元素）
  calls.length = 0;
  await api.saveDailyChatModule({disabled: false});
  await settled();
  {
    const early = posts().filter(c => c.url === '/settings/save');
    const earlyBody = early.length ? JSON.parse(early[0].body) : {};
    check('设置未加载时保存不带 daily_activity=false',
          early.length === 1 && earlyBody.daily_activity !== false, JSON.stringify(earlyBody));
  }
  toasts.length = 0;

  // ---- 1. 打开设置页：显示当前版本、最新版本与状态 ----
  updatesPayload = {
    current_version: '1.6.17', latest_version: '1.6.18', update_available: true,
    enabled: true, checking: false,
    last_attempt: '2026-10-09 12:00:00', last_success: '2026-10-09 12:00:00',
    last_error: '', release_url: 'https://github.com/ardeyouxipianyi/workbuddy2api-hub/releases/tag/v1.6.18',
    latest_tag: 'v1.6.18', install_enabled: true,
    installation: {state: 'idle', version: '', message: ''},
    published_at: '2026-10-09T00:00:00Z',
  };
  calls.length = 0;
  await api.loadSettings(true);
  await settled();

  check('/settings 缺 daily_activity 时总开关按缺省（开）勾上',
        element('setDailyActivity').checked === true);
  check('当前版本来自 /updates', element('setUpdateCurrent').textContent === '1.6.17',
        element('setUpdateCurrent').textContent);
  check('最新版本来自 /updates', element('setUpdateLatest').textContent === '1.6.18',
        element('setUpdateLatest').textContent);
  check('状态显示「发现新版本」', element('setUpdateState').textContent === '(发现新版本)',
        element('setUpdateState').textContent);
  check('上次检查时间被显示', element('setUpdateLastCheck').textContent === '2026-10-09 12:00:00',
        element('setUpdateLastCheck').textContent);
  check('自动检查开关跟随后端值', element('setUpdateCheckEnabled').checked === true);
  check('打开设置页只读 /updates，不会自己发起检查',
        urls().indexOf('GET /updates') !== -1 && posts().length === 0, urls().join(', '));

  // ---- 1b. 设备 Token 文件 / 脱敏状态 / 重载，以及桌面 chat 兼容开关 ----
  check('设备 token 文件路径按脱敏状态写回',
        element('setDeviceTokenFile').value === '/data/device-token',
        element('setDeviceTokenFile').value);
  check('设备 token 状态只显示长度并标明脱敏',
        element('setDeviceTokenState').textContent === '(已加载 · 48 字符 · 已脱敏)',
        element('setDeviceTokenState').textContent);
  check('桌面 chat 兼容默认关闭',
        element('setDesktopChatCompat').checked === false &&
        element('setDesktopChatCompatState').textContent === '(默认关闭)');

  calls.length = 0;
  toasts.length = 0;
  element('setDeviceTokenFile').value = '  /data/new-device-token  ';
  const tokenPathBtn = {disabled: false};
  await api.saveDeviceTokenFile(tokenPathBtn);
  await settled();
  const tokenSaves = posts().filter(c => c.url === '/settings/save');
  const tokenSaveBody = tokenSaves.length ? JSON.parse(tokenSaves[0].body) : {};
  check('设备 token 路径保存走 POST /settings/save',
        tokenSaves.length === 1, urls().join(', '));
  check('只提交 device_token_file，绝不上送 token 值',
        tokenSaveBody.upstream &&
        tokenSaveBody.upstream.device_token_file === '/data/new-device-token' &&
        !Object.prototype.hasOwnProperty.call(tokenSaveBody.upstream, 'device_token'),
        JSON.stringify(tokenSaveBody));
  check('保存后立即刷新脱敏状态',
        element('setDeviceTokenState').textContent === '(已加载 · 17 字符 · 已脱敏)',
        element('setDeviceTokenState').textContent);
  check('保存路径按钮结束后恢复可用', tokenPathBtn.disabled === false);

  calls.length = 0;
  toasts.length = 0;
  element('setDesktopChatCompat').checked = true;
  const compatBtn = {disabled: false};
  await api.saveDesktopChatCompat(compatBtn);
  await settled();
  const compatSaves = posts().filter(c => c.url === '/settings/save');
  const compatBody = compatSaves.length ? JSON.parse(compatSaves[0].body) : {};
  check('桌面 chat 兼容开关按布尔值保存',
        compatSaves.length === 1 && compatBody.desktop_chat_compat === true,
        JSON.stringify(compatBody));
  check('桌面 chat 兼容开关状态写回',
        element('setDesktopChatCompat').checked === true &&
        element('setDesktopChatCompatState').textContent === '(已启用)');

  calls.length = 0;
  toasts.length = 0;
  deviceTokenReloadStatus = {
    source: 'file',
    configured_file: '/data/new-device-token',
    loaded: true,
    length: 21,
    cached_at: 1760000002,
  };
  const reloadBtn = {disabled: false};
  await api.reloadDeviceToken(reloadBtn);
  await settled();
  check('重新加载走带面板鉴权的 settings POST 路由',
        posts().length === 1 &&
        posts()[0].url === '/settings/device-token/reload',
        urls().join(', '));
  check('重新加载后只刷新脱敏状态',
        element('setDeviceTokenState').textContent === '(已加载 · 21 字符 · 已脱敏)',
        element('setDeviceTokenState').textContent);
  check('重新加载按钮结束后恢复可用', reloadBtn.disabled === false);

  // ---- 2. 已是最新 / 检查失败两种状态 ----
  api.applyUpdateStatus({current_version: '1.6.18', latest_version: '1.6.18',
                         update_available: false, last_error: ''});
  check('同版本显示「已是最新」', element('setUpdateState').textContent === '(已是最新)',
        element('setUpdateState').textContent);
  api.applyUpdateStatus({current_version: '1.6.17', latest_version: null,
                         update_available: false, last_error: ''});
  check('没查过显示「尚未检查」', element('setUpdateState').textContent === '(尚未检查)',
        element('setUpdateState').textContent);
  api.applyUpdateStatus({current_version: '1.6.17', latest_version: null,
                         update_available: false, last_error: 'HTTP 403'});
  check('后端报错显示「检查失败」', element('setUpdateState').textContent === '(检查失败)',
        element('setUpdateState').textContent);
  check('失败原因原样显示给用户', element('setUpdateError').textContent === '上次检查失败：HTTP 403',
        element('setUpdateError').textContent);

  // ---- 3. 「立即检查更新」打到 POST /updates/check ----
  updatesPayload = {
    current_version: '1.6.17', latest_version: '1.6.19', update_available: true,
    enabled: false, last_error: '', last_attempt: '2026-10-09 13:00:00',
  };
  calls.length = 0;
  const btn = {disabled: false};
  await api.checkUpdateNow(btn);
  await settled();
  check('立即检查走 POST /updates/check', posts().length === 1 && posts()[0].url === '/updates/check',
        urls().join(', '));
  check('检查结果立刻显示出来', element('setUpdateLatest').textContent === '1.6.19',
        element('setUpdateLatest').textContent);
  check('检查结果会更新状态行', element('setUpdateState').textContent === '(发现新版本)',
        element('setUpdateState').textContent);
  check('按钮在检查结束后恢复可用', btn.disabled === false);
  check('自动检查关着也能手动检查（开关未被当作前置条件）',
        posts().filter(c => c.url === '/updates/check').length === 1, urls().join(', '));

  // ---- 4. 200 不等于成功：ok=false / last_error 非空都算检查失败 ----
  // 后端把「检查没跑起来」和「这一次查询失败」都放在 200 的响应体里回来。
  // 按钮必须报失败——以前它对着一次失败的检查说「已是最新版本」。
  toasts.length = 0;
  updatesPayload = {
    ok: true, current_version: '1.6.17', latest_version: null,
    update_available: false, enabled: true, last_error: 'HTTP 403',
    last_attempt: '2026-10-09 14:00:00',
  };
  const failBtn = {disabled: false};
  await api.checkUpdateNow(failBtn);
  await settled();
  check('200 + last_error 显示「检查失败」',
        element('setUpdateState').textContent === '(检查失败)',
        element('setUpdateState').textContent);
  check('200 + last_error 把原因写出来',
        element('setUpdateError').textContent === '上次检查失败：HTTP 403',
        element('setUpdateError').textContent);
  check('200 + last_error 不报「已是最新」',
        !toasts.some(([m]) => String(m).indexOf('已是最新') !== -1),
        JSON.stringify(toasts));
  check('200 + last_error 的提示是失败态',
        toasts.length === 1 && toasts[0][1] === 'bad' &&
        String(toasts[0][0]).indexOf('检查更新失败') === 0, JSON.stringify(toasts));
  check('失败之后按钮仍然恢复可用', failBtn.disabled === false);

  toasts.length = 0;
  updatesPayload = {ok: false, msg: '更新检查未运行'};
  await api.checkUpdateNow({disabled: false});
  await settled();
  check('200 + ok=false 显示「检查失败」',
        element('setUpdateState').textContent === '(检查失败)',
        element('setUpdateState').textContent);
  check('200 + ok=false 用后端的 msg 说明原因',
        element('setUpdateError').textContent === '上次检查失败：更新检查未运行',
        element('setUpdateError').textContent);
  check('200 + ok=false 不报「已是最新」',
        !toasts.some(([m]) => String(m).indexOf('已是最新') !== -1),
        JSON.stringify(toasts));
  check('200 + ok=false 的提示是失败态',
        toasts.length === 1 && toasts[0][1] === 'bad', JSON.stringify(toasts));

  toasts.length = 0;
  updatesPayload = {
    ok: true, current_version: '1.6.17', latest_version: '1.6.17',
    update_available: false, enabled: true, last_error: '',
  };
  await api.checkUpdateNow({disabled: false});
  await settled();
  check('真的没有新版本时才报「已是最新」',
        toasts.length === 1 && toasts[0][1] === 'ok' &&
        String(toasts[0][0]).indexOf('已是最新') !== -1, JSON.stringify(toasts));

  // ---- 5. 开关走既有 settings save 契约 ----
  calls.length = 0;
  element('setUpdateCheckEnabled').checked = true;
  const saveBtn = {disabled: false};
  await api.saveUpdateCheckEnabled(saveBtn);
  await settled();
  const save = posts().filter(c => c.url === '/settings/save');
  check('开关保存走 POST /settings/save', save.length === 1, urls().join(', '));
  check('提交的是 update_check_enabled 布尔值',
        save.length === 1 && JSON.parse(save[0].body).update_check_enabled === true,
        save.length ? save[0].body : '(no save)');
  check('没有走浏览器本地存储（偏好由服务端保存）',
        !calls.some(c => c.url.indexOf('localStorage') !== -1), urls().join(', '));

  // ---- 5b. 更新出口：直连 / 系统代理 / 代理槽三种模式 ----
  SETTINGS.update_check_proxy_slots = [
    {id: 'slot-1', label: 'Tokyo', enabled: true},
  ];
  SETTINGS.update_check_proxy_slot = '__system__';
  calls.length = 0;
  await api.loadSettings(true);
  await settled();
  const updateProxy = element('setUpdateProxySlot');
  check('更新出口下拉包含系统代理选项',
        updateProxy.innerHTML.includes('value="__system__"'),
        updateProxy.innerHTML);
  check('已保存的系统代理模式能正确回填',
        updateProxy.value === '__system__', updateProxy.value);
  toasts.length = 0;
  await api.saveUpdateProxySlot({disabled: false});
  await settled();
  const proxySave = posts().filter(c => c.url === '/settings/save');
  check('系统代理模式提交 __system__',
        proxySave.length === 1 &&
        JSON.parse(proxySave[0].body).update_check_proxy_slot === '__system__',
        proxySave.length ? proxySave[0].body : '(no save)');
  check('系统代理模式给出对应提示',
        toasts.length === 1 && String(toasts[0][0]).indexOf('跟随系统代理') !== -1,
        JSON.stringify(toasts));

  // ---- 6. 安装入口受更新状态与人工确认控制 ----
  const installBtn = element('setUpdateInstallBtn');
  updatesPayload = {
    current_version: '1.6.17', latest_version: '1.6.18', update_available: true,
    latest_tag: 'v1.6.18', release_url: 'https://github.com/ardeyouxipianyi/workbuddy2api-hub/releases/tag/v1.6.18',
    install_enabled: true, last_error: '', installation: {state: 'idle'},
  };
  api.applyUpdateStatus(updatesPayload);
  check('有可用稳定更新且更新服务已安装时按钮可用', installBtn.disabled === false);
  api.applyUpdateStatus({...updatesPayload, install_enabled: false});
  check('没有安装服务时按钮保持禁用', installBtn.disabled === true);
  api.applyUpdateStatus({...updatesPayload, installation: {state: 'applying'}});
  check('安装过程中按钮保持禁用', installBtn.disabled === true);
  api.applyUpdateStatus(updatesPayload);
  updatesPayload = {
    current_version: '1.6.18', latest_version: '1.6.18', update_available: false,
    install_enabled: true, last_error: '', installation: {state: 'idle'},
  };
  api.applyUpdateStatus(updatesPayload);
  check('没有新版本时安装按钮禁用', installBtn.disabled === true);
  updatesPayload = {
    current_version: '1.6.17', latest_version: '1.6.18', update_available: true,
    latest_tag: 'v1.6.18', release_url: 'https://github.com/ardeyouxipianyi/workbuddy2api-hub/releases/tag/v1.6.18',
    install_enabled: true, last_error: '', installation: {state: 'idle'},
  };
  api.applyUpdateStatus(updatesPayload);
  calls.length = 0;
  const confirmsBeforeInstall = confirmCalls;
  await api.installUpdateNow(installBtn); // DOM stub declines the confirmation.
  await settled();
  check('安装点击先经过人工确认', confirmCalls === confirmsBeforeInstall + 1);
  check('取消确认不会提交安装请求',
        posts().filter(c => c.url === '/updates/install').length === 0, urls().join(', '));
  check('面板从不直接访问 GitHub',
        !calls.some(c => /github\.com|api\.github/i.test(c.url)), urls().join(', '));
  check('面板只访问自己网关的相对路径',
        calls.every(c => c.url.charAt(0) === '/'), urls().join(', '));

  // ---- 7. 读取失败不拖垮设置页的其它区块 ----
  updatesStatus = 500;
  calls.length = 0;
  await api.loadSettings(true);
  await settled();
  check('读不到更新状态时只标注这一块', element('setUpdateState').textContent === '(读取失败)',
        element('setUpdateState').textContent);
  check('其它区块照常填上', element('setVersion').textContent === '1.6.17',
        element('setVersion').textContent);
  check('没有报「读取设置失败」',
        toasts.filter(([m]) => String(m).indexOf('读取设置失败') === 0).length === 0,
        JSON.stringify(toasts));
  updatesStatus = 200;

  // ---- 8. 每日活跃模块：一次提交预算 / 候选序列 / 时段 ----
  calls.length = 0;
  toasts.length = 0;
  element('setDailyActivity').checked = false;
  element('setDailyChatWeb').checked = true;
  element('setDailyChatBudget').value = '900';
  element('setDailyChatTurnTimeout').value = '45';
  element('setDailyChatWebConcurrency').value = '2';
  element('setDailyChatWebRetryBackoff').value = '7';
  element('setDailyChatStartupCatchup').checked = false;
  element('setDailyChatActivityFallback').checked = true;
  await api.saveDailyChatModule({disabled: false});
  await settled();
  let moduleSave = posts().filter(c => c.url === '/settings/save');
  check('模块保存只发一次 POST /settings/save', moduleSave.length === 1, urls().join(', '));
  let body = moduleSave.length ? JSON.parse(moduleSave[0].body) : {};
  check('提交单账号预算为整数秒', body.daily_chat_budget === 900, JSON.stringify(body));
  check('提交单轮最长为整数秒', body.daily_chat_web_timeout === 45, JSON.stringify(body));
  check('提交并发数与建会话退避', body.daily_chat_web_concurrency === 2 &&
        body.daily_chat_web_retry_backoff === 7, JSON.stringify(body));
  check('提交启动补跑开关', body.daily_chat_startup_catchup === false, JSON.stringify(body));
  check('提交每日活跃总开关', body.daily_activity === false, JSON.stringify(body));
  check('提交证书失败改走活跃上报开关',
        body.daily_chat_web_fallback_activity === true, JSON.stringify(body));
  check('设置页模块保存不再提交优先 Flash 开关',
        !('daily_chat_prefer_flash' in body), JSON.stringify(body));
  check('候选序列与时段随模块一起提交',
        Array.isArray(body.daily_chat_models) && body.schedule &&
        Array.isArray(body.schedule.daily_chat), JSON.stringify(body));
  check('保存成功给出成功提示',
        toasts.length === 1 && toasts[0][1] === 'ok', JSON.stringify(toasts));

  // 恢复推荐值：把四个数值与开关写回默认，并提交一次。
  calls.length = 0;
  toasts.length = 0;
  await api.resetDailyChatModule({disabled: false});
  await settled();
  let resetSave = posts().filter(c => c.url === '/settings/save');
  check('恢复推荐值走 POST /settings/save', resetSave.length === 1, urls().join(', '));
  let resetBody = resetSave.length ? JSON.parse(resetSave[0].body) : {};
  check('推荐值 = 预算 600 / 单轮 120 / 并发 1 / 退避 5',
        resetBody.daily_chat_budget === 600 && resetBody.daily_chat_web_timeout === 120 &&
        resetBody.daily_chat_web_concurrency === 1 &&
        resetBody.daily_chat_web_retry_backoff === 5, JSON.stringify(resetBody));
  check('推荐值清空候选序列与时段（回到随机 + 自动）',
        Array.isArray(resetBody.daily_chat_models) && resetBody.daily_chat_models.length === 0 &&
        resetBody.schedule && resetBody.schedule.daily_chat.length === 0,
        JSON.stringify(resetBody));
  check('推荐值关闭证书失败改走活跃上报',
        resetBody.daily_chat_web_fallback_activity === false, JSON.stringify(resetBody));
  check('设置页恢复推荐值不再改动优先 Flash 开关',
        !('daily_chat_prefer_flash' in resetBody), JSON.stringify(resetBody));
  check('恢复推荐值后输入框写回 600', element('setDailyChatBudget').value === '600');
  check('恢复推荐值后回退开关被取消勾选',
        element('setDailyChatActivityFallback').checked === false);

  // 存失败时给出失败提示，不静默。
  saveStatus = 500;
  calls.length = 0;
  toasts.length = 0;
  await api.saveDailyChatModule({disabled: false});
  await settled();
  saveStatus = 200;
  check('模块保存失败给出失败提示',
        toasts.length === 1 && toasts[0][1] === 'bad', JSON.stringify(toasts));

  console.log = realLog;
  realLog('settings update UI assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.log = realLog;
  console.error(e && e.stack || e);
  process.exit(1);
});
