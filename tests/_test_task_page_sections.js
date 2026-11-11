/* 自动化页的结构与手动入口。
 *
 * 页面回答「哪一类自动化现在能手动跑」：
 *
 *   1. 后台调度器（保留独立区块）；
 *   2. 一次性 / 活动福利（成长任务、活动任务、小程序任务）；
 *   3. 每日任务（国际版签到；国内版 Buddy 签到、猫猫旅行、连登、夜猫子）。
 *
 * 行为断言盯住「一键执行每日任务」在单项失败时仍继续跑后面的步骤，并且
 * 导入账号后的每日任务入口不跑国际版签到。Run with Node:
 *   node tests/_test_task_page_sections.js
 */
'use strict';

const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');

const html = dashboardHtml();
const script = dashboardScript();
let checks = 0;

function check(label, ok, detail){
  assert.ok(ok, label + (detail === undefined ? '' : '  [' + detail + ']'));
  checks += 1;
}

function section(id){
  const start = html.indexOf('<section id="' + id + '"');
  assert.ok(start >= 0, '缺少区块 #' + id);
  const end = html.indexOf('</section>', start);
  assert.ok(end > start, '区块 #' + id + ' 没有闭合');
  return html.slice(start, end + '</section>'.length);
}

const daily = section('sectionDailyTasks');
const growth = section('sectionGrowthTasks');

check('每日任务区块标题是「每日任务」',
  /<h2 style="margin:0">每日任务<\/h2>/.test(daily));
check('每日任务区块有总入口',
  /<button class="primary" id="btnRunAllDailyTasks" onclick="runAllDailyTasks\(this\)">一键执行每日任务<\/button>/.test(daily));
check('国际版签到仍在每日任务区块',
  /<button class="primary" id="btnPageDailyChat" onclick="doDailyChat\(this\)">一键国际版签到<\/button>/.test(daily) &&
  /id="btnPageDailyChatPending"[^>]*onclick="doDailyChatPending\(this\)"/.test(daily));
check('优先 Flash 开关位于国际版每日签到区块',
  /id="taskDailyChatPreferFlash"[^>]*checked[^>]*onchange="saveDailyChatPreferFlash\(this\)"/.test(daily));
check('设置页不再重复显示优先 Flash 开关',
  !html.includes('id="setDailyChatPreferFlash"'));
check('国内版签到仍在每日任务区块',
  /<button class="primary" id="btnPageCheckin" onclick="triggerBuddyCheckin\(this\)">一键国内版签到<\/button>/.test(daily));
check('猫猫旅行是每日任务单项入口',
  /<button id="btnCatTravel" onclick="triggerCatTravel\(this\)">猫猫旅行<\/button>/.test(daily));
check('连登管家是每日任务单项入口',
  /<button id="btnRunStreakBonus" onclick="triggerStreakBonus\(this\)">连登管家与抽奖<\/button>/.test(daily));
check('夜猫子是每日任务单项入口',
  /<button id="btnRunNightTask" onclick="triggerNightTask\(this\)">夜猫子任务<\/button>/.test(daily));
check('每日任务区块保留国际版与国内版状态卡',
  daily.includes('id="intlTodayActiveState"') &&
  daily.includes('id="cnTodayCheckinState"') &&
  daily.includes('id="stkDays"') &&
  daily.includes('id="catState"'));

check('一次性区块标题是「一次性 / 活动福利」',
  /<h2 style="margin:0">一次性 \/ 活动福利<\/h2>/.test(growth));
check('一次性区块只有一个总入口',
  /<button class="primary" id="btnRunTasks" onclick="runGrowthTasks\(this\)">一键完成一次性任务<\/button>/.test(growth));
check('一次性区块不包含每日单项按钮',
  !growth.includes('id="btnPageCheckin"') &&
  !growth.includes('id="btnCatTravel"') &&
  !growth.includes('id="btnRunStreakBonus"') &&
  !growth.includes('id="btnRunNightTask"'));
check('一次性区块保留任务表与日志',
  growth.includes('id="growthTable"') && growth.includes('id="taskLogBox"'));

check('任务表过滤每日夜猫子',
  /filter\(t => String\(t\.task_code \|\| ''\) !== 'black_cat'\)/.test(script));
check('两个国内账号下拉框双向同步',
  /\["growthAcctSelect", "cnCheckinAcctSelect"\]\.forEach/.test(script) &&
  /if\(el && el\.value !== uid\) el\.value = uid;/.test(script));
check('调度器保留独立的立即巡检按钮',
  /id="btnTriggerSched" onclick="triggerSchedulerNow\(this\)"/.test(html));

/* ---- 任务页开关：立即保存，失败时回滚界面 ------------------------------- */
const flashStart = script.indexOf('function applyDailyChatPreferFlash');
const flashEnd = script.indexOf('\nasync function loadGrowthTasks', flashStart);
assert.ok(flashStart >= 0 && flashEnd > flashStart,
  '找不到优先 Flash 开关逻辑，测试需要更新');

