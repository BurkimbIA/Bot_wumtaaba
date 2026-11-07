const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/bot_wumtaaba/static/check.js'),'utf8');
class Element{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.value='';this.disabled=false;this.hidden=false;this.dataset={};this.files=[];this._text='';this.scrollHeight=100;this.paused=true;}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(x=>x.textContent||'').join('');}
 get childNodes(){return this.children;}
 get firstChild(){return this.children[0];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this._text='';this.children=[...items];}
 setAttribute(k,v){this.attributes[k]=v;}
 removeAttribute(k){delete this.attributes[k];}
 pause(){this.paused=true;if(this.onpause)this.onpause();}
 async play(){this.paused=false;if(this.onplay)this.onplay();}
 requestSubmit(){return this.onsubmit({preventDefault(){}});}
}
const nodes=(element)=>[element,...element.children.flatMap(nodes)];
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
async function harness({saved=[],respond}={}){
 const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};$('provider').value='mac';
 const records=new Map(saved.map(item=>[item.id,item])),requests=[];
 const downloads=[];const library={audioUrl(value){return '/admin/api/experiments/'+value.id+'/audio';},async audio(value){downloads.push(value.id);return value.blob||new Blob(['remote voice'],{type:'audio/wav'});},async list(){return [...records.values()];},async save(value){records.set(value.id,structuredClone(value));return value;},async remove(id){records.delete(id);}};
 class Reader{readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result='data:audio/wav;base64,'+Buffer.from(bytes).toString('base64');this.onload();});}}
 const context=vm.createContext({document:{getElementById:$,createElement:tag=>new Element(tag),querySelectorAll:()=>[...elements.values(),...([...elements.values()].flatMap(nodes))]},window:{AudioLibrary:library,addEventListener(){}},navigator:{},crypto:require('node:crypto').webcrypto,Blob,URL,Uint8Array,Float32Array,DataView,ArrayBuffer,FileReader:Reader,performance,setInterval,clearInterval,
 fetch:async(url,options)=>{
  let data;if(url==='/admin/api/check/models')data={transcription:[{id:'whisper',label:'Whisper',available:true},{id:'wav2vec',label:'wav2vec',available:true}],translation:[{id:'nllb',label:'NLLB',available:true},{id:'yaaba',label:'Yaaba',available:true}],cloud_transcription:[{id:'bia-transcription-v1',label:'Whisper · RunPod direct',available:true}],cloud_translation:[],cloud_note:'Whisper utilise RunPod. Traduction indisponible.'};
  else if(url==='/admin/api/check/status')data={mac:{status:'ok'},cloud_configured:true};
  else {const body=JSON.parse(options.body);requests.push({url,body});data=respond?await respond(url,body):url.endsWith('/transcribe')?{ok:true,text:'Mooré transcrit'}:url.endsWith('/translate')?{ok:true,text:'Traduction française'}:{draft:{client:'Awa'}};}
  return {ok:true,json:async()=>data};
 }});vm.runInContext(source,context);await settle();return {$,requests,records,context,downloads};
}
const clip=(id,name='Essai')=>({id,name,blob:new Blob(['voice '+id],{type:'audio/wav'}),createdAt:1,results:[]});
function add(h,name='Nouveau.wav'){h.$('audio-file').files=[Object.assign(new Blob(['same vocal'],{type:'audio/wav'}),{name})];h.$('audio-file').onchange();}

test('Saved audios are rows; selection loads only that audio and results, deletion is scoped',async()=>{
 const a={...clip('a','Premier'),results:[{task:'transcribe',output:'Résultat A'}]},b={...clip('b','Deuxième'),results:[{task:'transcribe',output:'Résultat B'}]};
 const h=await harness({saved:[a,b]});assert.equal(h.$('history-count').textContent,'2');assert.match(h.$('result-transcribe').value,/Résultat A/);
 const choices=nodes(h.$('history-items')).filter(x=>x.attributes['aria-pressed']!==undefined);choices[1].onclick();
 assert.match(h.$('result-transcribe').value,/Résultat B/);assert.doesNotMatch(h.$('result-transcribe').value,/Résultat A/);
 await h.$('delete-audio').onclick();assert.equal(h.records.has('b'),false);assert.equal(h.records.has('a'),true);assert.equal(h.$('selected-panel').hidden,true);
 assert.equal(h.requests.length,0);
});

