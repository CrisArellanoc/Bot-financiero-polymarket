const assert = require("node:assert/strict")
const { readFileSync } = require("node:fs")
const path = require("node:path")
const { test } = require("node:test")
const vm = require("node:vm")

function chartFixture(reducedMotion = false, usd = false) {
  class Element {
    constructor() { this.attributes = {}; this.children = []; this.style = {}; this.listeners = {};
      this.classList={toggle(){},remove(){},add(){}} }
    setAttribute(key,value) { this.attributes[key] = String(value) }
    append(...children) { this.children.push(...children) }
    replaceChildren(...children) { this.children = children }
    getBoundingClientRect() { return {width:640} }
    addEventListener(name,handler) { this.listeners[name]=handler }
  }
  const frames = new Map()
  let id = 0
  const elements=new Map(),timeouts=new Map(),listeners={}
  const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)}
  const document = {hidden:false,createElementNS:()=>new Element(),addEventListener(name,fn){listeners[name]=fn}}
  const sandbox = {document,Date,performance:{now:()=>0},el,local:t=>String(t),
    node:(tag,text)=>{const n=new Element();n.textContent=text;return n},set:(id,text)=>{el(id).textContent=text},
    setTimeout:fn=>{timeouts.set(++id,fn);return id},clearTimeout:id=>timeouts.delete(id),
    window:{matchMedia:()=>({matches:reducedMotion})},
    ResizeObserver:class { observe() {} },
    requestAnimationFrame:cb=>{frames.set(++id,cb);return id},
    cancelAnimationFrame:id=>frames.delete(id)}
  const html = readFileSync(path.join(__dirname,"../monitor.html"),"utf8")
  const start = html.indexOf("class MarketChart")
  const end = html.indexOf("const el =",start)
  const {Chart,Flow} = vm.runInNewContext(html.slice(start,end)+"; ({Chart:MarketChart,Flow:FlowMap})",sandbox)
  return {chart:new Chart(new Element(),usd),flow:()=>new Flow(new Element()),frames,document,el,listeners,timeouts,
    step(now) { const callbacks=[...frames.values()];frames.clear();callbacks.forEach(cb=>cb(now)) }}
}
const state = (prices,focus=3000) => ({focus,current:focus,prices,laya:{ready:focus+12,p:.65}})

test("La curva conserva extremos y huecos y la predicción empieza cuando está disponible",()=>{
  const {chart}=chartFixture()
  const input=state([{seen:3002,price:.4},{seen:3004,price:.8},{seen:3040,price:.2}])
  const original=JSON.stringify(input)
  chart.update(input)
  assert.equal((chart.line.attributes.d.match(/M /g)||[]).length,2)
  assert.equal((chart.line.attributes.d.match(/C /g)||[]).length,1)
  const values=chart.line.attributes.d.split("C ")[1].split(" M")[0].match(/[\d.]+/g).map(Number)
  const y0=bottom(.4), y1=bottom(.8)
  for(let i=0;i<=20;i++){
    const t=i/20
    const y=(1-t)**3*y0+3*(1-t)**2*t*values[1]+3*(1-t)*t*t*values[3]+t**3*values[5]
    assert.ok(y>=Math.min(y0,y1)-1e-8 && y<=Math.max(y0,y1)+1e-8)
  }
  const forecast=chart.prediction.attributes.d.match(/[\d.]+/g).map(Number)
  assert.equal(forecast[0],10+(589-10)*12/300)
  assert.equal(forecast[1],forecast[3])
  assert.equal(JSON.stringify(input),original)
})

test("La red anima solamente mensajes nuevos y cancela la reproducción al cambiar de intervalo",()=>{
  const f=chartFixture()
  const network=f.flow()
  const s={focus:3000,current:3000,trace:[{id:1,at:3001,source:"CLOB",target:"Libro",message:"Lectura",payload:{}}],
    notes:[],services:[]}
  network.update(s)
  assert.equal(f.timeouts.size,0)
  s.trace.push({id:2,at:3002,source:"Libro",target:"Laya",message:"Enviado",payload:{}})
  network.update(s)
  assert.equal(f.timeouts.size,1)
  network.update(s)
  assert.equal(network.queue.length,0)
  f.el("replayFlow").listeners.click()
  assert.equal(network.replaying,true)
  network.update({...s,focus:3300,current:3300,trace:[]})
  assert.equal(f.timeouts.size,0)
  assert.equal(network.particles.children.length,0)
})
function bottom(price) { return 241-(241-18)*price }

