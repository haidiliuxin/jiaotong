// Real browser recording of the local workbench; no mocked API or result values.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, '成果', '演示');
  fs.mkdirSync(output, { recursive: true });
  const url = process.env.DEMO_URL || 'http://127.0.0.1:8765';
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 },
    locale: 'zh-CN', recordVideo: { dir: output, size: { width: 1440, height: 1000 } } });
  const page = await context.newPage();
  const video = page.video();
  const events = [];
  const errors = [];
  const started = Date.now();
  let completed = false;
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (request.method() === 'POST') events.push({ time: Date.now() - started, type: 'request', method: 'POST', url: request.url() });
  });
  async function caption(text) {
    // A recording-only caption, clearly labelled; application data is untouched.
    await page.evaluate(text => {
      let element = document.getElementById('recording-caption');
      if (!element) {
        element = document.createElement('div');
        element.id = 'recording-caption';
        element.style.cssText = 'position:fixed;bottom:0;left:0;right:0;padding:15px 30px;background:#102d3ef5;color:white;font:20px/1.6 "Microsoft YaHei",sans-serif;z-index:2147483647;border-top:3px solid #8dccdc;pointer-events:none';
        document.body.appendChild(element);
      }
      element.textContent = '录屏说明｜' + text;
    }, text);
  }
  async function scene(label, text, action, seconds = 7) {
    await action();
    await caption(text);
    events.push({ time: Date.now() - started, type: 'scene', label, caption: text });
    await page.screenshot({ path: path.join(output, `review_${label}.png`) });
    await page.waitForTimeout(seconds * 1000);
  }
  try {
    await page.goto(url);
    await page.locator('#history [data-job]').first().waitFor();
    await scene('01_intro', '真实浏览器操作：任务预览与已完成实验复核。本段不重新启动模型。', async () => {}, 6);
    await scene('02_task', '结构化任务卡明确范围、预算和小汽车需求系数；自然语言入口采用公开模板。', async () => {
      await page.locator('#request').fill('分析停车干扰拥堵，比较治理强度并检查周边影响');
      await page.locator('#links').fill('3');
      await page.locator('#budget').fill('10');
      await page.locator('#preview').click();
      await page.locator('#plan-panel h3').filter({ hasText: '任务卡' }).waitFor();
      await page.locator('#plan-panel').scrollIntoViewIfNeeded();
    }, 9);
    await scene('03_core', '选择已完成的10场景停车实验。核心速度提高，但过饱和里程仍需同时评价。', async () => {
      await page.locator('[data-job="20260908_031854_677ea5"]').click();
      await page.waitForFunction(() => document.getElementById('job-title').textContent === '20260908_031854_677ea5');
      await page.locator('#scope').selectOption('core');
      await page.locator('#result-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 10);
    await scene('04_neighbor', '切换固定邻区：不能只用核心区改善证明没有外溢。', async () => {
      await page.locator('#scope').selectOption('neighbor');
    }, 7);
    await scene('05_network', '切换全网：行驶时间改善幅度较小，过饱和里程增加也如实保留。', async () => {
      await page.locator('#scope').selectOption('network');
    }, 9);
    await scene('06_feedback', '读取实测结果后，智能体决定增强治理或停止搜索；候选与理由保留。', async () => {
      await page.locator('#decisions').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 9);
    await scene('07_demand', '0.9和1.1需求下原方案未通过约束，反馈将治理范围由3条缩至1条后重新比较。', async () => {
      await page.locator('#conditional-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 11);
    await scene('08_logs', '真实工具调用有模块名、时间和结果记录。完整原始日志与输入差异随数据库保存。', async () => {
      await page.locator('.trace-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 7);
    await scene('09_fallback', '选择另一项真实实验：停车、禁转与组合未通过验证，程序保留baseline。', async () => {
      await page.locator('[data-job="20260908_040515_6a3455"]').click();
      await page.waitForFunction(() => document.getElementById('job-title').textContent === '20260908_040515_6a3455');
      await page.locator('#scope').selectOption('core');
      await page.locator('#result-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 9);
    await scene('10_rejection', '拒绝无效方案是闭环的一部分。禁转进入缓存并不等于已通过实际响应检查。', async () => {
      await page.locator('#decisions').evaluate(el => el.scrollIntoView({ block: 'start' }));
    }, 9);
    await scene('11_gate', '未验证的微循环能力仍有门槛，不会被当成停车流程直接执行。', async () => {
      await page.locator('#request').fill('微循环转向治理');
      await page.locator('#preview').click();
      await page.locator('#plan-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
      await page.waitForFunction(() => document.getElementById('plan-panel').textContent.includes('先完成'));
    }, 8);
    await scene('12_end', '本段为实际浏览器中的既有实验复核；源代码、输入数据库、报告与可复现命令一并提供。', async () => {}, 6);
    if (events.some(e => e.type === 'request' && e.url.endsWith('/api/run'))) throw Error('Review-only recording unexpectedly launched a model');
    if (errors.length) throw Error('Browser errors: ' + errors.join('; '));
    completed = true;
  } finally {
    await context.close();
    await browser.close();
    const recorded = await video.path();
    const target = path.join(output, completed ? '01_任务卡与实验复核.webm' : `failed_review_${started}.webm`);
    fs.copyFileSync(recorded, target);
    fs.unlinkSync(recorded);
    fs.writeFileSync(path.join(output, completed ? '01_recording.json' : `failed_review_${started}.json`), JSON.stringify({
      status: completed ? 'completed' : 'failed',
      captured_at: new Date(started).toISOString(), elapsed_seconds: (Date.now() - started) / 1000,
      url, video: path.basename(target), browser: 'Playwright Chromium, real local application',
      mode: 'review_existing_completed_jobs', mocked_requests: false, application_data_modified: false,
      captions: 'Only the labelled recording-caption element is injected; no result text is changed.',
      errors, events
    }, null, 2));
    console.log(target);
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
