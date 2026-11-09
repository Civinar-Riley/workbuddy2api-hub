/* 总览首屏的启动顺序：核心数据先画，非首屏请求不能把首屏押后。
 *
 * 回归背景（issue #832）：startDashboard() 原先串行等待 /settings，再等待
 * /realm，随后又用 Promise.all 等模型目录、模型勾选、调度器和智能体可用性。
 * 其中设置页的额度查询目录还会再请求一次 /v1/models?realm=all；这些接口
 * 对总览首屏都不是必需的，却能把 KPI 和账号区一起拖住。
 *
 * 这里只钉启动链的结构，不重复 DOM 渲染测试：
 *   1. 启动时 loadSettings 必须带 deferSecondary，设置页自己的刷新不能带；
 *   2. 核心的 loadAccounts + refresh 必须先完成；
 *   3. 模型 / 调度器 / 智能体可用性放在后台 Promise 里，不参与首屏 await；
 *   4. 启动时 loadAccounts 必须带 deferActivity，活动记录回来后再重绘。
 *
 * Run with Node: node tests/_test_overview_boot_latency.js
 */
'use strict';
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();

function functionBody(name){
  const start = script.indexOf('async function ' + name + '(');
  assert.ok(start >= 0, '找不到 async function ' + name);
  const next = script.indexOf('\nasync function ', start + 1);
  const end = next < 0 ? script.length : next;
  return script.slice(start, end);
}

const boot = functionBody('startDashboard');

assert.ok(/loadSettings\(false, \{deferSecondary: true\}\)/.test(boot),
  '启动时必须延迟设置页的额度查询目录');
assert.ok(/loadAccounts\(false, \{deferActivity: true\}\)/.test(boot),
  '启动时账号区应先画核心数据，活动记录后台补');

const settingsAt = boot.indexOf('loadSettings(false, {deferSecondary: true})');
const initRealmAt = boot.indexOf('await initRealm()');
const coreAt = boot.indexOf('await Promise.all([\n    loadAccounts(false, {deferActivity: true}),\n    refresh(),\n  ])');
const secondaryAt = boot.indexOf('const secondary = Promise.all([');
assert.ok(settingsAt >= 0 && initRealmAt > settingsAt,
  '设置读取后仍需先确定 realm');
assert.ok(secondaryAt > initRealmAt, '非首屏请求应在 realm 确定后再启动');
assert.ok(coreAt > secondaryAt, '核心首屏数据必须单独等待，不能混进 secondary');

for(const name of ['loadModels()', 'loadAgentModelSelection()',
                   'loadSchedulerStatus()', 'loadAgentsAvailability()']){
  assert.ok(boot.slice(secondaryAt, coreAt).includes(name),
    '非首屏请求应放进 secondary：' + name);
}

const settingsFn = functionBody('loadSettings');
assert.ok(/if\(opts && opts\.deferSecondary\)\{[\s\S]*?loadQuotaScanCatalog\(\)\.then\(renderQuotaScanModels\)/.test(settingsFn),
  'loadSettings 的 deferSecondary 必须把额度目录改成后台加载');
assert.ok(/else \{\s*await loadQuotaScanCatalog\(\);/.test(settingsFn),
  '设置页普通刷新仍必须等待额度目录');

const accountsFn = functionBody('loadAccounts');
assert.ok(/const activityPromise = loadActivityToday\(\);/.test(accountsFn),
  '活动记录应和账号请求并行发起');
assert.ok(/if\(!\(opts && opts\.deferActivity\)\) await activityPromise;/.test(accountsFn),
  '默认调用方仍要等待活动记录，保持既有完整性语义');
assert.ok(/activityPromise\.then\(\(\) => \{[\s\S]*?renderAccounts\(true\);/.test(accountsFn),
  '后台活动记录返回后必须补画账号表');

console.log('overview boot latency assertions passed');