const flashElements = {
  taskDailyChatPreferFlash: {checked: true, disabled: false},
  taskDailyChatPreferFlashState: {textContent: '', style: {}},
};
const flashDocument = {getElementById: id => flashElements[id] || null};
const flashWindow = {CURRENT_SETTINGS: {daily_chat_prefer_flash: true}};
let flashPosts = [];
let flashToasts = [];
let flashFail = false;
const flashPostJSON = async (url, body) => {
  flashPosts.push({url: url, body: body});
  if(flashFail) throw new Error('network down');
  return {ok: true};
};
const flashToast = (msg, kind) => { flashToasts.push({msg: msg, kind: kind}); };
const flashFactory = new Function(
  'document', 'window', 'postJSON', 'toast',
  script.slice(flashStart, flashEnd) +
  '\nreturn {applyDailyChatPreferFlash, saveDailyChatPreferFlash};'
);
const flashApi = flashFactory(flashDocument, flashWindow, flashPostJSON, flashToast);

/* ---- 每日总执行：某一步返回 false 时仍继续，并正确汇总 ------------------ */
const start = script.indexOf('async function runAllDailyTasks');
const end = script.indexOf('\nfunction saveGrowthAutoClaim', start);
assert.ok(start >= 0 && end > start, '找不到 runAllDailyTasks，测试需要更新');

const calls = [];
const toasts = [];
const doDailyChatPending = async () => { calls.push('intl'); return false; };
const triggerBuddyCheckin = async (btn, uid) => { calls.push('checkin:' + uid); return true; };
const triggerCatTravel = async (btn, uid) => { calls.push('cat:' + uid); return true; };
const triggerStreakBonus = async (btn, uid) => { calls.push('streak:' + uid); return false; };
const triggerNightTask = async (btn, uid) => { calls.push('night:' + uid); return true; };
const toast = (msg, kind) => { toasts.push({msg: msg, kind: kind}); };
const elements = {
  btnPageDailyChatPending: {disabled: false},
  btnPageCheckin: {disabled: false},
  btnCatTravel: {disabled: false},
  btnRunStreakBonus: {disabled: false},
  btnRunNightTask: {disabled: false},
};
const document = {getElementById: id => elements[id] || null};

const factory = new Function(
  'document', 'toast', 'doDailyChatPending', 'triggerBuddyCheckin',
  'triggerCatTravel', 'triggerStreakBonus', 'triggerNightTask',
  script.slice(start, end) + '\nreturn runAllDailyTasks;'
);
const runAll = factory(document, toast, doDailyChatPending,
  triggerBuddyCheckin, triggerCatTravel, triggerStreakBonus, triggerNightTask);

(async () => {
  flashApi.applyDailyChatPreferFlash(false);
  check('任务页开关按后端值回填',
    flashElements.taskDailyChatPreferFlash.checked === false &&
    /关闭/.test(flashElements.taskDailyChatPreferFlashState.textContent));
  flashElements.taskDailyChatPreferFlash.checked = false;
  await flashApi.saveDailyChatPreferFlash(flashElements.taskDailyChatPreferFlash);
  check('任务页开关立即保存到 /settings/save',
    flashPosts.length === 1 &&
    flashPosts[0].url === '/settings/save' &&
    flashPosts[0].body.daily_chat_prefer_flash === false,
    JSON.stringify(flashPosts));
  check('保存成功后同步本地设置状态',
    flashWindow.CURRENT_SETTINGS.daily_chat_prefer_flash === false &&
    flashToasts.length === 1 && flashToasts[0].kind === 'ok');

  flashPosts = [];
  flashToasts = [];
  flashFail = true;
  flashElements.taskDailyChatPreferFlash.checked = true;
  await flashApi.saveDailyChatPreferFlash(flashElements.taskDailyChatPreferFlash);
  check('保存失败时开关回滚并提示失败',
    flashElements.taskDailyChatPreferFlash.checked === false &&
    flashToasts.length === 1 && flashToasts[0].kind === 'bad');

  await runAll({disabled: false, textContent: '一键执行每日任务'}, ['uid-a']);
  check('失败步骤不会中断后续步骤',
    JSON.stringify(calls) === JSON.stringify([
      'intl', 'checkin:uid-a', 'cat:uid-a', 'streak:uid-a', 'night:uid-a'
    ]), JSON.stringify(calls));
  check('成功与失败分别汇总',
    toasts.length === 1 && toasts[0].kind === 'warn' &&
    /国际版签到：未完成/.test(toasts[0].msg) &&
    /国内版签到：完成/.test(toasts[0].msg) &&
    /连登管家与抽奖：未完成/.test(toasts[0].msg),
    JSON.stringify(toasts));

  calls.length = 0;
  toasts.length = 0;
  await runAll({disabled: false, textContent: '一键执行每日任务'}, ['uid-a'],
    {skipIntl: true});
  check('导入后每日任务不跑国际版签到',
    !calls.includes('intl'), JSON.stringify(calls));
  check('导入后仍执行全部国内每日步骤',
    JSON.stringify(calls) === JSON.stringify([
      'checkin:uid-a', 'cat:uid-a', 'streak:uid-a', 'night:uid-a'
    ]), JSON.stringify(calls));

  console.log('task page section assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.error(e && e.stack || e);
  process.exit(1);
});
