<template>
  <section class="a-right">
    <div class="orb a"></div><div class="orb b"></div>
    <div class="spine">
      <span class="pip" style="animation-delay:0s"></span>
      <span class="pip" style="animation-delay:1.1s"></span>
      <span class="pip" style="animation-delay:2.2s"></span>
    </div>
    <div class="sc" ref="sc"></div>
  </section>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'

const sc = ref<HTMLElement | null>(null)
let timers: any[] = []
let elTimer: any = null
let mounted = true
const wait = (ms: number) => new Promise<void>(r => { timers.push(setTimeout(r, ms)) })
function clearAll(){ timers.forEach(clearTimeout); timers = []; if (elTimer){ clearInterval(elTimer); elTimer = null } }

const PIPE = [['CONNECT','connect'],['UNDERSTAND','understand'],['QUERY','query'],['ANSWER','answer'],['DASHBOARD','dashboard'],['DECK','deck'],['REPORT','report']]
const turns: any[] = [
  { q:'Analyze Q3 revenue and build me the deck.',
    sources:[['PG','#336791','Postgres','primary db'],['SF','#29b5e8','Snowflake','warehouse'],['PBI','#f2c811','Power BI','datasets'],['FS','#64748b','Files','uploads']], pick:1,
    schema:'reading schema · 11 tables · 84 columns',
    tasks:['Scan sales · 4.2M rows','Join region ⋈ orders','Aggregate revenue','Rank top 5'],
    sql:'SELECT region, SUM(revenue) rev FROM sales GROUP BY region ORDER BY rev DESC LIMIT 5',
    bars:[54,72,46,90,63], hl:3, kpiK:'Revenue', kpiV:7526, pre:'$', suf:'K', up:'▲ 12% QoQ',
    dec:['ACT','Lean budget into the <b>West</b> region before quarter-end.'],
    widgets:[{t:'kpi',l:'Revenue',v:7526,pre:'$',suf:'K',u:'▲ 12%'},{t:'kpi',l:'Top region',v:'West',u:'$2.1M'},{t:'bars',l:'By region',d:[50,70,44,90,60]},{t:'donut',l:'Channel mix',pct:62}],
    slides:[{n:'01',t:'Q3 Revenue',k:'bars'},{n:'02',t:'Top 5 Regions',k:'donut'},{n:'03',t:'Recommendations',k:'lines'}],
    report:{t:'Q3 Revenue Report',sub:'Executive summary',chart:[52,68,44,88,60]},
    chips:[['Semantic layer','#93c5fd'],['Read-only guard','#6ee7a8'],['SSO / LDAP','#a5b4fc']] },
  { q:'Why did customers churn in May? Write it up.',
    sources:[['MY','#00758f','MySQL','app db'],['RS','#c44','Redshift','warehouse'],['MG','#4faa41','MongoDB','events'],['DB','#ff3621','Databricks','lakehouse']], pick:0,
    schema:'reading schema · 9 tables · 61 columns',
    tasks:['Load subscriptions','Flag lapsed accounts','Compute lost MRR','Group by plan'],
    sql:'SELECT plan, COUNT(*) lapsed FROM subs WHERE churn=1 GROUP BY plan ORDER BY lapsed DESC',
    bars:[88,70,52,40,28], hl:0, kpiK:'Churned', kpiV:23, pre:'', suf:'', up:'▼ $4.8K MRR',
    dec:['WATCH','<b>Pro</b> plan drove 70% of churn — trigger a win-back flow.'],
    widgets:[{t:'kpi',l:'Churned',v:23,pre:'',suf:'',u:'▼ 4.8K'},{t:'kpi',l:'Worst plan',v:'Pro',u:'70%'},{t:'bars',l:'By plan',d:[88,64,48,34]},{t:'donut',l:'Win-back',pct:41}],
    slides:[{n:'01',t:'May Churn',k:'bars'},{n:'02',t:'By Plan',k:'donut'},{n:'03',t:'Win-back Plan',k:'lines'}],
    report:{t:'May Churn Analysis',sub:'Root-cause report',chart:[80,62,50,38,26]},
    chips:[['Mixture-of-Agents','#93c5fd'],['Shared memory','#c4b5fd'],['Forecasting','#6ee7a8']] },
]

