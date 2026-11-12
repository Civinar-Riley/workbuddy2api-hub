/* 刷新凭证：账号工具栏的批量按钮，等价于对每个账号点一次「刷新凭证」。
 *
 * 后端 /accounts/refresh 支持不带 uid 时刷新整池、带 uids 时只刷点名的账号。
 * 看板这一侧的规则是「有勾选只刷勾选的，没有勾选刷当前筛选结果」，作用范围
 * 由 acctScopeUids() 统一给出（刷新积分 / 测试 / 启停 / 切身分共用）。这里把
 * dashboard.html 里的 refreshAccounts 原样抽出来跑一遍，钉住：提交 uids 点名、
 * 按成功数回报、失败时把错误和条数一并说出来、按钮跑动期间禁用并复位、空范围
 * 时不发请求，以及按钮和英文词条确实在页面上。
 *
 * Run with Node: node tests/_test_refresh_all_credentials.js
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');

const start = html.indexOf('async function refreshAccounts');
const end = html.indexOf('async function setAll');
assert.ok(start > 0 && end > start,
  'dashboard.html 里找不到 refreshAccounts，测试需要更新');

const source = html.slice(start, end);

function makeHarness(response, scope) {
  const calls = { posts: [], toasts: [], loads: 0 };
  const postJSON = async (url, body) => {
    calls.posts.push({ url, body });
    if (response instanceof Error) throw response;
    return response;
  };
  const toast = (msg, kind) => calls.toasts.push({ msg, kind });
  const loadAccounts = async () => { calls.loads++; };
  const acctScopeUids = () => scope || { uids: ['a', 'b'], selected: false };
  const factory = new Function('postJSON', 'toast', 'loadAccounts', 'acctScopeUids',
    source + '\nreturn refreshAccounts;');
  return { fn: factory(postJSON, toast, loadAccounts, acctScopeUids), calls };
}

function makeButton() {
  return { disabled: false, textContent: '刷新凭证' };
}

(async () => {
  // 1) 批量刷新只发一次请求，并把作用范围的 uid 点名列出来
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: true }, { uid: 'b', ok: true }] });
    const btn = makeButton();
    await h.fn(btn);
    assert.strictEqual(h.calls.posts.length, 1, '批量刷新应只发一次请求');
    assert.strictEqual(h.calls.posts[0].url, '/accounts/refresh');
    assert.deepStrictEqual(h.calls.posts[0].body, { uids: ['a', 'b'] },
      '应把作用范围的 uid 点名交给后端，空 uids 会被后端当作「什么都不刷」');
  }

  // 1b) 勾选时作用范围只有勾选的号
  {
    const h = makeHarness({ results: [{ uid: 'x', ok: true }] },
                          { uids: ['x'], selected: true });
    await h.fn(makeButton());
    assert.deepStrictEqual(h.calls.posts[0].body, { uids: ['x'] },
      '勾选后只应刷新勾选的账号');
  }

  // 1c) 作用范围为空时不发请求，只提示
  {
    const h = makeHarness({ results: [] }, { uids: [], selected: false });
    const btn = makeButton();
    await h.fn(btn);
    assert.strictEqual(h.calls.posts.length, 0, '空范围不应发请求');
    assert.strictEqual(h.calls.toasts.length, 1);
    assert.strictEqual(h.calls.toasts[0].msg, '当前没有可刷新的账号');
    assert.strictEqual(h.calls.toasts[0].kind, 'warn');
    assert.strictEqual(btn.disabled, false, '空范围返回后按钮不能停在禁用态');
    assert.strictEqual(btn.textContent, '刷新凭证');
  }

  // 2) 全部成功：报「刷新完成: 2/2」，用 ok 配色
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: true }, { uid: 'b', ok: true }] });
    await h.fn(makeButton());
    assert.strictEqual(h.calls.toasts.length, 1, '全成功不该再弹第二条');
    assert.strictEqual(h.calls.toasts[0].msg, '刷新完成: 2/2');
    assert.strictEqual(h.calls.toasts[0].kind, 'ok');
  }

  // 3) 部分失败：第一条报完成数并降级成 warn，第二条带出错误与失败条数
  {
    const h = makeHarness({ results: [
      { uid: 'a', ok: true },
      { uid: 'b', ok: false, error: 'HTTP 401' },
      { uid: 'c', ok: false, error: 'HTTP 401' },
    ] });
    await h.fn(makeButton());
    assert.strictEqual(h.calls.toasts[0].msg, '刷新完成: 1/3');
    assert.strictEqual(h.calls.toasts[0].kind, 'warn', '有失败时完成提示不能再报绿');
    assert.strictEqual(h.calls.toasts[1].msg, '刷新失败: HTTP 401 (2)',
      '失败提示要带上原因和条数');
    assert.strictEqual(h.calls.toasts[1].kind, 'bad');
  }

  // 4) 后端没给 error 时兜底成「未知错误」，不能出现 undefined
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: false }] });
    await h.fn(makeButton());
    assert.strictEqual(h.calls.toasts[1].msg, '刷新失败: 未知错误 (1)');
  }

  // 5) 请求本身失败（网络 / 面板会话过期）：走 catch，报 bad，不留假成功
  {
    const h = makeHarness(new Error('unauthorized'));
    await h.fn(makeButton());
    assert.strictEqual(h.calls.toasts.length, 1);
    assert.strictEqual(h.calls.toasts[0].msg, '刷新失败: unauthorized');
    assert.strictEqual(h.calls.toasts[0].kind, 'bad');
  }

  // 6) 无论成败都要重画账号卡片，否则刷新后的新凭证不在面板上体现
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: true }] });
    await h.fn(makeButton());
    assert.strictEqual(h.calls.loads, 1, '刷新后必须重载账号列表');
  }

  // 7) 按钮在飞行期间禁用并显示进度，结束后复位
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: true }] });
    const btn = makeButton();
    const pending = h.fn(btn);
    assert.strictEqual(btn.disabled, true, '请求在飞时应禁用按钮，防连点');
    assert.strictEqual(btn.textContent, '刷新中...');
    await pending;
    assert.strictEqual(btn.disabled, false);
    assert.strictEqual(btn.textContent, '刷新凭证');
  }

  // 8) 没传按钮也不能炸（按钮是可选的）
  {
    const h = makeHarness({ results: [{ uid: 'a', ok: true }] });
    await h.fn();
    assert.strictEqual(h.calls.toasts[0].msg, '刷新完成: 1/1');
  }

  // 9) 工具栏里真的挂了这个按钮，并且指向 refreshAccounts
  {
    const bar = html.slice(html.indexOf('<div class="toolbar">'),
                           html.indexOf('<div id="accounts">'));
    assert.ok(/onclick="refreshAccounts\(this\)"/.test(bar),
      '账号工具栏里找不到刷新凭证按钮');
    assert.ok(/>刷新凭证</.test(bar), '按钮文案应为「刷新凭证」');
  }

  // 10) 英文词条齐全，切到 EN 时按钮不会是中文
  {
    assert.ok(/'刷新凭证':\s*'Refresh token'/.test(html),
      'DICT 里缺少「刷新凭证」的英文词条');
  }

  console.log('ok - refresh all credentials (11 checks)');
})();
