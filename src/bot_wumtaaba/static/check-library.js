'use strict';
(function(root){
 const base='/admin/api/experiments';
 const identifier=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 function path(record){
  if(!record||typeof record.id!=='string'||!identifier.test(record.id))throw Error('Identifiant audio invalide.');
  return base+'/'+encodeURIComponent(record.id);
 }
 function auth(response){
  if(response.status===401){root.location.assign('/admin?next=check');throw Error('Connexion administrateur requise.');}
 }
 async function request(url,method='GET',body){
  const response=await fetch(url,{method,credentials:'same-origin',cache:'no-store',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  auth(response);
  let value={};if(response.status!==204){try{value=await response.json();}catch{throw Error('Réponse du backoffice illisible. Réessayez.');}}
  if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'Impossible de mettre à jour les tests audio. Réessayez.');
  return value;
 }
 async function encoded(blob){
  if(!(blob instanceof Blob)||!blob.size||blob.size>7*1024*1024)throw Error('Choisissez un audio non vide de 7 Mo maximum.');
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Audio illisible.'));reader.readAsDataURL(blob);});
 }
 root.AudioLibrary={
  async list(){const result=await request(base);return result.items||[];},
  audioUrl(record){return path(record)+'/audio';},
  async audio(record){
   const response=await fetch(path(record)+'/audio',{credentials:'same-origin',cache:'no-store'});auth(response);
   if(!response.ok)throw Error('Impossible de charger cet audio. Réessayez.');
   const blob=await response.blob();
   if(!blob.size||blob.size>7*1024*1024)throw Error('Audio vide ou supérieur à 7 Mo.');
   return blob;
  },
  async save(record){
   const common={name:record.name,results:record.results||[]};
   const result=record.saved?await request(path(record),'PUT',common):await request(base,'POST',{...common,id:record.id,audio_base64:await encoded(record.blob),mime:record.blob.type||'audio/wav',createdAt:record.createdAt});
   return result.item||result;
  },
  async remove(id){await request(path({id}),'DELETE');}
 };
})(window);
