// Verify that a finished, read-only recording plays with its separate VTT track.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

async function main() {
  const [url, output, planPath] = process.argv.slice(2);
  if (!url || !output || !planPath || !url.startsWith('http://127.0.0.1:'))
    throw Error('Provide a loopback playback URL, JSON output path, and recording plan');
  const plan=JSON.parse(fs.readFileSync(planPath,'utf8'));
  const recording=JSON.parse(fs.readFileSync(path.join(plan.output,'recording.json'),'utf8'));
  const videoPath=path.join(plan.output,recording.video.file);
  const videoHash=crypto.createHash('sha256').update(fs.readFileSync(videoPath)).digest('hex');
  if(recording.status!=='recorded_pending_visual_review'||recording.new_model_calls!==0
      ||videoHash!==recording.video.sha256||recording.scenes.length!==8
      ||recording.errors.length||recording.blocked_requests.length)
    throw Error('Recording evidence changed or is incomplete');
  const {chromium}=require(plan.playwright_core);
  const browser = await chromium.launch({headless:true,
    executablePath:plan.browser_executable});
  const errors=[];
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    await page.goto(url,{waitUntil:'networkidle'});
    const media=page.locator('#review');
    await media.evaluate(video=>new Promise((resolve,reject)=>{
      if(video.readyState>=1)return resolve();
      video.addEventListener('loadedmetadata',resolve,{once:true});
      video.addEventListener('error',()=>reject(Error('Video load error')),{once:true});
    }));
    await page.waitForFunction(()=>document.querySelector('#review').textTracks[0]?.cues?.length===8);
    const metadata=await media.evaluate(video=>({duration:video.duration,width:video.videoWidth,
      height:video.videoHeight,cues:video.textTracks[0].cues.length,trackMode:video.textTracks[0].mode,
      mediaError:video.error?.message||null}));
    const times=await page.locator('button[data-time]').evaluateAll(buttons=>buttons.map(button=>Number(button.dataset.time)));
    if(times.length!==recording.scenes.length)throw Error('Chapter count differs from evidence');
    const chapterSeeks=[];
    for(const target of times){
      await page.locator(`button[data-time="${target}"]`).click();
      try {await page.waitForFunction(time=>{
        const video=document.querySelector('#review');
        return video.currentTime>=time-1 && video.readyState>=2 && !video.error;
      },target,{timeout:10000});}
      catch(error) {
        const playback=await media.evaluate(video=>({time:video.currentTime,paused:video.paused,
          readyState:video.readyState,networkState:video.networkState,mediaError:video.error?.message||null}));
        throw Error(JSON.stringify({target,metadata,playback,errors,cause:String(error)}));
      }
      chapterSeeks.push({target,actual:await media.evaluate(video=>video.currentTime)});
      if(target===55)await page.screenshot({path:path.join(path.dirname(output),'playback_checked.png')});
      await media.evaluate(video=>video.pause());
    }
    const seekedTo=chapterSeeks.find(row=>row.target===55)?.actual;
    if(errors.length||metadata.mediaError||metadata.width!==1440||metadata.height!==1000
        ||Math.abs(metadata.duration-recording.scenes.at(-1).end_seconds)>2
        ||metadata.cues!==8||seekedTo<54)
      throw Error(JSON.stringify({metadata,chapterSeeks,errors}));
    fs.writeFileSync(output,JSON.stringify({status:'passed',url,metadata,chapterSeeks,
      video_sha256:videoHash,screenshots_present:recording.scenes.every((_,index)=>
        fs.statSync(path.join(plan.output,String(index+1).padStart(2,'0')+'.png')).size>0),errors},null,2)+'\n');
    console.log(JSON.stringify({status:'passed',metadata,chapterSeeks}));
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
