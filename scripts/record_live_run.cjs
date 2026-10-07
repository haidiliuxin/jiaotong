// Record actual task creation, wait for its real model runs, then record its results.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, '成果', '演示');
  const url = process.env.DEMO_URL || 'http://127.0.0.1:8766';
  const benchmark = path.join(root, '智能体实验/benchmarks/20260908_045109_24146f');
  const metadata = { status: 'waiting_for_benchmark', started: new Date().toISOString(), url,
    mode: 'actual_new_task_start_and_completion', segments: [], observations: [], errors: [] };
  fs.mkdirSync(output, { recursive: true });
  const metaPath = path.join(output, '02_live_recording.json');
  const persist = () => fs.writeFileSync(metaPath, JSON.stringify(metadata, null, 2));
  persist();
  console.log('WAITING_FOR_BENCHMARK', benchmark);
  const waitStarted = Date.now();
  while (true) {
    const state = JSON.parse(fs.readFileSync(path.join(benchmark, 'state.json'), 'utf8'));
    if (state.status === 'completed') break;
    if (state.status === 'failed') throw Error('Benchmark failed; do not start another model workload');
    if (Date.now() - waitStarted > 3600000) throw Error('Benchmark wait exceeded one hour; no new task started');
    await delay(3000);
  }
  const benchmarkCheck = spawnSync('python', ['scripts/verify_benchmark.py', benchmark], { cwd: root, encoding: 'utf8' });
  fs.writeFileSync(path.join(output, 'benchmark_verification.stdout.txt'), benchmarkCheck.stdout || '');
  if (benchmarkCheck.status !== 0) throw Error('Benchmark verification failed: ' + benchmarkCheck.stderr);
  metadata.status = 'recording_start';
  persist();
  const browser = await chromium.launch({ headless: true });
  let context;
  let page;
  let segmentVideo;
  async function openSegment() {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN',
      recordVideo: { dir: output, size: { width: 1440, height: 1000 } } });
    page = await context.newPage();
    segmentVideo = page.video();
    page.on('pageerror', error => metadata.errors.push(String(error)));
    await page.goto(url);
    await page.locator('#history [data-job]').first().waitFor();
  }
  async function caption(text, seconds, screenshot) {
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
    metadata.observations.push({ time: new Date().toISOString(), caption: text });
    persist();
    if (screenshot) await page.screenshot({ path: path.join(output, screenshot) });
    await delay(seconds * 1000);
  }
  async function closeSegment(name) {
    await context.close();
    const source = await segmentVideo.path();
    const target = path.join(output, name);
    fs.copyFileSync(source, target);
    fs.unlinkSync(source);
    metadata.segments.push({ file: name, saved: new Date().toISOString(), bytes: fs.statSync(target).size });
    persist();
    context = null;
  }
  try {
    await openSegment();
    await page.locator('#request').fill('分析停车干扰拥堵，比较治理强度并检查周边影响');
    await page.locator('#links').fill('3');
    await page.locator('#budget').fill('4');
    await page.locator('#sensitivity').uncheck();
    await page.locator('#repeat').check();
    await page.locator('#preview').click();
    await page.locator('#plan-panel h3').filter({ hasText: '任务卡' }).waitFor();
    await page.locator('#plan-panel').scrollIntoViewIfNeeded();
    await caption('现在实际创建新任务：3路段、4次预算、同输入复跑；本次不做额外需求系数。', 8, 'live_01_task.png');
    const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/run') && response.request().method() === 'POST');
    await page.locator('#run').click();
    const response = await responsePromise;
    if (response.status() !== 202) throw Error('Actual task request rejected: ' + await response.text());
    const body = await response.json();
    metadata.job_id = body.id;
    metadata.run_request = response.request().postDataJSON();
    metadata.run_accepted = new Date().toISOString();
    metadata.status = 'model_running';
    persist();
    console.log('LIVE_JOB', body.id);
    await page.waitForFunction(id => document.getElementById('job-title').textContent === id, body.id);
    await page.locator('.workspace-head').scrollIntoViewIfNeeded();
    await caption('已提交并接受新任务。任务编号与下方实际工具日志对应，随后等待真实模型计算。', 10, 'live_02_started.png');
    await page.locator('.trace-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    await caption('道路、公交和管理预处理后进入机动车分配；完整计算间隔将省略，原始日志连续保留。', 12, 'live_03_tools.png');
    await closeSegment('02_实际创建与启动.webm');
    const modelStarted = Date.now();
    let last;
    while (true) {
      const state = (await (await fetch(`${url}/api/jobs/${body.id}`)).json()).state;
      if (state.active_scenario !== last) {
        last = state.active_scenario;
        metadata.observations.push({ time: new Date().toISOString(), active_scenario: last, status: state.status, run_count: state.run_count });
        persist();
        console.log('MODEL_PROGRESS', state.status, state.run_count, last);
      }
      if (state.status === 'completed') break;
      if (['failed', 'needs_validation', 'no_candidate'].includes(state.status)) throw Error('Live task did not complete: ' + JSON.stringify(state));
      if (Date.now() - modelStarted > 3600000) throw Error('Live model observation exceeded one hour; no restart attempted');
      await delay(3000);
    }
    const job = path.join(root, '智能体实验/jobs', body.id);
    const check = spawnSync('python', ['scripts/verify_experiment.py', job], { cwd: root, encoding: 'utf8' });
    fs.writeFileSync(path.join(output, 'live_verification.stdout.txt'), check.stdout || '');
    if (check.status !== 0) throw Error('Live experiment verification failed: ' + check.stderr);
    metadata.verified_at = new Date().toISOString();
    metadata.status = 'recording_completion';
    persist();
    await openSegment();
    await page.locator(`[data-job="${body.id}"]`).click();
    await page.waitForFunction(id => document.getElementById('job-title').textContent === id, body.id);
    await page.locator('#scope').selectOption('core');
    await page.locator('#result-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    await caption(`计算已完成：同一个任务 ${body.id}，中间等待时间省略，输入与输出通过独立核验。`, 10, 'live_04_completed.png');
    await page.locator('#scope').selectOption('network');
    await caption('全网指标与核心指标分别评价。基准复跑的4类路段数组逐项相等，详细口径见报告。', 9, 'live_05_network.png');
    await page.locator('#decisions').evaluate(el => el.scrollIntoView({ block: 'start' }));
    await caption('这个新任务根据首轮结果作出反馈，推荐由完成的候选比较产生。', 9, 'live_06_decisions.png');
    await page.locator('.trace-panel').evaluate(el => el.scrollIntoView({ block: 'start' }));
    await caption('已展示真实创建、模型调用和同任务结果读取；录像、源输入、运行日志及核验文件共同提供证据。', 8, 'live_07_end.png');
    await closeSegment('03_同任务完成与复核.webm');
    if (metadata.errors.length) throw Error('Browser errors were recorded');
    metadata.status = 'completed';
    metadata.finished = new Date().toISOString();
    persist();
  } finally {
    if (context) await context.close();
    await browser.close();
  }
}
main().catch(error => {
  const filename = path.resolve(__dirname, '../成果/演示/02_live_recording.json');
  if (fs.existsSync(filename)) {
    const state = JSON.parse(fs.readFileSync(filename, 'utf8'));
    state.status = 'failed'; state.error = String(error); state.finished = new Date().toISOString();
    fs.writeFileSync(filename, JSON.stringify(state, null, 2));
  }
  console.error(error); process.exitCode = 1;
});
