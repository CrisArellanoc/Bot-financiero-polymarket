const assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{test}=require('node:test')
function fixture(){
 class Element{
  constructor(){this.children=[];this.listeners={};this.attributes={};this.open=false;this.opens=0;this.textContent=''}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=nodes}
  setAttribute(k,v){this.attributes[k]=String(v)}
  addEventListener(k,v){this.listeners[k]=v}
  showModal(){this.open=true;this.opens++}
  close(){this.open=false;this.listeners.close?.()}
  focus(){this.focused=true}
 }
 const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)}
 const node=(tag,text,cls)=>{const e=new Element();e.tag=tag;e.textContent=text??'';e.className=cls;return e}
 const document={hidden:false},html=readFileSync(path.join(__dirname,'../monitor.html'),'utf8')
 const start=html.indexOf('class BetPanel {'),end=html.indexOf('const betPanel =',start)
 const Panel=vm.runInNewContext(html.slice(start,end)+';BetPanel',{el,node,document,set:(id,text)=>el(id).textContent=text,money:n=>Number(n).toFixed(2)+' USD',local:String,encodeURIComponent})
 const flat=e=>e.textContent+' '+e.children.map(flat).join(' ')
 return {panel:new Panel(),el,document,flat}
}
const state=()=>({summary:{cash:100},portfolio:{initial:100,capital_realized:100,next_stake:3},bet_attempts:[]})
test('La pestaña muestra el 3% y una ejecución se confirma por el recibo, no por la decisión',()=>{
 const f=fixture(),s=state();f.panel.update(s)
 assert.equal(f.el('nextStake').textContent,'3.00 USD')
 assert.equal(f.el('betDrawer').opens,0)
 s.bet_attempts=[{id:1,start:3000,at:3002,status:'pending',capital:100,stake:3,outcome:'Up',reason:'Validando'}]
 f.panel.update(s)
 assert.equal(f.el('betDrawer').opens,1)
 assert.match(f.flat(f.el('betAttempts')),/Ejecución sin confirmar/)
 s.bet_attempts[0]={...s.bet_attempts[0],status:'filled_paper',actual_cost:'3',reason:'Registrada',evidence_hash:'abc',audit_hash:'def'}
 f.panel.update(s)
 assert.match(f.flat(f.el('betAttempts')),/Apuesta simulada ejecutada/)
 assert.match(f.flat(f.el('betAttempts')),/Costo confirmado\s+3.00 USD/)
 assert.match(f.flat(f.el('betAttempts')),/abc/)
 assert.equal(f.el('betTab').attributes['data-status'],'executed')
 f.el('closeBetDrawer').listeners.click()
 assert.equal(f.el('betDrawer').open,false)
 assert.equal(f.el('betTab').attributes['aria-expanded'],'false')
 assert.equal(f.el('betTab').focused,true)
 f.panel.update(s)
 assert.equal(f.el('betDrawer').opens,1)
})
test('Un rechazo conserva su motivo y no presenta costo como apuesta hecha',()=>{
 const f=fixture(),s=state();s.bet_attempts=[{id:1,start:3000,at:3002,status:'rejected',capital:100,stake:3,actual_cost:0,reason:'Importe inferior al mínimo',outcome:'Down'}]
 f.panel.update(s)
 assert.equal(f.el('betDrawer').opens,0)
 assert.match(f.flat(f.el('betAttempts')),/No se realizó la apuesta/)
 assert.match(f.flat(f.el('betAttempts')),/Importe inferior al mínimo/)
 assert.match(f.flat(f.el('betAttempts')),/Costo confirmado\s+—/)
 f.el('betTab').listeners.click()
 assert.equal(f.el('betDrawer').open,true)
})
