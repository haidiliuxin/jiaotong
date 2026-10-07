// Real browser check: saved task parameters stay distinct from a new task draft.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH || '../tools/demo/node_modules/playwright-core');

async function main() {
  const output = path.resolve(__dirname, '../成果/界面核验');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (request.method() === 'POST') requests.push(new URL(request.url()).pathname);
  });
  try {
    await page.goto(process.env.DEMO_URL || 'http://127.0.0.1:8766');
    const saved = page.locator('#record-task-panel');
    await page.locator('[data-job="20260908_051630_12acc1"]').click();
    await page.waitForFunction(() => document.querySelector('#record-task-panel').textContent.includes('3 条 / 4 次'));
    assert.match(await saved.textContent(), /1 · 复跑/);
    await page.locator('#budget').fill('6');
    await page.locator('#sensitivity').uncheck();
    await page.locator('#preview').click();
    await page.waitForFunction(() => document.querySelector('#plan-panel').textContent.includes('3 条 / 6 次'));
    assert.match(await page.locator('#plan-panel h3').textContent(), /新任务预览/);
    assert.match(await saved.textContent(), /3 条 \/ 4 次/);
    await page.locator('[data-job="20260908_031854_677ea5"]').click();
    await page.waitForFunction(() => document.querySelector('#record-task-panel').textContent.includes('3 条 / 10 次'));
    assert.match(await saved.textContent(), /0.9 \/ 1 \/ 1.1/);
    await page.locator('#refresh').click();
    assert.equal(await page.locator('#budget').inputValue(), '6');
    await page.locator('[data-job="20260908_051630_12acc1"]').click();
    await page.waitForFunction(() => document.querySelector('#record-task-panel').textContent.includes('3 条 / 4 次'));
    await saved.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, '实际任务与新建草稿.png') });
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, ['/api/plan']);
    fs.writeFileSync(path.join(output, 'task_context_check.json'), JSON.stringify({
      passed: true, checked_at: new Date().toISOString(), errors, post_requests: requests,
      checks: ['saved budget 4 and demand 1', 'new draft budget 6 stays separate',
        'history switch loads saved budget 10 and demand 0.9/1/1.1', 'refresh preserves draft',
        'switch back loads original saved task', 'no simulation was submitted']
    }, null, 2));
    console.log('PASS: saved tasks, new drafts, history switching; no simulation submitted');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
