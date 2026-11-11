'use strict';
(function(){
 const pages=[['agreements','▤','Accords, échéances et rappels','/admin?view=agreements'],['conversations','◌','Conversations WhatsApp','/admin?view=conversations'],['annotations','✎','Annotation','/admin?view=annotations'],['check','▷','Wumtaaba Check','/admin/check'],['models','◇','Modèles IA','/admin?view=models']];
 const simulator=['simulator','◉','Simulateur WhatsApp','/admin/simulator'];
 function current(){if(location.pathname==='/admin/simulator')return 'simulator';if(location.pathname==='/admin/check')return 'check';const value=new URLSearchParams(location.search).get('view');return pages.some(p=>p[0]===value)?value:'agreements';}
 function mount(){for(const target of document.querySelectorAll('[data-admin-sidebar]')){
  target.replaceChildren();target.className='admin-sidebar';
  const brand=document.createElement('a');brand.className='admin-brand';brand.href='/admin';brand.textContent='w · Wuumtaaba';
  const note=document.createElement('p');note.className='admin-brand-note';note.textContent='ESPACE DE TRAVAIL';
  const nav=document.createElement('nav');nav.className='admin-navigation';nav.setAttribute('aria-label','Navigation principale');
  const environment=document.createElement('p');environment.className='admin-environment';environment.dataset.environment='';environment.textContent='Environnement';
  target.append(brand,note,nav,environment);
 }navigation(false);refresh();}
 function navigation(development){for(const target of document.querySelectorAll('[data-admin-sidebar]')){
  const nav=target.children[2];nav.replaceChildren();
  for(const [id,icon,label,url] of development?[...pages,simulator]:pages){const a=document.createElement('a');a.href=url;a.dataset.page=id;a.textContent=icon+'  '+label;if(id===current()){a.className='active';a.setAttribute('aria-current','page');}nav.append(a);}
 }}
 async function refresh(){try{const r=await fetch('/admin/api/environment',{credentials:'same-origin',cache:'no-store'});if(!r.ok){navigation(false);return;}const value=await r.json();const dev=value.environment==='development';navigation(dev);for(const el of document.querySelectorAll('[data-environment]')){el.textContent=dev?'DEV · Base locale':'PROD · Base de production';el.className='admin-environment '+(dev?'development':'production');}}catch{navigation(false);}}
 window.AdminShell={pages,current,refresh};mount();
})();
