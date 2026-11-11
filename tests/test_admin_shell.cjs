const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/bot_wumtaaba/static');
class Element{
 constructor(){this.children=[];this.dataset={};this.attributes={};this.className='';}
 append(...items){this.children.push(...items);}
 replaceChildren(){this.children=[];}
 setAttribute(name,value){this.attributes[name]=value;}
}
async function shell(pathname,search,environment='development',ok=true){
 const target=new Element();
 const context={document:{createElement:()=>new Element(),querySelectorAll:selector=>selector==='[data-admin-sidebar]'?[target]:target.children.filter(x=>x.dataset.environment!==undefined)},location:{pathname,search},URLSearchParams,window:{},fetch:async()=>({ok,json:async()=>({environment})})};
 vm.runInNewContext(fs.readFileSync(path.join(root,'admin-shell.js'),'utf8'),context);await new Promise(resolve=>setImmediate(resolve));return {target,nav:target.children[2],api:context.window.AdminShell};
}
test('Development navigation adds the simulator to the five shared pages',async()=>{
 const h=await shell('/admin','?view=annotations');
 assert.equal(h.nav.children.length,6);assert.deepEqual(h.nav.children.map(a=>a.dataset.page),['agreements','conversations','annotations','check','models','simulator']);
 assert.equal(h.nav.children.filter(a=>a.attributes['aria-current']==='page').length,1);assert.equal(h.nav.children[2].attributes['aria-current'],'page');
 assert.equal(h.nav.children[3].href,'/admin/check');assert.match(h.target.children[3].textContent,/DEV/);
});
test('Check route selects its shared menu entry and invalid query defaults to agreements',async()=>{
 const check=await shell('/admin/check','', 'production');assert.equal(check.api.current(),'check');assert.match(check.target.children[3].textContent,/PROD/);
 const fallback=await shell('/admin','?view=anything');assert.equal(fallback.api.current(),'agreements');
});
test('Both rendered pages use the same shell assets and retain Check result DOM',()=>{
 for(const file of ['admin.html','check.html']){const html=fs.readFileSync(path.join(root,file),'utf8');assert.match(html,/data-admin-sidebar/);assert.match(html,/\/admin\/assets\/admin-shell\.js/);assert.match(html,/\/admin\/assets\/admin-shell\.css/);}
 const check=fs.readFileSync(path.join(root,'check.html'),'utf8');for(const task of ['transcribe','translate','extract'])assert.ok(check.includes('id="result-'+task+'"'));
 const admin=fs.readFileSync(path.join(root,'admin.html'),'utf8');const scripts=[...admin.matchAll(/<script>([\s\S]*?)<\/script>/g)];for(const block of scripts)new vm.Script(block[1]);
});


test('Production and unavailable environment never display the development simulator',async()=>{
 for(const h of [await shell('/admin','', 'production'),await shell('/admin','', 'development',false)]){assert.equal(h.nav.children.length,5);assert.equal(h.nav.children.some(a=>a.href==='/admin/simulator'),false);}
 const simulator=await shell('/admin/simulator','');assert.equal(simulator.api.current(),'simulator');assert.equal(simulator.nav.children[5].attributes['aria-current'],'page');
 await simulator.api.refresh();assert.equal(simulator.nav.children.length,6);
});