function stepper(key: string){
  const ai = PIPE.findIndex(x => x[1] === key)
  return `<div class="pipe">${PIPE.map((p, mi) => {
    const cls = mi < ai ? 'done' : mi === ai ? 'on' : ''
    return `<span class="pstep ${cls}"><span class="pc"></span>${p[0]}</span>`
  }).join('')}</div>`
}

function typeInto(el: HTMLElement | null, txt: string, sp: number, caret?: boolean, sql?: boolean){
  return new Promise<void>(res => {
    if (!el){ res(); return }
    let c = 0
    ;(function tick(){
      if (!mounted){ res(); return }
      const shown = sql ? txt.slice(0,c).replace(/SELECT|SUM|FROM|GROUP BY|COUNT|WHERE|ORDER BY|LIMIT|DESC/g, m => `<b>${m}</b>`) : txt.slice(0,c)
      el.innerHTML = shown + (caret && c < txt.length ? '<span class="car"></span>' : '')
      c++
      if (c <= txt.length) timers.push(setTimeout(tick, sp)); else res()
    })()
  })
}
function countUp(el: HTMLElement, to: number, pre: string, suf: string, dur: number){
  const t0 = performance.now()
  ;(function f(now: number){
    const p = Math.min(1, (now - t0) / dur); const e = 1 - Math.pow(1 - p, 3)
    el.textContent = (pre || '') + Math.round(to * e).toLocaleString() + (suf || '')
    if (p < 1 && mounted) requestAnimationFrame(f)
  })(t0)
}

