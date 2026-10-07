// Explicit user permission for Playwright recording is required before execution.
// No generated model outputs or response interception; only completed-task review.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

async function main() {
  if (!process.argv[2]) throw Error('Provide the reviewed recording-plan JSON');
  const planPath = path.resolve(process.argv[2]);
  const plan = JSON.parse(fs.readFileSync(planPath,'utf8'));
  const url = new URL(plan.url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') throw Error('Only the named local application is allowed');
  const job = path.resolve(plan.job);
  const statePath = path.join(job,'state.json');
  const archivePath = path.join(job,'archive_manifest.json');
  const state = JSON.parse(fs.readFileSync(statePath,'utf8'));
  if (state.status !== 'completed' || state.id !== plan.job_id || sha(archivePath) !== plan.archive_sha256) throw Error('Completed source evidence does not match the reviewed plan');
  if (plan.review_mode === 'turn-grid' && (state.task.scenario !== 'circulation' || state.runs.length !== 13
      || state.recommendation !== 'S2_turn' || state.task.demand_factors.join(',') !== '0.9,1,1.1'))
    throw Error('Turn-grid evidence does not match the reviewed plan');
  if (plan.review_mode === 'robust-grid' && (state.task.scenario !== 'parking'
      || state.task.search_mode !== 'robust' || state.runs.length !== 11
      || state.recommendation !== 'robust_p3_1' || state.task.max_new_network_oversaturated_km !== 0.1
      || state.task.demand_factors.join(',') !== '0.9,1,1.1'))
    throw Error('Robust-grid evidence does not match the reviewed plan');
  const output = path.resolve(plan.output);
  if (fs.existsSync(output)) throw Error('Recording output already exists; do not overwrite');
  fs.mkdirSync(output,{recursive:true});
  const meta = {status:'auditing',kind:'completed_real_task_review',new_model_calls:0,
    started:new Date().toISOString(),plan_sha256:sha(planPath),script_sha256:sha(__filename),
    job_id:state.id,archive_sha256:sha(archivePath),state_sha256:sha(statePath),
    source_runtime_code:plan.runtime_code,scenes:[],errors:[],blocked_requests:[],
    limits:plan.review_mode === 'turn-grid'
      ? '已完成三需求禁转任务的界面回看，不是本次新建仿真的连续录像；高需求全网毛新增过饱和仍需单独披露。'
      : plan.review_mode === 'robust-grid'
      ? '已完成三需求停车任务的界面回看，不是连续求解录像；1.0需求下仍有0.056km全网毛新增过饱和。'
      : '已完成真实任务的界面回看，不是本次新建仿真的连续录像。只有需求系数1.0。'};
  const persist=()=>fs.writeFileSync(path.join(output,'recording.json'),JSON.stringify(meta,null,2));
  const audit=label=>{
    const r=spawnSync(plan.python,[plan.verifier,job,'--archive'],{cwd:plan.runtime_root,encoding:'utf8',windowsHide:true,timeout:300000});
    fs.writeFileSync(path.join(output,label+'.stdout.txt'),r.stdout||'');
    fs.writeFileSync(path.join(output,label+'.stderr.txt'),r.stderr||'');
    if(r.status!==0)throw Error(label+' independent audit failed');
  };
  let browser,context,video;
  try {
    audit('before');
    const {chromium}=require(plan.playwright_core);
    meta.playwright_version=require(path.join(plan.playwright_core,'package.json')).version;
    browser=await chromium.launch({headless:true,executablePath:plan.browser_executable});
    context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',
      recordVideo:{dir:output,size:{width:1440,height:1000}}});
    await context.route('**/*',route=>{
      const req=route.request(),target=new URL(req.url());
      if(target.origin===url.origin && req.method()==='GET')return route.continue();
      meta.blocked_requests.push({url:req.url(),method:req.method()});
      return route.abort();
    });
    const page=await context.newPage();
    video=page.video();
    page.on('pageerror',e=>meta.errors.push(String(e)));
    meta.status='recording';persist();
    const started=Date.now();
    async function hold(title,seconds) {
      const begin=(Date.now()-started)/1000;
      await page.screenshot({path:path.join(output,String(meta.scenes.length+1).padStart(2,'0')+'.png')});
      await delay(seconds*1000);
      meta.scenes.push({title,start_seconds:begin,end_seconds:(Date.now()-started)/1000,
        job_title:await page.locator('#job-title').innerText(),status:await page.locator('#status').innerText()});persist();
    }
    await page.goto(plan.url+'/?job='+encodeURIComponent(state.id));
    await page.locator('#status[data-status="completed"]').waitFor();
    if(await page.locator('#job-title').innerText()!==state.id)throw Error('Wrong task in visible page');
    const response=await context.request.get(plan.url+'/api/jobs/'+state.id);
    const actual=(await response.json()).state;
    const canonical=x=>JSON.stringify(x,Object.keys(x).sort());
    if(actual.id!==state.id || actual.status!=='completed' || actual.recommendation!==state.recommendation
       || actual.runs.length!==state.runs.length || canonical(actual.task)!==canonical(state.task))throw Error('Served task differs from saved evidence');
    await page.locator('#map-run').selectOption(state.recommendation);
    await page.locator('#map-message').filter({hasText:state.recommendation+' − baseline'}).waitFor({timeout:30000});
    if (plan.review_mode === 'turn-grid') {
      await hold('已完成的禁转实测任务；13场景、91顶层及520次原生车型调用，本次新增模型调用0。',14);
      await page.locator('[data-view="split"]').click();
      await page.locator('#map-fit').click();
      await hold('基准需求下的S2禁转与同需求基准双图；有向路段颜色来自实际输出。',16);
      await page.locator('#map-run').selectOption('demand_0p900_S2_turn');
      await page.locator('#map-after-name').filter({hasText:'demand_0p900_S2_turn'}).waitFor({timeout:30000});
      await hold('低需求0.9独立配对：只在各自的基准下比较，不跨需求混算收益。',16);
      await page.locator('#map-run').selectOption('demand_1p100_S2_turn');
      await page.locator('#map-after-name').filter({hasText:'demand_1p100_S2_turn'}).waitFor({timeout:30000});
      await hold('高需求1.1仍有外溢风险；本片展示原版决策，不能把局部收益写成全面改善。',17);
      await page.locator('#tab-analysis').click();
      await hold('容差v2在已测需求下选择较简单的S2；其他候选及硬约束结果仍保留。',18);
      await page.locator('#tradeoffs').scrollIntoViewIfNeeded();
      await hold('不利影响栏列出保存的同需求配对指标；过饱和净变化不等于毛新增。',17);
      await page.locator('#archive-open').click();
      await page.locator('#scope').selectOption('network');
      await hold('实验档案核对三需求任务卡、13场景和全网指标；原始证据可独立复核。',17);
      await page.locator('#archive-close').click();
      await page.locator('#tab-messages').click();
      await hold('时间线与禁转响应来自已保存的真实任务；这里没有重新运行TranStar。',12);
    } else if (plan.review_mode === 'robust-grid') {
      await hold('v5便携包已完成三需求任务；11场景、77次TranStar顶层调用，录制新增调用0。',14);
      await page.locator('[data-view="split"]').click();
      await page.locator('#map-fit').click();
      await hold('基准需求1.0：单方向停车1级与同需求基准双图；全网耗时下降0.0604%。',16);
      await page.locator('#map-run').selectOption('robust_p3_0.9');
      await page.locator('#map-after-name').filter({hasText:'robust_p3_0.9'}).waitFor({timeout:30000});
      await hold('低需求0.9独立配对：核心耗时下降45.485%，全网毛新增过饱和0公里。',16);
      await page.locator('#map-run').selectOption('robust_p3_1.1');
      await page.locator('#map-after-name').filter({hasText:'robust_p3_1.1'}).waitFor({timeout:30000});
      await hold('高需求1.1独立配对：核心耗时下降33.984%，全网毛新增过饱和0公里。',16);
      await page.locator('#map-run').selectOption('robust_p3_1');
      await page.locator('#map-message').filter({hasText:'robust_p3_1 − baseline'}).waitFor({timeout:30000});
      await page.locator('#tab-analysis').click();
      await hold('三路段联合方案因邻区恶化被拒；按实测缩到单方向，并验证2级与1级。',18);
      await page.locator('#tradeoffs').scrollIntoViewIfNeeded();
      await hold('基准需求1.0仍新增0.056公里过饱和路段；它低于固定的0.1公里硬上限。',17);
      await page.locator('#archive-open').click();
      await page.locator('#scope').selectOption('network');
      await hold('档案保留三需求任务卡、固定范围、77次调用及逐路段原始输入输出。',17);
      await page.locator('#archive-close').click();
      await page.locator('#tab-messages').click();
      await hold('事件时间线与反馈均来自已封存任务；片中没有重新求解，也不代表现实实施。',12);
    } else {
      await hold('已完成真实任务回看；4场景、28次原始顶层调用，本次新增模型调用0。',14);
      await page.locator('[data-view="split"]').click();
      await page.locator('#map-fit').click();
      await hold('同需求、同范围的基准与治理双图对照；颜色读取真实输出。',17);
      await page.locator('#tab-analysis').click();
      await hold('算法读取首轮结果并增加治理强度；建议parking_2，仅验证需求1.0。',20);
      await page.locator('#tradeoffs').scrollIntoViewIfNeeded();
      await hold('收益和代价同时披露：全网过饱和里程净增约0.584km。',18);
      await page.locator('#archive-open').click();
      await page.locator('#scope').selectOption('network');
      await hold('实验档案展示实际任务与全网指标；基准复跑检查保留。',18);
      await page.locator('#archive-close').click();
      await page.locator('#tab-messages').click();
      await hold('时间线来自这个任务已保存的事件；完整日志和核验记录可追溯。',18);
      await page.locator('#tab-situation').click();
      await hold('赛事调整案例，不代表现实南京交通；三需求扩展验收单独记录。',12);
    }
    await context.close();context=null;
    const source=await video.path();
    const target=path.join(output,'已完成真实任务回看.webm');
    fs.copyFileSync(source,target);
    meta.video={file:path.basename(target),bytes:fs.statSync(target).size,sha256:sha(target)};
    audit('after');
    if(sha(statePath)!==meta.state_sha256 || sha(archivePath)!==meta.archive_sha256)throw Error('Source evidence changed');
    if(meta.errors.length || meta.blocked_requests.length)throw Error('Browser errors or unexpected requests recorded');
    const timestamp=seconds=>{
      const ms=Math.round(seconds*1000);
      return [Math.floor(ms/3600000),Math.floor(ms/60000)%60,Math.floor(ms/1000)%60].map(x=>String(x).padStart(2,'0')).join(':')+'.'+String(ms%1000).padStart(3,'0');
    };
    fs.writeFileSync(path.join(output,'说明字幕.vtt'),'WEBVTT\n\n'+meta.scenes.map((s,i)=>`${i+1}\n${timestamp(s.start_seconds)} --> ${timestamp(s.end_seconds)}\n${s.title}\n`).join('\n'));
    meta.status='recorded_pending_visual_review';meta.finished=new Date().toISOString();persist();
  } catch(error) {meta.status='failed';meta.error=String(error);persist();throw error;}
  finally {if(context)await context.close();if(browser)await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
