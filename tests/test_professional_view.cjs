// The browser must show gross local loss separately and escape source labels.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('traffic_agent/static/app.js','utf8');
const start=source.indexOf('function professionalImpact('),end=source.indexOf('function drawConditional(){',start);
assert(start>=0&&end>start);
const context={escapeHTML:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  number:x=>x==null?'—':Number(x).toLocaleString('zh-CN',{maximumFractionDigits:4})};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const html=context.professionalImpact({rule:'净值不可掩盖局部损失',boundary:'不是新增门槛',
  evidence_scope:{demand_levels:[1],repeat_status:'not_requested'},next_checks:[{action:'追加 <script>'}],policies:[
    {run:'parking_1',demand_factor:1,status:'measured',core_link_count:2,worsened_link_count:1,
      gross_added_pcu_h:10,gross_saved_pcu_h:18,net_change_pcu_h:-8,
      largest_increases:[{name:'<img src=x onerror=alert(1)>',from_node:1,to_node:2,added_pcu_h:10}]},
    {run:'parking_2',demand_factor:1,status:'unverified',gap:'缺少配对 <script>'}]});
for(const item of ['局部影响与证据边界','正增量 10','改善量 18','净变化 -8','耗时增加','未验证','现场标定','后续证据建议'])assert(html.includes(item),item);
assert(html.includes('&lt;img'));assert(html.includes('&lt;script&gt;'));
assert(!html.includes('<img'));assert(!html.includes('<script>'));
console.log('Professional local-impact card keeps masked losses and missing evidence visible without HTML injection');