async function runLoop(i: number){
  if (!mounted || !sc.value) return
  const el0 = sc.value
  const t = turns[i % turns.length]
  el0.innerHTML = `
    <div class="sc-top"><span class="sc-livedot"></span><span class="sc-live">LIVE</span>
      <span class="sc-title">CityAgent is working on your task</span>
      <span class="sc-elapsed" id="el">0.0s</span></div>
    <div class="sc-q" id="q"></div>
    <div id="pipe"></div>
    <div class="stg" id="stg"></div>
    <div class="sc-foot"><span><b>677</b> routes · <b>46</b> connectors</span><span>● SSO ready</span></div>`
  const el = el0.querySelector('#el') as HTMLElement
  const t0 = performance.now()
  elTimer = setInterval(() => { if (el) el.textContent = ((performance.now() - t0) / 1000).toFixed(1) + 's' }, 100)
  const pipe = el0.querySelector('#pipe') as HTMLElement
  const stg = el0.querySelector('#stg') as HTMLElement
  const setP = (k: string) => { pipe.innerHTML = stepper(k) }

  await typeInto(el0.querySelector('#q'), t.q, 20, true)
  await wait(200); if (!mounted) return

  // CONNECT
  setP('connect')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Connect your data</div><div class="tiles" id="tl"></div>`
  const tl = stg.querySelector('#tl') as HTMLElement
  t.sources.forEach((s: any) => { const d = document.createElement('div'); d.className = 'tile'
    d.innerHTML = `<div class="lg" style="background:${s[1]}">${s[0]}</div><div class="nm">${s[2]}</div><div class="sb">${s[3]}</div>`; tl.appendChild(d) })
  const tiles = [...tl.children] as HTMLElement[]
  for (let k = 0; k < tiles.length; k++){ tiles[k].classList.add('in'); await wait(150) }
  await wait(260); tiles[t.pick].classList.add('pick'); await wait(650); if (!mounted) return

  // UNDERSTAND
  setP('understand')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Understand</div><div class="schema">${t.schema}</div><div class="tasks" id="tk"></div>`
  const tkw = stg.querySelector('#tk') as HTMLElement
  t.tasks.forEach((x: string) => { const d = document.createElement('div'); d.className = 'task'; d.innerHTML = `<span class="tk"></span><span>${x}</span>`; tkw.appendChild(d) })
  const tks = [...tkw.children] as HTMLElement[]
  for (let k = 0; k < tks.length; k++){ tks[k].classList.add('in'); await wait(110) }
  for (let k = 0; k < tks.length; k++){
    ;(tks[k].querySelector('.tk') as HTMLElement).innerHTML = '<span class="sp"></span>'; await wait(360)
    tks[k].classList.add('done')
    ;(tks[k].querySelector('.tk') as HTMLElement).innerHTML = '<svg width="10" height="10" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.2l2.2 2.3L9.5 3.5" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'; await wait(90)
  }
  await wait(250); if (!mounted) return

  // QUERY
  setP('query')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Query<span class="badge">READ-ONLY</span></div><div class="sql" id="sq"></div>`
  await typeInto(stg.querySelector('#sq'), t.sql, 11, true, true); await wait(600); if (!mounted) return

  // ANSWER
  setP('answer')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Answer</div>
    <div class="viz"><div class="chart" id="ch"></div>
      <div class="kpi"><div class="k">${t.kpiK}</div><div class="v" id="kv">${t.pre}0${t.suf}</div><div class="u">${t.up}</div></div></div>`
  const ch = stg.querySelector('#ch') as HTMLElement
  t.bars.forEach((h: number, idx: number) => { const b = document.createElement('div'); b.className = 'bar' + (idx === t.hl ? ' hl' : ''); ch.appendChild(b)
    timers.push(setTimeout(() => b.style.height = h + '%', 70 + idx * 80)) })
  countUp(stg.querySelector('#kv') as HTMLElement, t.kpiV, t.pre, t.suf, 850)
  await wait(1300); if (!mounted) return
  const dc = document.createElement('div'); dc.className = 'dec'; dc.innerHTML = `<span class="vp">${t.dec[0]}</span><span class="dt">${t.dec[1]}</span>`
  stg.appendChild(dc); requestAnimationFrame(() => dc.classList.add('in')); await wait(1500); if (!mounted) return

  // DASHBOARD
  setP('dashboard')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Building dashboard<span class="badge">6 charts</span></div><div class="dgrid" id="dg"></div>`
  const dg = stg.querySelector('#dg') as HTMLElement
  t.widgets.forEach((w: any) => { const d = document.createElement('div'); d.className = 'dw'
    if (w.t === 'kpi') d.innerHTML = `<div class="wl">${w.l}</div><div class="wv" data-v="${w.v}" data-pre="${w.pre||''}" data-suf="${w.suf||''}">${w.pre||''}${typeof w.v==='number'?'0':w.v}${w.suf||''}</div><div class="wu">${w.u}</div>`
    else if (w.t === 'bars') d.innerHTML = `<div class="wl">${w.l}</div><div class="mbars">${w.d.map(() => '<i></i>').join('')}</div>`
    else d.innerHTML = `<div class="wl">${w.l}</div><div class="donut" data-pct="${w.pct}"></div>`
    d.dataset.type = w.t; dg.appendChild(d) })
  const dws = [...dg.children] as HTMLElement[]
  for (let k = 0; k < dws.length; k++){
    dws[k].classList.add('in'); const wel = dws[k]
    if (wel.dataset.type === 'bars'){ const bs = [...wel.querySelectorAll('.mbars i')] as HTMLElement[]; const d = t.widgets.filter((w: any) => w.t === 'bars')[0].d
      bs.forEach((b, ix) => timers.push(setTimeout(() => b.style.height = d[ix] + '%', 120 + ix * 70))) }
    if (wel.dataset.type === 'donut'){ const dn = wel.querySelector('.donut') as HTMLElement; const pct = +(dn.dataset.pct as string)
      timers.push(setTimeout(() => dn.style.background = `conic-gradient(#60a5fa 0 ${pct*3.6}deg, rgba(255,255,255,.1) 0)`, 120)) }
    if (wel.dataset.type === 'kpi'){ const v = wel.querySelector('.wv') as HTMLElement; const to = +(v.dataset.v as string); if (to) countUp(v, to, v.dataset.pre as string, v.dataset.suf as string, 700) }
    await wait(240)
  }
  await wait(1400); if (!mounted) return

  // DECK
  setP('deck')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Generating slide deck<span class="badge">PPTX</span></div><div class="deck" id="dk"></div>`
  const dk = stg.querySelector('#dk') as HTMLElement
  const pos = [{ l:'6px', r:'-4deg' },{ l:'92px', r:'0deg' },{ l:'178px', r:'4deg' }]
  t.slides.forEach((s: any, ix: number) => { const d = document.createElement('div'); d.className = 'slide'
    d.style.left = pos[ix].l; d.style.transform = `rotate(${pos[ix].r}) translateY(6px)`
    const body = s.k === 'bars' ? `<div class="sbars"><i style="height:60%"></i><i style="height:80%"></i><i style="height:45%"></i><i style="height:95%"></i></div>`
      : s.k === 'donut' ? `<div class="sdot"></div>`
      : `<div class="sl" style="width:90%"></div><div class="sl" style="width:70%"></div><div class="sl" style="width:80%"></div>`
    d.innerHTML = `<div class="snum">${s.n}</div><div class="st">${s.t}</div>${body}`; dk.appendChild(d) })
  const sls = [...dk.children] as HTMLElement[]
  for (let k = 0; k < sls.length; k++){ sls[k].classList.add('in'); sls[k].style.transform = `rotate(${pos[k].r})`; await wait(320) }
  await wait(1500); if (!mounted) return

  // REPORT
  setP('report')
  stg.innerHTML = `<div class="phase"><span class="pd"></span>Writing report<span class="badge">DOCX</span></div>
    <div class="doc">
      <div class="drow" id="dr0"><div class="dbadge">W</div><div><div class="dh">${t.report.t}</div><div class="dsub">${t.report.sub}</div></div></div>
      <div class="ln h" id="l0"></div><div class="ln" id="l1"></div><div class="ln" id="l2" style="width:88%"></div>
      <div class="dchart" id="dch">${t.report.chart.map(() => '<i></i>').join('')}</div>
      <div class="ln" id="l3" style="width:76%"></div><div class="ln" id="l4" style="width:64%"></div>
    </div>`
  const seq = ['dr0','l0','l1','l2','dch','l3','l4']
  for (const id of seq){ const node = stg.querySelector('#' + id) as HTMLElement; node.classList.add('in')
    if (id === 'dch'){ const bars = [...stg.querySelectorAll('#dch i')] as HTMLElement[]; const d = t.report.chart
      bars.forEach((b, ix) => timers.push(setTimeout(() => b.style.height = d[ix] + '%', 120 + ix * 70))) }
    await wait(220)
  }
  const chips = document.createElement('div'); chips.className = 'chips'
  chips.innerHTML = t.chips.map((c: any) => `<span class="chip"><span class="cd" style="background:${c[1]}"></span>${c[0]}</span>`).join('')
  stg.appendChild(chips)
  await wait(2600); if (!mounted) return
  if (elTimer){ clearInterval(elTimer); elTimer = null }
  runLoop(i + 1)
}

onMounted(() => { mounted = true; requestAnimationFrame(() => runLoop(0)) })
onBeforeUnmount(() => { mounted = false; clearAll() })
</script>

<style scoped>
.a-right{align-self:stretch;border-radius:22px;overflow:hidden;position:relative;min-height:520px;height:100%;
  background:radial-gradient(120% 120% at 82% -12%,#1e3a8a 0%,#0f1e3d 52%,#0a1226 100%);
  border:1px solid rgba(59,130,246,.22);box-shadow:0 40px 90px -40px rgba(10,20,45,.8)}
.orb{position:absolute;border-radius:50%;filter:blur(40px);opacity:.5;pointer-events:none}
.orb.a{width:220px;height:220px;background:#2563eb;top:-40px;right:-30px;animation:orb 9s ease-in-out infinite}
.orb.b{width:180px;height:180px;background:#0ea5e9;bottom:-40px;left:-20px;animation:orb2 11s ease-in-out infinite}
@keyframes orb{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(-24px,20px) scale(1.15)}}
@keyframes orb2{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(20px,-16px) scale(1.2)}}
.spine{position:absolute;left:16px;top:26px;bottom:20px;width:2px;background:rgba(255,255,255,.07);border-radius:2px;overflow:hidden}
.spine .pip{position:absolute;left:-3px;width:8px;height:8px;border-radius:50%;background:#60a5fa;box-shadow:0 0 10px #3b82f6;animation:travel 3.4s linear infinite}
@keyframes travel{0%{top:-4%;opacity:0}8%{opacity:1}92%{opacity:1}100%{top:104%;opacity:0}}

.sc{position:absolute;inset:0;padding:20px 22px 18px 32px;display:flex;flex-direction:column;color:#e6ecf5;font-size:13px;z-index:2;font-family:inherit}
:deep(.sc-top){display:flex;align-items:center;gap:9px;margin-bottom:4px}
:deep(.sc-live){font-size:10px;font-weight:800;letter-spacing:.14em;color:#08122a;background:#60A5FA;padding:3px 7px;border-radius:5px}
:deep(.sc-livedot){width:6px;height:6px;border-radius:50%;background:#38BDF8;box-shadow:0 0 0 3px rgba(56,189,248,.2);animation:pulse 1.4s infinite}
:deep(.sc-title){font-size:12px;color:#c7d6ef}
:deep(.sc-elapsed){margin-left:auto;font-size:11px;color:#8ba0c4;font-variant-numeric:tabular-nums}
:deep(.sc-q){margin:9px 0 3px;background:rgba(255,255,255,.05);border:1px solid rgba(96,165,250,.28);border-radius:11px;padding:10px 13px;font-size:14.5px;font-weight:600;color:#eef4ff;min-height:18px}
:deep(.sc-q .car){display:inline-block;width:2px;height:14px;background:#60a5fa;margin-left:2px;vertical-align:-2px;animation:blink 1s step-end infinite}

:deep(.pipe){display:flex;gap:4px;flex-wrap:wrap;margin:9px 0 10px}
:deep(.pstep){font-size:9.5px;font-weight:700;letter-spacing:.02em;color:#7e93b6;border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:3px 8px;display:flex;align-items:center;gap:5px;transition:.3s}
:deep(.pstep.on){color:#eef4ff;border-color:#60a5fa;background:rgba(59,130,246,.16)}
:deep(.pstep.done){color:#9db2d6}
:deep(.pstep .pc){width:5px;height:5px;border-radius:50%;background:#3a5170}
:deep(.pstep.on .pc){background:#60a5fa;box-shadow:0 0 8px #3b82f6}
:deep(.pstep.done .pc){background:#6ee7a8}

:deep(.stg){flex:1;display:flex;flex-direction:column;gap:9px;min-height:0}
:deep(.phase){display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;letter-spacing:.06em;color:#c7d6ef;text-transform:uppercase}
:deep(.phase .pd){width:7px;height:7px;border-radius:50%;background:#60a5fa;box-shadow:0 0 8px #3b82f6}
:deep(.phase .badge){margin-left:6px;font-size:9px;font-weight:700;letter-spacing:.06em;color:#6ee7a8;border:1px solid rgba(110,231,168,.35);border-radius:5px;padding:2px 6px}
:deep(.schema){font-size:12.5px;color:#c7d6ef}

:deep(.tiles){display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
:deep(.tile){border:1px solid rgba(255,255,255,.09);background:rgba(255,255,255,.03);border-radius:10px;padding:9px 8px;text-align:center;opacity:0;transform:translateY(8px);transition:.4s}
:deep(.tile.in){opacity:1;transform:none}
:deep(.tile.pick){border-color:#60a5fa;background:rgba(59,130,246,.16);box-shadow:0 8px 26px -12px rgba(59,130,246,.8)}
:deep(.tile .lg){width:26px;height:26px;border-radius:7px;margin:0 auto 6px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;color:#fff}
:deep(.tile .nm){font-size:11px;color:#c7d6ef;font-weight:600}
:deep(.tile .sb){font-size:9.5px;color:#8ba0c4}

:deep(.tasks){display:flex;flex-direction:column;gap:7px}
:deep(.task){display:flex;align-items:center;gap:9px;font-size:12.5px;color:#9db2d6;opacity:0;transform:translateX(-6px);transition:.3s}
:deep(.task.in){opacity:1;transform:none}
:deep(.task .tk){width:16px;height:16px;border-radius:5px;flex:none;border:1.5px solid #3a5170;display:flex;align-items:center;justify-content:center}
:deep(.task.done){color:#dbe6f7}
:deep(.task.done .tk){background:#2563eb;border-color:#2563eb}
:deep(.task .sp){width:11px;height:11px;border:2px solid rgba(96,165,250,.35);border-top-color:#60a5fa;border-radius:50%;animation:spin .8s linear infinite}

:deep(.sql){font-family:ui-monospace,monospace;font-size:11.5px;color:#c7d6ef;background:rgba(0,0,0,.32);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:9px 11px;line-height:1.6;min-height:16px}
:deep(.sql b){color:#7fb0ff;font-weight:700}
:deep(.sql .car){display:inline-block;width:7px;height:13px;background:#7fb0ff;vertical-align:-2px;animation:blink 1s step-end infinite}

:deep(.viz){display:flex;gap:14px;align-items:flex-end}
:deep(.chart){flex:1;display:flex;align-items:flex-end;gap:7px;height:86px;padding:6px 4px 0;border-bottom:1px solid rgba(255,255,255,.1)}
:deep(.bar){flex:1;border-radius:5px 5px 0 0;height:0;background:linear-gradient(180deg,#60a5fa,#2563eb);transition:height .7s cubic-bezier(.2,.7,.2,1)}
:deep(.bar.hl){background:linear-gradient(180deg,#93c5fd,#3b82f6);box-shadow:0 0 20px -4px rgba(96,165,250,.9)}
:deep(.kpi){width:118px;flex:none;border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:10px 12px;background:rgba(255,255,255,.03)}
:deep(.kpi .k){font-size:10px;color:#9db2d6;font-weight:700;letter-spacing:.04em}
:deep(.kpi .v){font-size:24px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums;line-height:1.15;margin-top:3px}
:deep(.kpi .u){font-size:11px;font-weight:700;color:#6ee7a8}
:deep(.dec){display:flex;gap:11px;align-items:flex-start;border:1px solid rgba(96,165,250,.4);background:rgba(59,130,246,.12);border-radius:12px;padding:11px 13px;opacity:0;transform:translateY(10px);transition:.4s}
:deep(.dec.in){opacity:1;transform:none}
:deep(.dec .vp){font-size:10px;font-weight:800;letter-spacing:.1em;color:#08122a;background:#6ee7a8;border-radius:6px;padding:4px 8px;flex:none}
:deep(.dec .dt){font-size:12.5px;color:#eef4ff;line-height:1.4}
:deep(.dec .dt b){color:#93c5fd}

:deep(.dgrid){display:grid;grid-template-columns:1fr 1fr;gap:8px}
:deep(.dw){border:1px solid rgba(255,255,255,.09);background:rgba(255,255,255,.03);border-radius:11px;padding:10px 11px;opacity:0;transform:scale(.93);transition:.35s}
:deep(.dw.in){opacity:1;transform:none}
:deep(.dw .wl){font-size:9.5px;color:#9db2d6;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
:deep(.dw .wv){font-size:19px;font-weight:800;color:#fff;margin-top:2px;font-variant-numeric:tabular-nums}
:deep(.dw .wu){font-size:10px;color:#6ee7a8;font-weight:700}
:deep(.mbars){display:flex;align-items:flex-end;gap:4px;height:40px;margin-top:6px}
:deep(.mbars i){flex:1;background:linear-gradient(180deg,#60a5fa,#2563eb);border-radius:3px 3px 0 0;height:0;transition:height .55s cubic-bezier(.2,.7,.2,1)}
:deep(.donut){width:46px;height:46px;border-radius:50%;margin:4px auto 0;position:relative;background:conic-gradient(#60a5fa 0 0deg,rgba(255,255,255,.1) 0)}
:deep(.donut::after){content:"";position:absolute;inset:9px;border-radius:50%;background:#0f1e3d}

:deep(.deck){position:relative;height:158px}
:deep(.slide){position:absolute;top:14px;width:152px;height:100px;border-radius:11px;border:1px solid rgba(255,255,255,.13);background:linear-gradient(180deg,#152a52,#0f1e3d);padding:10px;opacity:0;transition:.45s cubic-bezier(.2,.7,.2,1);box-shadow:0 16px 34px -16px rgba(0,0,0,.7)}
:deep(.slide.in){opacity:1}
:deep(.slide .snum){position:absolute;top:8px;right:10px;font-size:9px;color:#8ba0c4;font-weight:700}
:deep(.slide .st){font-size:10.5px;font-weight:800;color:#eef4ff}
:deep(.slide .sbars){display:flex;gap:3px;align-items:flex-end;height:42px;margin-top:9px}
:deep(.slide .sbars i){flex:1;background:#3b82f6;border-radius:2px 2px 0 0}
:deep(.slide .sdot){width:36px;height:36px;border-radius:50%;margin:8px auto 0;background:conic-gradient(#60a5fa 0 58%,rgba(255,255,255,.12) 0);position:relative}
:deep(.slide .sdot::after){content:"";position:absolute;inset:7px;border-radius:50%;background:#0f1e3d}
:deep(.slide .sl){height:5px;background:rgba(255,255,255,.14);border-radius:3px;margin-top:7px}

:deep(.doc){background:#f8fafc;border-radius:11px;padding:13px 15px;color:#0f172a;box-shadow:0 24px 46px -22px rgba(0,0,0,.7);overflow:hidden}
:deep(.doc .drow){display:flex;align-items:center;gap:8px;opacity:0;transform:translateY(4px);transition:.3s}
:deep(.doc .drow.in){opacity:1;transform:none}
:deep(.doc .dbadge){width:22px;height:22px;border-radius:6px;background:#2563EB;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:11px}
:deep(.doc .dh){font-size:13px;font-weight:800;color:#0f172a}
:deep(.doc .dsub){font-size:8.5px;color:#64748b;letter-spacing:.1em;font-weight:700;text-transform:uppercase}
:deep(.doc .ln){height:6px;border-radius:3px;background:#e2e8f0;margin-top:7px;opacity:0;transform:translateX(-6px);transition:.3s}
:deep(.doc .ln.in){opacity:1;transform:none}
:deep(.doc .ln.h){background:#c7d2e0;height:8px;width:44%}
:deep(.doc .dchart){height:46px;border-radius:7px;margin-top:9px;background:linear-gradient(180deg,#dbeafe,#eff6ff);border:1px solid #dbeafe;display:flex;align-items:flex-end;gap:5px;padding:7px 8px;opacity:0;transition:.35s}
:deep(.doc .dchart.in){opacity:1}
:deep(.doc .dchart i){flex:1;background:#3b82f6;border-radius:2px 2px 0 0;height:0;transition:height .5s}

:deep(.chips){display:flex;gap:7px;flex-wrap:wrap;margin-top:auto;padding-top:10px}
:deep(.chip){font-size:11px;color:#a7bde0;border:1px solid rgba(255,255,255,.1);border-radius:999px;padding:4px 10px;display:flex;align-items:center;gap:6px}
:deep(.chip .cd){width:6px;height:6px;border-radius:50%}
:deep(.sc-foot){display:flex;justify-content:space-between;font-size:11.5px;color:#8ba0c4;margin-top:9px;padding-top:9px;border-top:1px solid rgba(255,255,255,.07)}
:deep(.sc-foot b){color:#93c5fd}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.a-right *{animation:none !important;transition:none !important}}
@media(max-width:900px){.a-right{display:none}}
</style>
