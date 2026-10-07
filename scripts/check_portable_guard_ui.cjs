// Read-only browser check of a completed portable job and the optional plan field.
const fs=require('node:fs');
const path=require('node:path');

async function main(){
  const [base,jobId,configPath,output]=process.argv.slice(2);
  if(!base?.startsWith('http://127.0.0.1:')||!jobId||!configPath||!output)
    throw Error('Expected loopback URL, completed job ID, browser config, and output JSON');
  const config=JSON.parse(fs.readFileSync(configPath,'utf8'));
  const {chromium}=require(config.playwright_core);
  const browser=await chromium.launch({headless:true,executablePath:config.browser_executable});
  const errors=[];
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('response',response=>{
      if(response.status()>=400 && !response.url().endsWith('/favicon.ico'))
        errors.push(`${response.status()} ${response.url()}`);
    });
    page.on('console',message=>{
      if(message.type()==='error' && !message.text().includes('404 (Not Found)'))
        errors.push(message.text());
    });
    const url=`${base}/?job=${encodeURIComponent(jobId)}`;
    await page.goto(url,{waitUntil:'networkidle'});
    await page.waitForFunction(id=>document.querySelector('#job-title')?.textContent===id,jobId);
    await page.waitForFunction(()=>document.querySelector('#status')?.dataset.status==='completed');
    const before=await page.evaluate(async()=>await(await fetch('/api/jobs')).json());
    const original=await page.evaluate(()=>payload());
    if(Object.hasOwn(original.overrides,'max_new_network_oversaturated_km'))
      throw Error('Unfilled optional field unexpectedly changes legacy payload');
    await page.locator('.constraints summary').click();
    await page.locator('#network-overload').fill('0.1');
    await page.locator('#preview').click();
    await page.waitForFunction(()=>document.querySelector('#plan-panel pre')?.textContent.includes('additional_hard_constraint'));
    const plan=JSON.parse(await page.locator('#plan-panel pre').textContent());
    if(plan.task.max_new_network_oversaturated_km!==0.1 ||
       !plan.selection_protocol?.additional_hard_constraint?.includes('0.1'))
      throw Error('Preview does not carry the configured safety limit');
    const after=await page.evaluate(async()=>await(await fetch('/api/jobs')).json());
    if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Read-only check changed job list');
    const screenshot=path.join(path.dirname(output),'guard_ui_checked.png');
    await page.screenshot({path:screenshot,fullPage:true});
    if(errors.length)throw Error(JSON.stringify(errors));
    const result={status:'passed',url,job_id:jobId,job_status:'completed',
      old_payload_omits_optional_limit:true,preview_limit_km:0.1,
      preview_rule:plan.selection_protocol.additional_hard_constraint,
      new_model_calls:0,job_list_unchanged:true,console_errors:errors,screenshot:path.basename(screenshot)};
    fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');
    process.stdout.write(JSON.stringify(result)+'\n');
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
