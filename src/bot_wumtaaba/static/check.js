'use strict';
const $=id=>document.getElementById(id);
let busy=false,catalog=null,selected=null,inputUrl=null,recording=null,recordTimer=null;
let audios=[];
function node(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function showError(text){$('chat-error').textContent=text;$('chat-error').hidden=false;}
function hideError(){$('chat-error').hidden=true;}
function controls(){
 document.querySelectorAll('[data-control]').forEach(el=>{el.disabled=busy||Boolean(recording);});
 $('record').disabled=busy||Boolean(recording&&recording.state!=='recording');
 for(const id of ['save-audio','delete-audio','analyze-audio','audio-name'])$(id).disabled=busy||Boolean(recording)||!selected;
}
function setBusy(value){busy=value;controls();}
function currentModels(){return {provider:$('provider').value,transcription_model:$('transcription-model').value,translation_model:$('mo-fr-model').value};}
async function post(path,body){
 const response=await fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(response.status===401){window.location.assign('/admin?next=check');throw Error('Connexion administrateur requise.');}
 let data;try{data=await response.json();}catch{throw Error('Réponse du laboratoire illisible. Réessayez.');}
 if(!response.ok||data.ok===false)throw Error(typeof data.detail==='string'?data.detail:data.error||'Analyse impossible. Réessayez.');
 return data;
}
function renderResults(){
 const stages=selected?.results||[];
 $('stage-count').textContent=String(stages.filter(stage=>stage.output!==undefined).length);
 for(const task of ['transcribe','translate','extract']){
  const stage=stages.filter(item=>item.task===task).at(-1);
  $('result-'+task).value=stage?.output===undefined?'':typeof stage.output==='string'?stage.output:JSON.stringify(stage.output,null,2);
  $('model-'+task).textContent=stage?[stage.model,stage.provider==='cloud'?'Cloud':stage.provider==='mac'?'Mac':null,stage.seconds!==undefined?stage.seconds+' s':null].filter(Boolean).join(' · '):'En attente d’analyse';
  $('error-'+task).textContent=stage?.error||'';
  $('error-'+task).hidden=!stage?.error;
 }
}
function renderHistory(){
 $('history-items').replaceChildren();$('history-count').textContent=String(audios.length);
 if(!audios.length)$('history-items').append(node('p','empty-history','Aucun audio. Enregistrez un vocal ou joignez un fichier pour commencer.'));
 for(const audio of audios){
  const row=node('div','history-row'+(selected===audio?' selected':''));
  const choose=node('button','audio-choice');choose.type='button';choose.setAttribute('data-control','');choose.setAttribute('aria-pressed',String(selected===audio));
  choose.append(node('strong','',audio.name),node('small','',new Date(audio.createdAt).toLocaleString('fr-FR')+' · '+(audio.saved?'Enregistré':'Non enregistré')+' · '+(audio.results?.length||0)+' résultats'));
  choose.onclick=()=>{if(!busy&&!recording)activate(audio);};
  const remove=node('button','delete-row','Supprimer');remove.type='button';remove.setAttribute('data-control','');remove.setAttribute('aria-label','Supprimer '+audio.name);remove.onclick=()=>deleteAudio(audio);
  row.append(choose,remove);$('history-items').append(row);
 }controls();
}
function activate(audio){
 $('input-audio').pause();$('play-audio').textContent='▶ Écouter';
 if(inputUrl)URL.revokeObjectURL(inputUrl);inputUrl=null;selected=audio;
 $('selected-empty').hidden=Boolean(audio);$('selected-panel').hidden=!audio;
 if(audio){if(audio.blob)inputUrl=URL.createObjectURL(audio.blob);$('input-audio').src=inputUrl||window.AudioLibrary.audioUrl(audio);$('audio-name').value=audio.name;}
 else{$('input-audio').removeAttribute('src');$('audio-name').value='';}
 $('analysis-status').textContent=audio?'Audio sélectionné. Choisissez les modèles puis lancez l’analyse.':'';
 renderResults();renderHistory();
}
function selectAudio(blob,name){
 if(!blob.size||blob.size>7*1024*1024)throw Error('Choisissez un audio non vide de 7 Mo maximum.');
 if(blob.type&&!blob.type.startsWith('audio/'))throw Error('Choisissez un fichier audio.');
 if(audios.length>=100)throw Error('100 audios maximum. Supprimez un audio avant d’en ajouter un.');
 const audio={id:crypto.randomUUID(),name,blob,createdAt:Date.now(),results:[],saved:false};audios.unshift(audio);activate(audio);hideError();
}
async function saveAudio(audio){
 const record=await window.AudioLibrary.save({id:audio.id,name:audio.name,blob:audio.blob,results:audio.results,createdAt:audio.createdAt,saved:audio.saved});audio.id=record.id;audio.saved=true;renderHistory();
}
async function deleteAudio(audio){
 if(!audio||busy||recording)return;setBusy(true);hideError();
 try{if(audio.saved)await window.AudioLibrary.remove(audio.id);audios=audios.filter(item=>item!==audio);if(selected===audio)activate(null);else renderHistory();$('analysis-status').textContent='Audio supprimé.';}
 catch(error){showError(error.message);}finally{setBusy(false);}
}
$('play-audio').onclick=async()=>{
 if(!selected)return;
 const player=$('input-audio');
 try{if(player.paused)await player.play();else player.pause();}
 catch{showError('Lecture impossible pour cet audio. Essayez un fichier WAV ou MP3.');}
};
$('input-audio').onplay=()=>{$('play-audio').textContent='⏸ Pause';$('play-audio').setAttribute('aria-label','Mettre l’audio en pause');};
$('input-audio').onpause=$('input-audio').onended=()=>{$('play-audio').textContent='▶ Écouter';$('play-audio').setAttribute('aria-label','Lire l’audio sélectionné');};
$('audio-name').oninput=()=>{if(selected){selected.name=$('audio-name').value.trim().slice(0,200)||'Vocal';renderHistory();}};
$('audio-file').onchange=()=>{const file=$('audio-file').files?.[0];if(!file||busy||recording)return;try{selectAudio(file,file.name);$('record-status').textContent='Audio ajouté. Enregistrez-le pour le conserver après rechargement.';}catch(error){showError(error.message);}finally{$('audio-file').value='';}};
$('save-audio').onclick=async()=>{if(!selected||busy||recording)return;setBusy(true);hideError();try{await saveAudio(selected);$('analysis-status').textContent='Audio et résultats enregistrés dans Supabase.';}catch(error){showError(error.message);}finally{setBusy(false);}};
$('delete-audio').onclick=()=>deleteAudio(selected);
async function audioBase64(blob){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Audio illisible.'));reader.readAsDataURL(blob);});}
function splitText(text,limit=512){
 const parts=[];let rest=text.trim();while(rest.length>limit){let cut=rest.lastIndexOf(' ',limit);if(cut<limit/2)cut=limit;parts.push(rest.slice(0,cut));rest=rest.slice(cut).trim();}if(rest)parts.push(rest);return parts;
}
$('analyze-audio').onclick=async()=>{
 if(!selected||busy||recording)return;
 const audio=selected,settings=currentModels(),at=Date.now();setBusy(true);hideError();audio.results=[];renderResults();$('analysis-status').textContent='Transcription en cours…';
 async function stage(task,model,input,work){
  const result={task,model,provider:settings.provider,at,input};audio.results.push(result);renderResults();const start=performance.now();
  try{const data=await work();result.model=data.model||model;result.output=data.text??data;return data;}
  catch(error){result.error=error.message;throw error;}
  finally{result.seconds=Number(((performance.now()-start)/1000).toFixed(2));renderResults();renderHistory();}
 }
 try{
  if(!settings.transcription_model||(settings.provider==='mac'&&!settings.translation_model))throw Error('Sélectionnez les modèles disponibles.');
  const encoded=await audioBase64(audio.blob||await window.AudioLibrary.audio(audio));
  const transcription=await stage('transcribe',settings.transcription_model,audio.name,()=>post('/admin/api/check/run/'+settings.provider+'/transcribe',{audio_base64:encoded,...(settings.provider==='mac'?{model_id:settings.transcription_model}:{model:settings.transcription_model})}));
  if(!transcription.text?.trim())throw Error('Transcription vide. Essayez un autre modèle.');
  if(!settings.translation_model){$('analysis-status').textContent='Transcription terminée avec RunPod. Traduction cloud indisponible ; choisissez le Mac pour l’analyse complète.';return;}
  $('analysis-status').textContent='Traduction en cours…';
  const translation=await stage('translate',settings.translation_model,transcription.text,async()=>{
   const parts=[];for(const text of splitText(transcription.text)){const data=await post('/admin/api/check/run/'+settings.provider+'/translate',{text,source:'moore',...(settings.provider==='mac'?{model_id:settings.translation_model}:{model:settings.translation_model})});if(!data.text?.trim())throw Error('Traduction vide.');parts.push(data.text);}return {text:parts.join('\n')};
  });
  $('analysis-status').textContent='Extraction des détails en cours…';
  await stage('extract','Gemini',translation.text,()=>post('/admin/api/check/extract',{text:translation.text}));
  $('analysis-status').textContent='Analyse terminée. Changez les modèles pour comparer le même audio.';
 }catch(error){showError(error.message);$('analysis-status').textContent='Analyse interrompue. L’audio et les résultats obtenus sont conservés.';}
 finally{
  if(audio.saved){try{await saveAudio(audio);}catch(error){showError('Résultats non enregistrés : '+error.message);}}
  setBusy(false);
 }
};

function fillModels(id,items){const select=$(id),old=select.value;select.replaceChildren();for(const value of items){const option=node('option','',value.label+(value.available?'':' · indisponible'));option.value=value.id;option.disabled=!value.available;select.append(option);}select.value=items.some(x=>x.id===old&&x.available)?old:(items.find(x=>x.default&&x.available)||items.find(x=>x.available))?.id||'';}
function updateModels(){
 if($('provider').value==='cloud'){
  fillModels('transcription-model',catalog?.cloud_transcription||[]);
  fillModels('mo-fr-model',catalog?.cloud_translation||[]);
  if(!$('mo-fr-model').value){const option=node('option','','Aucune traduction RunPod activée');option.value='';$('mo-fr-model').append(option);}
  $('models-note').textContent=catalog?.cloud_note||'Les appels cloud utilisent directement RunPod et vos crédits.';
 }else{
  fillModels('transcription-model',catalog?.transcription||[]);fillModels('mo-fr-model',catalog?.translation||[]);
  $('models-note').textContent='Transcription mooré, traduction française puis extraction des détails avec Gemini. Aucun modèle téléchargé automatiquement.';
 }
}
$('provider').onchange=updateModels;
async function loadModels(){try{const response=await fetch('/admin/api/check/models');if(response.status===401){window.location.assign('/admin?next=check');return;}if(!response.ok)throw Error();catalog=await response.json();updateModels();}catch{$('models-note').textContent='Catalogue indisponible. Actualisez la page pour réessayer.';}}
async function availability(){try{const response=await fetch('/admin/api/check/status');if(!response.ok)throw Error();const data=await response.json();$('availability').textContent='Mac '+(data.mac?.status==='ok'?'en ligne':'hors ligne')+' · Cloud '+(data.cloud_configured?'configuré':'non configuré');}catch{$('availability').textContent='État des modèles indisponible.';}}

function wav16(buffer){const samples=buffer.getChannelData(0),data=new ArrayBuffer(44+samples.length*2),view=new DataView(data);const write=(at,text)=>{for(let i=0;i<text.length;i++)view.setUint8(at+i,text.charCodeAt(i));};write(0,'RIFF');view.setUint32(4,36+samples.length*2,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,samples.length*2,true);for(let i=0;i<samples.length;i++){const n=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,n<0?n*32768:n*32767,true);}return new Blob([data],{type:'audio/wav'});}
function stopRecording(){if(recording?.state==='recording'){recording.state='stopping';clearInterval(recordTimer);recording.capture.port.postMessage({stop:true});$('record').disabled=true;$('record-status').textContent='Préparation du vocal…';}}
$('record').onclick=async()=>{
 if(recording){stopRecording();return;}if(busy)return;hideError();let stream,context,source,capture,finished=false;
 const finish=async()=>{if(finished)return;finished=true;clearInterval(recordTimer);stream?.getTracks().forEach(track=>track.stop());source?.disconnect();capture?.disconnect();if(context)await context.close();recording=null;controls();$('record').textContent='● Vocal';};
 try{
  if(!navigator.mediaDevices?.getUserMedia||typeof AudioContext==='undefined'||typeof AudioWorkletNode==='undefined')throw Error('Le micro n’est pas disponible. Vous pouvez joindre un fichier audio.');
  recording={state:'starting'};controls();$('record').disabled=true;$('record-status').textContent='Autorisez le microphone pour enregistrer ce vocal…';
  stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1}});context=new AudioContext();await context.resume();await context.audioWorklet.addModule('/admin/check/assets/check-recording-worklet.js');
  const chunks=[];let sampleCount=0;capture=new AudioWorkletNode(context,'pcm-recorder');
  capture.port.onmessage=async({data})=>{
   if(data.samples?.length){chunks.push(data.samples);sampleCount+=data.samples.length;}if(!data.done)return;
   try{const duration=Math.min(60,sampleCount/context.sampleRate);if(duration<.2)throw Error('Vocal trop court. Réessayez.');const length=Math.min(sampleCount,Math.floor(60*context.sampleRate)),samples=new Float32Array(length);let offset=0;for(const chunk of chunks){const count=Math.min(chunk.length,length-offset);if(count<=0)break;samples.set(chunk.subarray(0,count),offset);offset+=count;}const buffer=context.createBuffer(1,length,context.sampleRate);buffer.copyToChannel(samples,0);const offline=new OfflineAudioContext(1,Math.ceil(duration*16000),16000),playback=offline.createBufferSource();playback.buffer=buffer;playback.connect(offline.destination);playback.start();selectAudio(wav16(await offline.startRendering()),'Vocal · '+duration.toFixed(1)+' s');$('record-status').textContent='Vocal ajouté. Sélectionnez les modèles pour l’analyser.';}catch(error){showError(error.message);$('record-status').textContent='Aucun nouveau vocal envoyé.';}finally{await finish();}
  };
  capture.onprocessorerror=async()=>{await finish();showError('L’enregistrement a été interrompu. Réessayez ou joignez un audio.');};
  source=context.createMediaStreamSource(stream);source.connect(capture);capture.connect(context.destination);recording={state:'recording',stream,capture};controls();$('record').textContent='■ Arrêter';const started=performance.now();$('record-status').textContent='Enregistrement · 0 / 60 s';recordTimer=setInterval(()=>{const elapsed=(performance.now()-started)/1000;$('record-status').textContent='Enregistrement · '+Math.floor(elapsed)+' / 60 s';if(elapsed>=60)stopRecording();},200);
 }catch(error){await finish();showError(error.name==='NotAllowedError'?'Microphone refusé. Vous pouvez joindre un fichier audio ou autoriser le micro dans les réglages du site.':error.message);$('record-status').textContent='Aucun nouveau vocal envoyé.';}
};

window.addEventListener('pagehide',()=>{recording?.stream?.getTracks().forEach(track=>track.stop());if(inputUrl)URL.revokeObjectURL(inputUrl);});
async function initialize(){setBusy(true);try{audios=(await window.AudioLibrary.list()).map(item=>{const results=item.results||[],last=results.at(-1);return {...item,saved:true,results:last?.at?results.filter(stage=>stage.at===last.at):results.slice(-3)};});activate(audios[0]||null);}catch(error){showError(error.message);activate(null);}finally{setBusy(false);}}
initialize();loadModels();availability();
