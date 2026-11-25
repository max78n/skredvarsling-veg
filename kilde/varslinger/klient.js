/* SKRED – felles klient for arbeidsprosjekt og overføring mellom faner (snakker med menysiden) */
(function(){
  const R=window.SKRED={id:null,laas:false,liste:[],cb:[],mottak:{},vent:{}};
  R.on=f=>{R.cb.push(f);if(R.id!==null)try{f(R.id,R.laas)}catch(e){console.error(e)}};
  R.paMottak=(k,f)=>{R.mottak[k]=f;if(R.vent[k]){const d=R.vent[k];delete R.vent[k];f(d)}};
  R.send=(til,data)=>{try{parent.postMessage({type:'skred-overfor',til,data},'*')}catch(e){}};
  R.gaa=tab=>{try{parent.postMessage({type:'skred-gaa',tab},'*')}catch(e){}};
  R.prosj=id=>R.liste.find(p=>p.id===id)||null;
  /* finn <option> i en nedtrekksliste som svarer til prosjektet (verdi = id, eller tekst/verdi som starter med vegkoden) */
  R.finn=(sel,id)=>{const p=R.prosj(id);if(!sel||!p)return null;const o=[...sel.options].find(o=>o.value===id)||[...sel.options].find(o=>o.text===p.navn||o.value===p.navn)
      ||[...sel.options].find(o=>(o.text+' ').startsWith(p.kode+' ')||(o.value+' ').startsWith(p.kode+' '));return o||null};
  addEventListener('message',e=>{const d=e.data||{};
    if(d.type==='skred-prosjekt'){R.id=d.id||'';R.laas=!!d.id;R.liste=d.liste||R.liste;R.cb.forEach(f=>{try{f(R.id,R.laas)}catch(x){console.error(x)}})}
    if(d.type==='skred-overfor'){if(R.mottak[d.til])R.mottak[d.til](d.data);else R.vent[d.til]=d.data}});
  try{if(parent!==window)parent.postMessage({type:'skred-klar'},'*')}catch(e){}
})();