test('Same selected bytes use new models, results persist, and no chat session is requested',async()=>{
 const h=await harness();add(h);assert.equal(h.requests.length,0);await h.$('save-audio').onclick();
 await h.$('analyze-audio').onclick();h.$('transcription-model').value='wav2vec';h.$('mo-fr-model').value='yaaba';await h.$('analyze-audio').onclick();
 const asr=h.requests.filter(x=>x.url.endsWith('/transcribe'));assert.equal(asr.length,2);assert.equal(asr[0].body.audio_base64,asr[1].body.audio_base64);assert.equal(asr[1].body.model_id,'wav2vec');
 const translations=h.requests.filter(x=>x.url.endsWith('/translate'));assert.equal(translations[1].body.model_id,'yaaba');assert.equal(h.$('stage-count').textContent,'3');
 assert.equal([...h.records.values()][0].results.length,3);assert.equal(h.requests.some(x=>x.url.includes('/conversation/')),false);
});

test('Long Mac transcriptions are sent in bounded translation chunks',async()=>{
 const h=await harness({respond:async(url,body)=>url.endsWith('/transcribe')?{ok:true,text:'laafi '.repeat(180)}:url.endsWith('/translate')?{ok:true,text:'français'}:{draft:{}}});
 add(h);await h.$('analyze-audio').onclick();
 assert.equal(h.requests[0].body.model_id,'whisper');
 const chunks=h.requests.filter(x=>x.url.endsWith('/translate'));assert.ok(chunks.length>1);for(const call of chunks){assert.ok(call.body.text.length<=512);assert.equal(call.body.model_id,'nllb');}
});

test('Failure keeps the vocal and partial results; duplicate analyses are blocked',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);
 const h=await harness({respond:async()=>{await gate;return {ok:false,error:'Cloud indisponible'};}});add(h);
 const pending=h.$('analyze-audio').onclick();await settle();await h.$('analyze-audio').onclick();assert.equal(h.requests.length,1);release();await pending;
 assert.equal(h.$('selected-panel').hidden,false);assert.match(h.$('error-transcribe').textContent,/Cloud indisponible/);assert.equal(h.$('analyze-audio').disabled,false);
});

test('Rerun overwrites fixed text zones and clears downstream results immediately',async()=>{
 let run=0,fail=false,release;
 const h=await harness({respond:async(url)=>{
  if(url.endsWith('/transcribe')){run++;if(fail){await new Promise(resolve=>release=resolve);return {ok:false,error:'Échec transcription'};}return {ok:true,text:'Transcription '+run};}
  return url.endsWith('/translate')?{ok:true,text:'Traduction '+run}:{draft:{client:'Client '+run}};
 }});
 add(h);const output=h.$('result-transcribe');await h.$('analyze-audio').onclick();
 assert.equal(output.value,'Transcription 1');assert.equal(h.$('result-translate').value,'Traduction 1');
 await h.$('analyze-audio').onclick();assert.equal(h.$('result-transcribe'),output);assert.equal(output.value,'Transcription 2');assert.equal(h.$('stage-count').textContent,'3');
 fail=true;const pending=h.$('analyze-audio').onclick();await settle();
 assert.equal(output.value,'');assert.equal(h.$('result-translate').value,'');assert.equal(h.$('result-extract').value,'');
 release();await pending;assert.equal(h.$('result-translate').value,'');assert.match(h.$('error-transcribe').textContent,/Échec transcription/);
});


test('Play button plays and pauses selected audio, and changing selection stops playback',async()=>{
 const h=await harness({saved:[clip('a'),clip('b')]});
 await h.$('play-audio').onclick();assert.equal(h.$('input-audio').paused,false);assert.equal(h.$('play-audio').textContent,'⏸ Pause');
 await h.$('play-audio').onclick();assert.equal(h.$('input-audio').paused,true);assert.equal(h.$('play-audio').textContent,'▶ Écouter');
 await h.$('play-audio').onclick();nodes(h.$('history-items')).filter(x=>x.attributes['aria-pressed']!==undefined)[1].onclick();
 assert.equal(h.$('input-audio').paused,true);assert.equal(h.$('play-audio').textContent,'▶ Écouter');
});


test('Saved metadata uses authenticated audio URLs and downloads only the selected voice for analysis',async()=>{
 const h=await harness({saved:[{id:'remote-one',name:'Distant',createdAt:1,results:[]},{id:'remote-two',name:'Autre',createdAt:1,results:[]}]});
 assert.equal(h.downloads.length,0);assert.equal(h.$('input-audio').src,'/admin/api/experiments/remote-one/audio');
 const choices=nodes(h.$('history-items')).filter(x=>x.attributes['aria-pressed']!==undefined);choices[1].onclick();assert.equal(h.downloads.length,0);
 await h.$('analyze-audio').onclick();assert.deepEqual(h.downloads,['remote-two']);
});