test("La transición termina en la cotización exacta y conserva los nodos SVG",()=>{
  const f=chartFixture()
  f.chart.update(state([{seen:3002,price:.4}]))
  const node=f.chart.line
  f.chart.update(state([{seen:3002,price:.4},{seen:3004,price:.8}]))
  assert.equal(f.frames.size,1)
  f.step(350)
  assert.ok(Number(f.chart.dot.attributes.cy)<bottom(.4))
  assert.ok(Number(f.chart.dot.attributes.cy)>bottom(.8))
  f.step(700)
  assert.equal(f.chart.line,node)
  assert.equal(Number(f.chart.dot.attributes.cy),bottom(.8))
  assert.equal(f.frames.size,0)
})

test("El cambio de intervalo cancela la transición anterior",()=>{
  const f=chartFixture()
  f.chart.update(state([{seen:3002,price:.4}]))
  f.chart.update(state([{seen:3002,price:.4},{seen:3004,price:.8}]))
  f.chart.update(state([{seen:3302,price:.5}],3300))
  assert.equal(f.frames.size,0)
  assert.equal(f.chart.focus,3300)
  assert.equal((f.chart.line.attributes.d.match(/M /g)||[]).length,1)
})

test("Movimiento reducido, pestaña oculta y múltiples muestras evitan la animación",()=>{
  for(const mode of ["reduced","hidden","batch"]){
    const f=chartFixture(mode==="reduced")
    f.chart.update(state([{seen:3002,price:.4}]))
    if(mode==="hidden") f.document.hidden=true
    const rows=[{seen:3002,price:.4},{seen:3004,price:.8}]
    if(mode==="batch") rows.push({seen:3006,price:.6})
    f.chart.update(state(rows))
    assert.equal(f.frames.size,0)
  }
})


test("BTC usa dólares, referencia exacta y predicción fija solo desde su emisión",()=>{
  const {chart}=chartFixture(true,true)
  chart.update({focus:3000,current:3000,btc:{target:{price:60000},
    quotes:[{ts:3002,price:59990},{ts:3012,price:60010}],forecast:{ready:3005,price:60020}}})
  assert.equal(chart.targetLine.style.display,"")
  const targetY=Number(chart.targetLine.attributes.y1)
  const priceY=chart.line.attributes.d.match(/[\d.]+/g).map(Number)[1]
  assert.ok(targetY<priceY)
  assert.ok(!chart.line.attributes.d.includes("NaN"))
  const forecast=chart.prediction.attributes.d.match(/[\d.]+/g).map(Number)
  assert.equal(forecast[0],10+(551-10)*5/300)
  assert.equal(forecast[2],551)
  assert.equal(forecast[1],forecast[3])
  chart.update({focus:3300,current:3300,btc:{quotes:[]}})
  assert.equal(chart.targetLine.style.display,"none")
  assert.equal(chart.prediction.attributes.d,"")
})

test("El indicador remoto distingue respuesta válida, pérdida de conexión y estado vencido",()=>{
  const html=readFileSync(path.join(__dirname,"../monitor.html"),"utf8")
  const start=html.indexOf("function teacherConnection("),end=html.indexOf("function connections(",start)
  const badge=vm.runInNewContext(html.slice(start,end)+";teacherConnection",{Date,local:String})
  assert.equal(badge([],3000).state,"waiting")
  assert.equal(badge([{service:"supervisor",state:"busy",at:3000,last_ok:2990}],3000).state,"ok")
  assert.equal(badge([{service:"supervisor",state:"ok",at:3000,last_ok:2990}],3200).state,"waiting")
  assert.equal(badge([{service:"supervisor",state:"error",at:3000,last_ok:2990}],3001).state,"error")
  assert.equal(badge([{service:"laya",state:"ok",at:3000,last_ok:2990}],3001).state,"waiting")
})
