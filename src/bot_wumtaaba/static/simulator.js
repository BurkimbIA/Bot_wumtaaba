'use strict';
const $=id=>document.getElementById(id);
let sessionId=null,busy=false,catalog=null,pendingAudio=null,lastAudio=null,inputUrl=null,recording=null,recordTimer=null,stageCount=0;
const outgoingUrls=[];
function node(tag,className,text){const n=document.createElement(tag);if(className)n.className=className;if(text!==undefined)n.textContent=String(text);return n;}
function currentModels(){return {provider:$('provider').value,transcription_model:$('transcription-model').value||null,translation_model:$('mo-fr-model').value||null};}
function controls(){document.querySelectorAll('[data-control]').forEach(el=>{el.disabled=busy||Boolean(recording);});$('record').disabled=busy;$('send').disabled=busy||Boolean(recording)||!sessionId;$('reset-session').disabled=busy||Boolean(recording)||!sessionId;$('discard-audio').disabled=busy||Boolean(recording)||!pendingAudio;$('reuse-audio').disabled=busy||Boolean(recording)||!lastAudio;$('compare-audio').disabled=busy||Boolean(recording)||!lastAudio;$('send').firstChild.textContent=pendingAudio?'Envoyer le vocal ':'Envoyer ';}
function setBusy(value){busy=value;controls();}
function showError(message){$('chat-error').textContent=message;$('chat-error').hidden=false;}
function hideError(){$('chat-error').hidden=true;$('chat-error').textContent='';}
function scrollChat(){$('chat').scrollTop=$('chat').scrollHeight;}
function richText(parent,text){const parts=String(text).split(/(\*[^*\n]+\*)/g);for(const part of parts){const strong=part.length>2&&part.startsWith('*')&&part.endsWith('*');parent.append(node(strong?'strong':'span','',strong?part.slice(1,-1):part));}}
function bubble(who,text){const item=node('article','bubble '+who),content=node('div','bubble-content');if(text)richText(content,text);item.append(content,node('div','bubble-meta',who==='user'?'Vous':'Wuumtaaba'));$('chat').append(item);return {item,content};}
function safeAudioUrl(value){if(typeof value!=='string')return null;const prefix='/admin/api/simulator/audio/'+sessionId+'/';return value.startsWith(prefix)&&!value.includes('..')&&!/[\s?#\\]/.test(value)?value:null;}
function appendReply(reply){
 const interactive=reply.interactive||{},text=reply.type==='text'?reply.text?.body:interactive.body?.text;
 const view=bubble('bot',text||'');
 if(reply.type==='audio'){
  const url=safeAudioUrl(reply.url||reply.audio?.url);
  if(url){const audio=node('audio');audio.controls=true;audio.src=url;view.content.append(audio);}else view.content.append(node('span','','Lecture audio indisponible pour cette réponse.'));
 }
 if(reply.type!=='interactive')return;
 const actions=node('div','reply-actions');
 function choice(id,title,description){if(typeof id!=='string'||typeof title!=='string')return;const button=node('button','',title);button.type='button';button.setAttribute('data-control','');if(description)button.append(node('small','',description));button.onclick=()=>sendTurn({action:id},title);actions.append(button);}
 if(interactive.type==='button'){for(const entry of interactive.action?.buttons||[]){const value=entry.reply||entry;choice(value.id,value.title);}}
 if(interactive.type==='list'){for(const section of interactive.action?.sections||[]){if(section.title)actions.append(node('p','reply-group',section.title));for(const row of section.rows||[])choice(row.id,row.title,row.description);}}
 if(actions.childNodes.length)view.content.append(actions);
}
function stageLabel(task){return {transcribe:'Transcription',transcription:'Transcription',translate:'Traduction',translation:'Traduction',extract:'Extraction de l’accord',extraction:'Extraction de l’accord',tts:'Lecture vocale',synthesize:'Lecture vocale'}[task]||task||'Résultat';}
function outputText(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
function appendStages(stages){for(const value of stages||[]){
 const card=node('section','stage'),header=node('div','stage-header',stageLabel(value.task));
 if(Number.isFinite(value.seconds))header.append(node('span','stage-time',value.seconds.toFixed(2)+' s'));card.append(header);
 if(value.model)card.append(node('p','stage-model',value.model));
 if(value.input!==undefined&&value.input!==null&&value.input!==''){card.append(node('p','stage-label','Entrée'),node('pre','stage-input',outputText(value.input)));}
 if(value.output!==undefined&&value.output!==null){card.append(node('p','stage-label','Résultat'),node('pre','stage-output',outputText(value.output)));}
 if(value.error)card.append(node('p','stage-error',value.error));
 $('stages').append(card);stageCount++;
 }$('stage-count').textContent=String(stageCount);$('stages-empty').hidden=stageCount>0;}
function showSession(data){
 sessionId=data.session_id||sessionId;
 const count=Array.isArray(data.agreements)?data.agreements.length:0;
 $('session-ledger').textContent=count?count+' accord'+(count>1?'s':'')+' enregistré'+(count>1?'s':'')+' dans le carnet de cet essai.':'Aucun accord de test enregistré.';
 const engine=data.engine||{};$('engine-info').textContent=engine.name?'Parcours : '+engine.name+(engine.revision?' · '+String(engine.revision).slice(0,12):''):'';
 for(const reply of data.replies||[])appendReply(reply);appendStages(data.stages);scrollChat();
}
async function post(path,body){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 let data;try{data=await response.json();}catch{throw Error('Le laboratoire ne répond pas correctement. Relancez-le puis réessayez.');}
 if(!response.ok||data.ok===false){const detail=typeof data.detail==='string'?data.detail:data.error;const error=Error(response.status===410?'Cette session a expiré. Rechargez la page pour ouvrir un nouvel essai.':detail||'Le message n’a pas pu être traité. Réessayez.');error.stages=data.stages;throw error;}
 return data;
}
async function audioBase64(blob){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Le fichier audio est illisible.'));reader.readAsDataURL(blob);});}
function clearPendingAudio(){pendingAudio=null;if(inputUrl)URL.revokeObjectURL(inputUrl);inputUrl=null;$('input-audio').removeAttribute('src');$('audio-preview').hidden=true;$('audio-file').value='';controls();}
function selectAudio(blob,name){if(inputUrl)URL.revokeObjectURL(inputUrl);pendingAudio={blob,name};lastAudio=pendingAudio;inputUrl=URL.createObjectURL(blob);$('input-audio').src=inputUrl;$('audio-name').textContent=name;$('audio-preview').hidden=false;controls();}
async function sendTurn(message,label){
 if(busy||recording||!sessionId)return;
 const settings=currentModels();setBusy(true);hideError();$('chat-status').textContent='Wuumtaaba prépare sa réponse…';
 const textBefore=$('message').value,audioBefore=pendingAudio;
 try{
  const payload={session_id:sessionId,...message,...settings};
  if(message.audio){payload.audio_base64=await audioBase64(message.audio.blob);payload.filename=message.audio.name;payload.mime=message.audio.blob.type||'audio/wav';delete payload.audio;}
  const result=await post('/admin/api/simulator/turn',payload);
  const sent=bubble('user',label||message.text||'Vocal envoyé');
  if(message.audio){const url=URL.createObjectURL(message.audio.blob);outgoingUrls.push(url);const player=node('audio');player.controls=true;player.src=url;sent.content.append(player);}
  showSession(result);
  if(message.text!==undefined&&$('message').value===textBefore)$('message').value='';
  if(message.audio&&pendingAudio===audioBefore)clearPendingAudio();
  $('chat-status').textContent='À vous de répondre.';
 }catch(error){appendStages(error.stages);showError(error.message);$('chat-status').textContent='Votre message reste disponible pour réessayer.';}
 finally{setBusy(false);scrollChat();}
}

$('composer').onsubmit=event=>{event.preventDefault();if(pendingAudio)return sendTurn({audio:pendingAudio},'Message vocal');const text=$('message').value.trim();if(text)return sendTurn({text},text);};
$('message').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('composer').requestSubmit();}};
$('discard-audio').onclick=clearPendingAudio;
$('compare-audio').onclick=async()=>{
 if(!lastAudio||busy||recording)return;
 const audio=lastAudio,settings=currentModels();setBusy(true);hideError();
 $('chat-status').textContent='Comparaison du même vocal en cours…';
 try{
  const encoded=await audioBase64(audio.blob);
  const transcription=await post('/admin/api/check/run/'+settings.provider+'/transcribe',{audio_base64:encoded,...(settings.provider==='mac'?{model_id:settings.transcription_model}:{model:settings.transcription_model})});
  const asrStage={task:'transcribe',model:transcription.model||settings.transcription_model,input:'Comparaison · '+audio.name,output:transcription.text,seconds:transcription.seconds};appendStages([asrStage]);
  if(!transcription.text?.trim())throw Error('La transcription est vide. Essayez un autre modèle.');
  const translation=await post('/admin/api/check/run/'+settings.provider+'/translate',{text:transcription.text,source:'moore',...(settings.provider==='mac'?{model_id:settings.translation_model}:{model:settings.translation_model})});
  const translationStage={task:'translate',model:translation.model||settings.translation_model,input:transcription.text,output:translation.text,seconds:translation.seconds};appendStages([translationStage]);
  $('chat-status').textContent='Comparaison terminée. La conversation est inchangée.';
 }catch(error){showError(error.message);$('chat-status').textContent='Vocal conservé : vous pouvez relancer la comparaison.';}
 finally{setBusy(false);}
};
$('reuse-audio').onclick=()=>{if(!lastAudio||busy||recording)return;selectAudio(lastAudio.blob,lastAudio.name);$('record-status').textContent='Dernier vocal repris. Choisissez les modèles puis envoyez-le à nouveau.';};
$('audio-file').onchange=()=>{const file=$('audio-file').files?.[0];if(!file)return;if(!file.size||file.size>7*1024*1024){showError('Choisissez un audio non vide de 7 Mo maximum.');$('audio-file').value='';return;}if(file.type&&!file.type.startsWith('audio/')){showError('Choisissez un fichier audio.');return;}hideError();selectAudio(file,file.name);$('record-status').textContent='Audio prêt. Son envoi utilise les modèles sélectionnés.';};
document.querySelectorAll('[data-command]').forEach(button=>{button.onclick=()=>sendTurn({text:button.dataset.command},button.dataset.command);});
$('reset-session').onclick=async()=>{if(busy||recording||!sessionId)return;setBusy(true);hideError();try{const result=await post('/admin/api/simulator/reset',{session_id:sessionId});$('chat').replaceChildren();$('stages').replaceChildren();stageCount=0;outgoingUrls.splice(0).forEach(url=>URL.revokeObjectURL(url));clearPendingAudio();lastAudio=null;$('message').value='';showSession(result);$('chat-status').textContent='Nouvel essai prêt.';}catch(error){showError(error.message);}finally{setBusy(false);}};
function fillModels(id,items){const select=$(id),old=select.value;select.replaceChildren();for(const value of items){const option=node('option','',value.label+(value.available?'':' · non installé'));option.value=value.id;option.disabled=!value.available;select.append(option);}select.value=items.some(x=>x.id===old&&x.available)?old:(items.find(x=>x.default&&x.available)||items.find(x=>x.available))?.id||'';}
function updateModels(){if($('provider').value==='cloud'){fillModels('transcription-model',[{id:'bia-transcription-v1',label:'Whisper BurkimbIA · Cloud',available:true}]);fillModels('mo-fr-model',[{id:'bia-translation-v1-fast',label:'NLLB 600M · Cloud',available:true},{id:'bia-translation-v2',label:'Mistral 7B · Cloud',available:true}]);$('models-note').textContent='Les appels cloud utilisent les services configurés et peuvent être facturés.';}else{fillModels('transcription-model',catalog?.transcription||[]);fillModels('mo-fr-model',catalog?.translation||[]);$('models-note').textContent='Le prochain vocal mooré utilise ces modèles. Le français est traité par Gemini, comme dans WhatsApp. Aucun modèle n’est téléchargé automatiquement.';}}
$('provider').onchange=updateModels;
async function initialize(){setBusy(true);hideError();try{showSession(await post('/admin/api/simulator/session',{}));$('chat-status').textContent='Choisissez une action ou écrivez votre message.';}catch(error){showError(error.message);$('chat-status').textContent='Conversation indisponible.';}finally{setBusy(false);}}
async function loadModels(){try{const response=await fetch('/admin/api/check/models');if(!response.ok)throw Error();catalog=await response.json();updateModels();}catch{$('models-note').textContent='Catalogue indisponible : relancez le laboratoire pour choisir les modèles locaux.';}}
async function availability(){try{const response=await fetch('/admin/api/check/status');if(!response.ok)throw Error();const data=await response.json();$('availability').textContent='Mac '+(data.mac?.status==='ok'?'en ligne':'hors ligne')+' · Cloud '+(data.cloud_configured?'configuré':'non configuré');}catch{$('availability').textContent='État des modèles indisponible.';}}
function wav16(buffer){const samples=buffer.getChannelData(0),data=new ArrayBuffer(44+samples.length*2),view=new DataView(data);const write=(at,text)=>{for(let i=0;i<text.length;i++)view.setUint8(at+i,text.charCodeAt(i));};write(0,'RIFF');view.setUint32(4,36+samples.length*2,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,samples.length*2,true);for(let i=0;i<samples.length;i++){const n=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,n<0?n*32768:n*32767,true);}return new Blob([data],{type:'audio/wav'});}
function stopRecording(){if(recording?.state==='recording'){recording.state='stopping';clearInterval(recordTimer);recording.capture.port.postMessage({stop:true});$('record').disabled=true;$('record-status').textContent='Préparation du vocal…';}}
$('record').onclick=async()=>{
 if(recording){stopRecording();return;}if(busy)return;hideError();let stream,context,source,capture,finished=false;
 const finish=async()=>{if(finished)return;finished=true;clearInterval(recordTimer);stream?.getTracks().forEach(track=>track.stop());source?.disconnect();capture?.disconnect();if(context)await context.close();recording=null;controls();$('record').textContent='● Vocal';};
 try{
  if(!navigator.mediaDevices?.getUserMedia||typeof AudioContext==='undefined'||typeof AudioWorkletNode==='undefined')throw Error('Le micro n’est pas disponible. Vous pouvez joindre un fichier audio.');
  recording={state:'starting'};controls();$('record').disabled=true;$('record-status').textContent='Autorisez le microphone pour enregistrer ce vocal…';
  stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1}});context=new AudioContext();await context.resume();await context.audioWorklet.addModule('/admin/simulator/assets/recording-worklet.js');
  const chunks=[];let sampleCount=0;capture=new AudioWorkletNode(context,'pcm-recorder');
  capture.port.onmessage=async({data})=>{
   if(data.samples?.length){chunks.push(data.samples);sampleCount+=data.samples.length;}if(!data.done)return;
   try{const duration=Math.min(60,sampleCount/context.sampleRate);if(duration<.2)throw Error('Vocal trop court. Réessayez.');const length=Math.min(sampleCount,Math.floor(60*context.sampleRate)),samples=new Float32Array(length);let offset=0;for(const chunk of chunks){const count=Math.min(chunk.length,length-offset);if(count<=0)break;samples.set(chunk.subarray(0,count),offset);offset+=count;}const buffer=context.createBuffer(1,length,context.sampleRate);buffer.copyToChannel(samples,0);const offline=new OfflineAudioContext(1,Math.ceil(duration*16000),16000),playback=offline.createBufferSource();playback.buffer=buffer;playback.connect(offline.destination);playback.start();selectAudio(wav16(await offline.startRendering()),'Vocal · '+duration.toFixed(1)+' s');$('record-status').textContent='Vocal prêt à écouter et envoyer.';}catch(error){showError(error.message);$('record-status').textContent='Aucun nouveau vocal envoyé.';}finally{await finish();}
  };
  capture.onprocessorerror=async()=>{await finish();showError('L’enregistrement a été interrompu. Réessayez ou joignez un audio.');};
  source=context.createMediaStreamSource(stream);source.connect(capture);capture.connect(context.destination);recording={state:'recording',stream,capture};controls();$('record').textContent='■ Arrêter';const started=performance.now();$('record-status').textContent='Enregistrement · 0 / 60 s';recordTimer=setInterval(()=>{const elapsed=(performance.now()-started)/1000;$('record-status').textContent='Enregistrement · '+Math.floor(elapsed)+' / 60 s';if(elapsed>=60)stopRecording();},200);
 }catch(error){await finish();showError(error.name==='NotAllowedError'?'Microphone refusé. Vous pouvez joindre un fichier audio ou autoriser le micro dans les réglages du site.':error.message);$('record-status').textContent='Aucun nouveau vocal envoyé.';}
};
window.addEventListener('pagehide',()=>{recording?.stream?.getTracks().forEach(track=>track.stop());if(inputUrl)URL.revokeObjectURL(inputUrl);outgoingUrls.forEach(url=>URL.revokeObjectURL(url));});
initialize();loadModels();availability();