const librarySource=fs.readFileSync(path.join(__dirname,'../src/bot_wumtaaba/static/check-library.js'),'utf8');
async function libraryHarness(respond){
 const calls=[],redirects=[];
 class Reader{readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result='data:audio/wav;base64,'+Buffer.from(bytes).toString('base64');this.onload();});}}
 const context=vm.createContext({window:{location:{assign:value=>redirects.push(value)}},Blob,FileReader:Reader,fetch:async(url,options)=>{calls.push({url,...options,body:options.body?JSON.parse(options.body):undefined});return respond(url,options);}});
 vm.runInContext(librarySource,context);return {library:context.window.AudioLibrary,calls,redirects};
}
const uuid='11111111-1111-4111-8111-111111111111';
const response=(value,status=200)=>({status,ok:status<400,json:async()=>value,blob:async()=>new Blob(['remote audio'],{type:'audio/wav'})});
test('Remote library saves bytes once, updates metadata/results without reupload, and deletes only its record',async()=>{
 const h=await libraryHarness(async()=>response({id:uuid}));const record={id:uuid,name:'Essai',blob:new Blob(['voice'],{type:'audio/wav'}),createdAt:1,results:[]};
 await h.library.save(record);assert.equal(h.calls[0].method,'POST');assert.equal(h.calls[0].body.audio_base64,Buffer.from('voice').toString('base64'));
 await h.library.save({...record,saved:true,results:[{task:'transcribe',output:'Texte'}]});assert.equal(h.calls[1].method,'PUT');assert.equal(h.calls[1].body.audio_base64,undefined);assert.equal(h.calls[1].body.results[0].output,'Texte');
 await h.library.remove(uuid);assert.equal(h.calls[2].method,'DELETE');assert.equal(h.calls[2].url,'/admin/api/experiments/'+uuid);
 for(const call of h.calls){assert.equal(call.credentials,'same-origin');assert.equal(call.cache,'no-store');}
});
test('Remote list is metadata-only; protected playback path never trusts a supplied external audio URL',async()=>{
 const h=await libraryHarness(async()=>response({items:[{id:uuid,name:'Essai',audio_url:'https://evil.example/private.wav'}]}));const items=await h.library.list();
 assert.equal(h.calls.length,1);assert.equal(h.library.audioUrl(items[0]),'/admin/api/experiments/'+uuid+'/audio');
 await h.library.audio(items[0]);assert.equal(h.calls.length,2);assert.equal(h.calls[1].url,'/admin/api/experiments/'+uuid+'/audio');
 assert.throws(()=>h.library.audioUrl({id:'../private'}),/Identifiant/);
});
test('Expired admin authentication redirects to login and failed saves remain errors',async()=>{
 const h=await libraryHarness(async()=>response({detail:'Connexion requise'},401));await assert.rejects(h.library.list(),/Connexion administrateur/);assert.equal(h.redirects[0],'/admin?next=check');
 const broken=await libraryHarness(async()=>response({detail:'Stockage indisponible'},503));await assert.rejects(broken.library.save({id:uuid,saved:true,name:'Essai'}),/Stockage indisponible/);
});


test('Cloud transcription-only clears old downstream zones and never calls translation or Gemini',async()=>{
 const h=await harness();add(h);await h.$('save-audio').onclick();await h.$('analyze-audio').onclick();
 assert.equal(h.$('stage-count').textContent,'3');
 h.requests.splice(0);h.$('provider').value='cloud';h.$('provider').onchange();
 assert.equal(h.$('transcription-model').value,'bia-transcription-v1');assert.equal(h.$('mo-fr-model').value,'');
 await h.$('analyze-audio').onclick();
 assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/admin/api/check/run/cloud/transcribe');
 assert.equal(h.requests[0].body.model,'bia-transcription-v1');assert.equal(h.requests[0].body.model_id,undefined);
 assert.equal(h.$('result-transcribe').value,'Mooré transcrit');assert.equal(h.$('result-translate').value,'');assert.equal(h.$('result-extract').value,'');
 assert.equal(h.$('stage-count').textContent,'1');assert.equal([...h.records.values()][0].results.length,1);
 assert.match(h.$('analysis-status').textContent,/Traduction cloud indisponible/);
});
