// 原型三层验证脚本：逐页冒烟 + 黄金流链路 + 布局遮挡检测（可复跑回归）
const { chromium } = require('playwright');
const path = require('path');

const URL = 'file://' + encodeURIComponent(path.resolve(__dirname, 'index.html')).replace(/%2F/g, '/');
const errors = [];
const results = [];

function check(name, cond, detail = '') {
  results.push({ name, pass: !!cond, detail });
  if (!cond) errors.push(`FAIL: ${name} ${detail}`);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  });

  // ── 第二层：逐页冒烟（390 移动 viewport + 360 小屏各一遍）──
  for (const vp of [{ width: 390, height: 844 }, { width: 360, height: 800 }]) {
    const page = await browser.newPage({ viewport: vp });
    const jsErrors = [];
    page.on('pageerror', e => jsErrors.push(String(e)));
    page.on('console', m => { if (m.type() === 'error') jsErrors.push(m.text()); });
    await page.goto(URL);
    await page.waitForTimeout(400);

    // 登录
    await page.click('#btn-login');
    await page.waitForTimeout(1200);
    check(`[${vp.width}] 登录后进入 chat`, await page.isVisible('#pg-chat.active'));

    // 每个 Root Tab 通过底部导航进入（点击当前激活页内的导航项）
    const tabOrder = ['chat', 'history', 'plan', 'discover', 'mine'];
    for (const tab of tabOrder) {
      await page.click(`.page.active .root-nav .nav-item:nth-child(${tabOrder.indexOf(tab) + 1})`);
      await page.waitForTimeout(250);
      const active = await page.$(`#pg-${tab}.active`);
      const text = active ? (await active.innerText()).length : 0;
      check(`[${vp.width}] Tab ${tab} 渲染且非空`, active && text > 60, `len=${text}`);
      const navVisible = await page.isVisible(`#pg-${tab} .root-nav`);
      check(`[${vp.width}] Tab ${tab} 显示底部导航`, navVisible);
    }

    // Task 页多入口直达
    const taskEntries = [
      ['today', "go('today')"], ['trends-full', "go('trends-full')"], ['profile-edit', "go('profile-edit')"],
      ['facts', "go('facts')"], ['prefs', "go('prefs')"], ['messages', "go('messages')"],
      ['consent', "go('consent')"], ['deletion', "go('deletion')"],
      ['event', "openEvent('e1')"],
    ];
    for (const [pg, expr] of taskEntries) {
      await page.evaluate(expr);
      await page.waitForTimeout(300);
      const active = await page.$(`#pg-${pg}.active`);
      const text = active ? (await active.innerText()).length : 0;
      check(`[${vp.width}] Task 页 ${pg} 渲染且非空`, active && text > 40, `len=${text}`);
      const navHidden = !(await page.isVisible(`#pg-${pg} .root-nav`));
      check(`[${vp.width}] Task 页 ${pg} 隐藏底部导航`, navHidden);
    }

    // 布局遮挡检测：Root 页聊天输入区不被导航遮挡
    await page.evaluate("show('chat')");
    await page.waitForTimeout(200);
    const occl = await page.evaluate(() => {
      const inp = document.querySelector('#pg-chat .chat-input .ci-box').getBoundingClientRect();
      const nav = document.querySelector('#pg-chat .root-nav').getBoundingClientRect();
      return { inputBottom: inp.bottom, navTop: nav.top, gap: nav.top - inp.bottom };
    });
    check(`[${vp.width}] 聊天输入区在导航上方(gap>0)`, occl.gap > 0, JSON.stringify(occl));

    // Task 页底部操作栏在视口内且不超出
    await page.evaluate("go('profile-edit')");
    await page.waitForTimeout(200);
    const ba = await page.evaluate(() => {
      const el = document.querySelector('#pg-profile-edit .bottom-action').getBoundingClientRect();
      return { bottom: el.bottom, vh: window.innerHeight };
    });
    check(`[${vp.width}] 底部操作栏在视口内`, ba.bottom <= ba.vh + 1, JSON.stringify(ba));

    check(`[${vp.width}] 零 JS 错误`, jsErrors.length === 0, jsErrors.slice(0, 3).join(' | '));
    await page.close();
  }

  // ── 第三层：黄金流链路实测 ──
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const flowErrors = [];
  page.on('pageerror', e => flowErrors.push(String(e)));
  await page.goto(URL);
  await page.waitForTimeout(300);

  // 1) 登录 → 2) 发送记录 → 餐食卡片
  await page.click('#btn-login');
  await page.waitForTimeout(1100);
  await page.fill('#chat-text', '中午吃了一大碗牛肉面，加了一个卤蛋');
  await page.click('#btn-send');
  await page.waitForTimeout(1400);
  check('黄金流: 出现已保存餐食卡片', await page.isVisible('#pg-chat .meal-card'));

  // 3) 追问流程：睡眠 → 澄清选项 → 保存
  await page.fill('#chat-text', '昨晚睡了7小时');
  await page.click('#btn-send');
  await page.waitForTimeout(1400);
  check('黄金流: 出现追问卡片', await page.isVisible('#pg-chat .clarify'));
  await page.click('#pg-chat .clarify .opt');
  await page.waitForTimeout(500);
  check('黄金流: 追问回答后保存记录', (await page.$$('#pg-chat .meal-card')).length >= 2);

  // 4) 卡片 → 详情 → 修订历史
  await page.click('#pg-chat .meal-card');
  await page.waitForTimeout(300);
  check('黄金流: 进入记录详情', await page.isVisible('#pg-event.active'));
  check('黄金流: 详情含修订历史', (await page.innerText('#event-body')).includes('修订历史'));

  // 5) 详情 → 删除 → 隐私删除流程三步
  await page.click('#pg-event .btn.danger');
  await page.waitForTimeout(300);
  await page.click('#dg-ok');
  await page.waitForTimeout(400);
  check('黄金流: 进入隐私删除预览', await page.isVisible('#pg-deletion.active'));
  await page.click('#del-step1 .btn.primary');
  await page.waitForTimeout(300);
  check('黄金流: 影响范围预览显示', await page.isVisible('#del-step2'));
  await page.click('#del-step2 .btn.danger');
  await page.waitForTimeout(300);
  await page.click('#dg-ok');
  await page.waitForTimeout(1500);
  check('黄金流: 删除完成页', await page.isVisible('#del-step3'));

  // 6) 计划草案流：修改目标 → 超5% → 确认
  await page.evaluate("show('plan')");
  await page.waitForTimeout(200);
  await page.evaluate('editTarget()');
  await page.waitForTimeout(300);
  await page.click('#sheet .btn.primary');
  await page.waitForTimeout(400);
  check('黄金流: 计划草案待确认出现', (await page.innerText('#plan-body')).includes('草案待确认'));
  await page.evaluate('acceptPlan()');
  await page.waitForTimeout(300);
  check('黄金流: 确认后目标生效 1550', (await page.innerText('#plan-body')).includes('1550'));

  // 7) 发现页反馈
  await page.evaluate("show('discover')");
  await page.waitForTimeout(200);
  await page.click('#disc-insights .btn.soft');
  await page.waitForTimeout(300);
  check('黄金流: 洞察反馈后状态更新', (await page.innerText('#disc-insights')).includes('已认可'));

  // 8) 离线模拟（390 视口下评审工具条按媒体查询隐藏，直接触发函数）
  await page.evaluate(() => document.getElementById('btn-offline').click());
  await page.waitForTimeout(200);
  await page.evaluate("show('chat')");
  await page.fill('#chat-text', '今天跑步30分钟');
  await page.click('#btn-send');
  await page.waitForTimeout(400);
  check('黄金流: 离线时提示本地排队', (await page.innerText('#pg-chat')).includes('本地队列'));

  check('黄金流: 零 JS 错误', flowErrors.length === 0, flowErrors.slice(0, 3).join(' | '));
  await browser.close();

  // ── 报告 ──
  const pass = results.filter(r => r.pass).length;
  console.log(`\n通过 ${pass}/${results.length}`);
  errors.forEach(e => console.log(e));
  if (errors.length) process.exit(1);
  console.log('三层验证全部通过 ✓');
})();
