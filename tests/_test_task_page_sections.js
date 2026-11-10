/* 自动化页的四段结构与三个手动入口。
 *
 * 这页回答的是「哪一类自动化现在能手动跑」，而不是把签到、成长任务和后台
 * 调度器混成一大块：
 *
 *   1. 后台调度器；
 *   2. 国际版每日签到（官方叫每日活跃福利）；
 *   3. 国内版每日签到（Buddy 加油站）；
 *   4. 国内版任务（成长任务、猫猫旅行、连登管家与抽奖）。
 *
 * 结构断言盯住每段各自的按钮，行为断言盯住「一键执行国内任务」在单项失败时
 * 仍继续跑后面的步骤，并把失败列进最终提示。Run with Node:
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

const intl = section('sectionIntlTasks');
const cnCheckin = section('sectionCnCheckin');
const cnTasks = section('sectionGrowthTasks');

check('国际版区块标题是「国际版每日签到」',
  /<h2 style="margin:0">国际版每日签到<\/h2>/.test(intl));
check('国际版区块有独立的一键签到按钮',
  /<button class="primary" id="btnPageDailyChat" onclick="doDailyChat\(this\)">一键国际版签到<\/button>/.test(intl));
check('国际版区块保留未活跃账号补跑入口',
  /id="btnPageDailyChatPending"[^>]*onclick="doDailyChatPending\(this\)"/.test(intl));

check('国内签到区块标题是「国内版每日签到」',
  /<h2 style="margin:0">国内版每日签到<\/h2>/.test(cnCheckin));
check('国内签到区块有 Buddy 加油站独立入口',
  /<button class="primary" id="btnPageCheckin" onclick="triggerBuddyCheckin\(this\)">一键国内版签到<\/button>/.test(cnCheckin));
check('国内签到状态与福利总积分留在签到区块',
  cnCheckin.includes('id="cnTodayCheckinState"') && cnCheckin.includes('id="cnTotalGranted"'));
check('国内签到区块有自己的国内账号选择器',
  /id="cnCheckinAcctSelect"[^>]*onchange="switchGrowthAccount\(this.value\)"/.test(cnCheckin));

check('国内任务区块标题是「国内版任务」',
  /<h2 style="margin:0">国内版任务<\/h2>/.test(cnTasks));
check('国内任务区块有串行总执行按钮',
  /<button class="primary" id="btnRunAllCnTasks" onclick="runAllCnTasks\(this\)">一键执行国内任务<\/button>/.test(cnTasks));
check('国内任务区块保留成长任务单项入口',
  /<button class="sec mini" id="btnRunTasks" onclick="runGrowthTasks\(this\)">/.test(cnTasks));
check('国内任务区块保留猫猫旅行单项入口',
  /<button id="btnCatTravel" onclick="triggerCatTravel\(this\)">猫猫旅行<\/button>/.test(cnTasks));
check('国内任务区块保留连登管家单项入口',
  /<button id="btnRunStreakBonus" onclick="triggerStreakBonus\(this\)">连登管家与抽奖<\/button>/.test(cnTasks));
check('签到卡片已从国内任务区块移走',
  !cnTasks.includes('id="cnTodayCheckinState"') && !cnTasks.includes('id="cnTotalGranted"'));

check('两个国内账号下拉框双向同步',
  /\["growthAcctSelect", "cnCheckinAcctSelect"\]\.forEach/.test(script) &&
  /if\(el && el\.value !== uid\) el\.value = uid;/.test(script));
check('调度器保留独立的立即巡检按钮',
  /id="btnTriggerSched" onclick="triggerSchedulerNow\(this\)"/.test(html));

/* ---- 总执行：某一步返回 false 时仍继续，并正确汇总 -------------------- */
const start = script.indexOf('async function runAllCnTasks');
const end = script.indexOf('\nfunction saveGrowthAutoClaim', start);
assert.ok(start >= 0 && end > start, '找不到 runAllCnTasks，测试需要更新');

const calls = [];
const toasts = [];
const runGrowthTasks = async () => { calls.push('growth'); return false; };
const triggerCatTravel = async () => { calls.push('cat'); return true; };
const triggerStreakBonus = async () => { calls.push('streak'); return false; };
const toast = (msg, kind) => { toasts.push({msg: msg, kind: kind}); };
const elements = {
  btnRunTasks: {disabled: false},
  btnCatTravel: {disabled: false},
  btnRunStreakBonus: {disabled: false},
};
const document = {getElementById: id => elements[id] || null};

const factory = new Function(
  'document', 'toast', 'runGrowthTasks', 'triggerCatTravel', 'triggerStreakBonus',
  script.slice(start, end) + '\nreturn runAllCnTasks;'
);
const runAll = factory(document, toast, runGrowthTasks, triggerCatTravel, triggerStreakBonus);

(async () => {
  await runAll({disabled: false, textContent: '一键执行国内任务'});
  check('失败步骤不会中断后续步骤',
    JSON.stringify(calls) === JSON.stringify(['growth', 'cat', 'streak']), JSON.stringify(calls));
  check('成功与失败分别汇总',
    toasts.length === 1 && toasts[0].kind === 'warn' &&
    /成长任务：未完成/.test(toasts[0].msg) &&
    /猫猫旅行：完成/.test(toasts[0].msg) &&
    /连登管家与抽奖：未完成/.test(toasts[0].msg),
    JSON.stringify(toasts));

  console.log('task page section assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.error(e && e.stack || e);
  process.exit(1);
});
