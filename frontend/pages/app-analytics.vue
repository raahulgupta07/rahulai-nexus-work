<template>
  <div class="appx">

    <div class="wrap">

      <!-- HEADER -->
      <div class="top">
        <div class="title">
          <div class="crumb">Monitoring · App Analytics</div>
          <h1>App Analytics <span class="badge new">NEW</span></h1>
          <p>End-to-end usage, adoption &amp; ROI across every user, company and connector — City Holdings Limited</p>
        </div>
        <div class="controls">
          <button class="ctl"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg><b>Last 90 days</b>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></button>
          <button class="ctl"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M7 12h10M11 18h2"/></svg>All companies</button>
          <button class="ctl"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v4H4zM4 12h10v8H4z"/></svg>All connectors</button>
          <button class="ctl" style="background:var(--accent);color:#fff;border-color:var(--accent)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 21h16"/></svg>Export</button>
        </div>
      </div>

      <!-- TABS -->
      <div class="tabs" id="tabs">
        <button class="tab on" data-v="overview"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>Overview &amp; ROI</button>
        <button class="tab" data-v="users"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0112 0M16 6a3 3 0 010 6M21 20a6 6 0 00-4-5.7"/></svg>Users</button>
        <button class="tab" data-v="companies"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M5 21V7l7-4 7 4v14M9 9h.01M9 13h.01M15 9h.01M15 13h.01"/></svg>Companies</button>
        <button class="tab" data-v="agents"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 8V4M9 3h6M9 14h.01M15 14h.01"/></svg>Agents</button>
        <button class="tab" data-v="connectors"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="7" height="7" rx="1"/><rect x="14" y="4" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M17 14v7"/></svg>Connectors</button>
        <button class="tab" data-v="questions"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>Questions</button>
        <button class="tab" data-v="cost"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/></svg>Cost &amp; Tokens</button>
      </div>

      <!-- ============ OVERVIEW ============ -->
      <div class="view on" id="v-overview">

        <!-- top KPI band -->
        <div class="grid g6">
          <div class="card kpi"><div class="lbl">Active users</div><div class="big tnum" data-k="active_users">—</div>
            <div class="foot" data-k="active_users_foot"></div></div>
          <div class="card kpi"><div class="lbl">Companies</div><div class="big tnum" data-k="companies">—</div>
            <div class="foot" data-k="companies_foot"></div></div>
          <div class="card kpi"><div class="lbl">Chat sessions</div><div class="big tnum" data-k="sessions">—</div>
            <div class="foot" data-k="sessions_foot"></div></div>
          <div class="card kpi"><div class="lbl">Queries run</div><div class="big tnum" data-k="queries">—</div>
            <div class="foot" data-k="queries_foot"></div></div>
          <div class="card kpi"><div class="lbl">AI spend</div><div class="big tnum" data-k="spend">—</div>
            <div class="foot" data-k="spend_foot"></div></div>
          <div class="card kpi"><div class="lbl">Answer quality</div><div class="big tnum" data-k="quality">—</div>
            <div class="foot" data-k="quality_foot"></div></div>
        </div>

        <!-- ACTIVITY STRIP: DAU/WAU/MAU + chats + tokens -->
        <div class="section-t">Activity <span class="mut3" style="text-transform:none;letter-spacing:0;font-weight:400">— active users &amp; volume over the period</span></div>
        <div class="grid g6">
          <div class="card kpi clik" data-t="Daily active users (DAU)" data-u="users/day" data-series="dau"><div class="lbl">Daily active <span class="badge live" style="font-size:9px">DAU</span><span class="expico"></span></div><div class="big tnum" data-k="act_dau">—</div><div class="spk" data-s="0" data-c="blue"></div></div>
          <div class="card kpi clik" data-t="Weekly active users (WAU)" data-u="users/week" data-series="wau"><div class="lbl">Weekly active <span class="badge live" style="font-size:9px">WAU</span><span class="expico"></span></div><div class="big tnum" data-k="act_wau">—</div><div class="spk" data-s="0" data-c="violet"></div></div>
          <div class="card kpi clik" data-t="Monthly active users (MAU)" data-u="users/month" data-series="mau"><div class="lbl">Monthly active <span class="badge live" style="font-size:9px">MAU</span><span class="expico"></span></div><div class="big tnum" data-k="act_mau">—</div><div class="spk" data-s="0" data-c="teal"></div></div>
          <div class="card kpi clik" data-t="Total chats" data-u="chats/week" data-series="chats"><div class="lbl">Total chats<span class="expico"></span></div><div class="big tnum" data-k="act_chats">—</div><div class="spk" data-s="0" data-c="amber"></div></div>
          <div class="card kpi clik" data-t="Total messages" data-u="messages/week" data-series="messages"><div class="lbl">Total messages<span class="expico"></span></div><div class="big tnum" data-k="act_messages">—</div><div class="spk" data-s="0" data-c="blue"></div></div>
          <div class="card kpi clik" data-t="Total tokens" data-u="tokens/week" data-series="tokens"><div class="lbl">Total tokens<span class="expico"></span></div><div class="big tnum" data-k="act_tokens">—</div><div class="spk" data-s="0" data-c="teal"></div><div class="foot" data-k="act_tokens_foot"></div></div>
        </div>

        <!-- ROI BAND -->
        <div class="roi mt14">
          <div class="between">
            <div><div class="eyebrow">Portfolio value · derived from locked 2026 baseline</div>
              <div style="font-size:15px;font-weight:600;margin-top:3px">Output ÷ Input — what the platform returned vs what it cost</div></div>
            <div class="badge" style="background:rgba(255,255,255,.16);color:#fff">▲ tracking to +50% by FY30</div>
          </div>
          <div class="roi-row">
            <div class="roi-cell"><div class="v tnum" data-k="roi_hours_v">—</div><div class="k">Hours saved</div><div class="n" data-k="roi_hours_n"></div></div>
            <div class="roi-cell"><div class="v tnum" data-k="roi_labour_v">—</div><div class="k">Labour value freed</div><div class="n" data-k="roi_labour_n"></div></div>
            <div class="roi-cell"><div class="v tnum" data-k="roi_autocost_v">—</div><div class="k">Automation cost</div><div class="n" data-k="roi_autocost_n"></div></div>
            <div class="roi-cell"><div class="v tnum" data-k="roi_net_v">—</div><div class="k">Net benefit</div><div class="n" data-k="roi_net_n"></div></div>
            <div class="roi-cell"><div class="v tnum" data-k="roi_roi_v">—</div><div class="k">ROI</div><div class="n" data-k="roi_roi_n"></div></div>
          </div>
          <div class="n" data-k="roi_caption" style="margin-top:10px;opacity:.75;font-size:11px"></div>
        </div>

        <!-- VALUE DIMENSIONS (deck s16/37) -->
        <div class="section-t">Business value dimensions <span class="mut3" style="text-transform:none;letter-spacing:0;font-weight:400">— each auditable as output ÷ input</span></div>
        <div class="grid g3">
          <div class="card vd"><div class="ico tint-blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg></div>
            <div><div class="v tnum" data-k="vd_time">—</div><div class="k">Time saved</div><div class="n">Hours removed from manual workflows</div></div></div>
          <div class="card vd"><div class="ico tint-green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg></div>
            <div><div class="v tnum" data-k="vd_money">—</div><div class="k">Money saved</div><div class="n">FTE capacity + rework avoided</div></div></div>
          <div class="card vd"><div class="ico tint-violet"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg></div>
            <div><div class="v tnum" data-k="vd_revenue" style="color:var(--ink3)">Not tracked</div><div class="k">Revenue influenced</div><div class="n">No revenue signal connected</div></div></div>
          <div class="card vd"><div class="ico tint-amber"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l9 4v6c0 5-3.8 8.5-9 10-5.2-1.5-9-5-9-10V6z"/></svg></div>
            <div><div class="v tnum" data-k="vd_risk">—</div><div class="k">Error rate</div><div class="n" data-k="vd_risk_n">Failed vs total this period</div></div></div>
          <div class="card vd"><div class="ico tint-teal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 10h.01M12 10h.01M16 10h.01M21 12a8 8 0 01-11.5 7.2L3 21l1.8-6.5A8 8 0 1121 12z"/></svg></div>
            <div><div class="v tnum" data-k="vd_csat" style="color:var(--ink3)">Not tracked</div><div class="k">Customer satisfaction</div><div class="n">No CSAT/NPS survey connected</div></div></div>
          <div class="card vd"><div class="ico tint-red"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h7l-1 8 10-12h-7z"/></svg></div>
            <div><div class="v tnum" data-k="vd_prod">—</div><div class="k">Employee productivity</div><div class="n">Capacity released to higher-value work</div></div></div>
        </div>

        <!-- automation before/after + adoption -->
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Automation: before → after</h3><div class="sub">Same workflow, manual vs AI-optimized (deck slide 19/21)</div></div></div>
            <div id="ba"></div>
            <div class="note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>
              Baseline minutes per workflow are the locked 2026 figures signed by each business unit. Target derives itself: ×0.5 for lower-is-better metrics.</div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Adoption &amp; quality guardrail</h3><div class="sub">Volume paired with quality — a gain, not a spike (deck slide 18)</div></div></div>
            <div class="grid g2" style="gap:10px">
              <div><div class="mut" style="font-size:12px">Daily active</div><div style="font-size:22px;font-weight:700" class="tnum" data-k="ad_dau">—</div></div>
              <div><div class="mut" style="font-size:12px">Weekly active</div><div style="font-size:22px;font-weight:700" class="tnum" data-k="ad_wau">—</div></div>
              <div><div class="mut" style="font-size:12px">Monthly active</div><div style="font-size:22px;font-weight:700" class="tnum" data-k="ad_mau">—</div></div>
              <div><div class="mut" style="font-size:12px">Repeat use (WAU/MAU)</div><div style="font-size:22px;font-weight:700" class="tnum" data-k="ad_repeat">—</div></div>
            </div>
            <div style="margin-top:16px">
              <div class="between" style="font-size:12px"><span class="mut">Answer accuracy (paired quality)</span><b data-k="ad_acc">—</b></div>
              <div class="bar-mini mt14" style="height:8px"><i data-k="ad_acc_bar" style="width:0%;background:var(--good)"></i></div>
              <div class="between mt14" style="font-size:12px"><span class="mut">Instruction coverage</span><b data-k="ad_instr">—</b></div>
              <div class="bar-mini" style="height:8px;margin-top:6px"><i data-k="ad_instr_bar" style="width:0%"></i></div>
              <div class="between mt14" style="font-size:12px"><span class="mut">Positive feedback rate</span><b data-k="ad_fb">—</b></div>
              <div class="bar-mini" style="height:8px;margin-top:6px"><i data-k="ad_fb_bar" style="width:0%;background:var(--violet)"></i></div>
            </div>
          </div>
        </div>

        <!-- RETENTION & ACTIVATION (research gaps: HEART / cohort / funnel) -->
        <div class="section-t">Retention &amp; activation <span class="mut3" style="text-transform:none;letter-spacing:0;font-weight:400">— HEART framework: is growth real or vanity?</span></div>
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Stickiness (DAU/MAU)</div><div class="big tnum" data-k="ret_stick">—</div><div class="foot">target &gt;20% is healthy</div></div>
          <div class="card kpi"><div class="lbl">Resolution rate</div><div class="big tnum" data-k="ret_res">—</div><div class="foot">answered without fallback · benchmark 75–85%</div></div>
          <div class="card kpi"><div class="lbl">30-day retention</div><div class="big tnum" data-k="ret_r30">—</div><div class="foot">users active again within 30d</div></div>
          <div class="card kpi"><div class="lbl">Median time-to-value</div><div class="big tnum" data-k="ret_ttv">—</div><div class="foot">signup → first answer</div></div>
        </div>
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Weekly retention cohorts</h3><div class="sub">% of each signup cohort still active N weeks later</div></div></div>
            <div style="overflow-x:auto"><table class="cohort"><thead><tr><th class="rowlab">Cohort</th><th>Users</th><th>W0</th><th>W1</th><th>W2</th><th>W3</th><th>W4</th><th>W5</th></tr></thead><tbody id="cohort"></tbody></table></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Activation funnel</h3><div class="sub">where new users drop before habit</div></div></div>
            <div class="funnel" id="funnel"></div>
            <div class="note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>
              Biggest drop: signed up → connected an agent (−26%). Onboarding a first connector is the activation bottleneck.</div>
          </div>
        </div>

        <!-- companies + connectors + questions preview -->
        <div class="grid g3 mt14">
          <div class="card">
            <div class="chd"><div><h3>Top companies</h3><div class="sub">by domain · net value</div></div></div>
            <div id="ovco"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Connector adoption</h3><div class="sub">users on each connector</div></div></div>
            <div id="ovcn"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Question mix</h3><div class="sub">what people ask</div></div></div>
            <div class="flex" style="align-items:center;gap:16px">
              <div id="qdonut"></div>
              <div class="legend" id="qleg" style="flex:1"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- ============ USERS ============ -->
      <div class="view" id="v-users">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Total users</div><div class="big tnum" data-k="u_total">—</div><div class="foot" data-k="u_total_foot"></div></div>
          <div class="card kpi"><div class="lbl">Power users</div><div class="big tnum" data-k="u_power">—</div><div class="foot">&gt;50 queries in period</div></div>
          <div class="card kpi"><div class="lbl">Dormant</div><div class="big tnum" data-k="u_dormant">—</div><div class="foot">no activity 30d+ · re-engage</div></div>
          <div class="card kpi"><div class="lbl">Avg hours saved / user</div><div class="big tnum" data-k="u_avghrs">—</div><div class="foot">per active user</div></div>
        </div>
        <div class="card pad0 mt14">
          <div class="between" style="padding:14px 16px;border-bottom:1px solid var(--line)">
            <div><h3>Every user — activity &amp; value</h3><span class="mut3" style="font-size:12px">sortable · click a row to drill in</span></div>
            <div class="exp">
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 21h16"/></svg>CSV</button>
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>Excel</button>
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/></svg>PDF</button>
            </div>
          </div>
          <div style="overflow-x:auto">
          <table>
            <thead><tr><th>User</th><th>Company</th><th>Dept</th><th>Joined</th><th>Last active</th><th class="r">Sessions</th><th class="r">Queries</th><th class="r">Cost</th><th class="r">Hrs saved</th><th>Adoption</th></tr></thead>
            <tbody id="utab"></tbody>
          </table>
          </div>
        </div>
      </div>

      <!-- ============ COMPANIES ============ -->
      <div class="view" id="v-companies">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Companies (domains)</div><div class="big tnum" data-k="co_count">—</div><div class="foot">1 domain = 1 company</div></div>
          <div class="card kpi"><div class="lbl">Top by value</div><div class="big" style="font-size:19px" data-k="co_top">—</div><div class="foot" data-k="co_top_foot"></div></div>
          <div class="card kpi"><div class="lbl">Most users</div><div class="big" style="font-size:19px" data-k="co_most">—</div><div class="foot" data-k="co_most_foot"></div></div>
          <div class="card kpi"><div class="lbl">Most queries</div><div class="big" style="font-size:19px" data-k="co_active">—</div><div class="foot" data-k="co_active_foot"></div></div>
        </div>
        <div class="card pad0 mt14">
          <div class="between" style="padding:14px 16px;border-bottom:1px solid var(--line)"><h3>Company leaderboard</h3>
            <div class="exp">
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 21h16"/></svg>CSV</button>
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/></svg>PDF</button>
            </div>
          </div>
          <div style="overflow-x:auto">
          <table>
            <thead><tr><th>Company</th><th>Domain</th><th class="r">Users</th><th class="r">Active</th><th>Connectors</th><th class="r">Queries</th><th class="r">Hrs saved</th><th class="r">Net value</th><th class="r">ROI</th></tr></thead>
            <tbody id="ctab"></tbody>
          </table>
          </div>
        </div>
      </div>

      <!-- ============ AGENTS ============ -->
      <div class="view" id="v-agents">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Agents</div><div class="big tnum" data-k="ag_count">—</div><div class="foot" data-k="ag_count_foot"></div></div>
          <div class="card kpi"><div class="lbl">Top agent</div><div class="big" style="font-size:18px" data-k="ag_top">—</div><div class="foot" data-k="ag_top_foot"></div></div>
          <div class="card kpi"><div class="lbl">Avg success</div><div class="big tnum" data-k="ag_res">—</div><div class="foot">runs completed / total</div></div>
          <div class="card kpi"><div class="lbl">Avg latency</div><div class="big tnum" data-k="ag_lat">—</div><div class="foot">per run</div></div>
        </div>
        <div class="card pad0 mt14">
          <div class="between" style="padding:14px 16px;border-bottom:1px solid var(--line)">
            <div><h3>Agent performance &amp; value</h3><span class="mut3" style="font-size:12px">per agent · last 90 days</span></div>
            <div class="exp">
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 21h16"/></svg>CSV</button>
              <button class="chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/></svg>PDF</button>
            </div>
          </div>
          <div style="overflow-x:auto">
          <table>
            <thead><tr><th>Agent</th><th>Type</th><th>Connectors</th><th class="r">Users</th><th class="r">Runs</th><th class="r">Success</th><th class="r">Avg latency</th><th class="r">Cost</th><th class="r">Hrs saved</th><th class="r">Quality</th><th>Last run</th></tr></thead>
            <tbody id="atab"></tbody>
          </table>
          </div>
        </div>
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Runs &amp; value by agent</h3><div class="sub">which agents earn their keep</div></div></div>
            <div id="abars"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Agent quality vs volume</h3><div class="sub">watch for high volume + low quality (deck s18)</div></div></div>
            <div id="aquality"></div>
            <div class="note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>
              HR Policy agent: high volume, quality dipping to 71% — candidate for an instruction / skill fix before it erodes trust.</div>
          </div>
        </div>
      </div>

      <!-- ============ CONNECTORS ============ -->
      <div class="view" id="v-connectors">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Connectors</div><div class="big tnum" data-k="cn_count">—</div><div class="foot" data-k="cn_count_foot"></div></div>
          <div class="card kpi"><div class="lbl">Most used</div><div class="big" style="font-size:18px" data-k="cn_most">—</div><div class="foot" data-k="cn_most_foot"></div></div>
          <div class="card kpi"><div class="lbl">Avg success rate</div><div class="big tnum" data-k="cn_succ">—</div><div class="foot">across all connectors</div></div>
          <div class="card kpi"><div class="lbl">Total queries</div><div class="big tnum" data-k="cn_queries">—</div><div class="foot">via connectors in period</div></div>
        </div>
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Who uses which connector</h3><div class="sub">from per-user credentials · last used</div></div></div>
            <div id="conntab"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Queries &amp; cost per connector</h3><div class="sub">last 90 days</div></div></div>
            <div id="connbars"></div>
            <div class="note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>
              Per-user token connectors (Fabric User, Power BI User) attribute cost to the individual signer, so cost splits by person even on a shared connector.</div>
          </div>
        </div>
      </div>

      <!-- ============ QUESTIONS ============ -->
      <div class="view" id="v-questions">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Chats classified</div><div class="big tnum" data-k="q_classified">—</div><div class="foot" data-k="q_classified_foot"></div></div>
          <div class="card kpi"><div class="lbl">Top intent</div><div class="big" style="font-size:18px" data-k="q_top">—</div><div class="foot" data-k="q_top_foot"></div></div>
          <div class="card kpi"><div class="lbl">Unanswered / failed</div><div class="big tnum" data-k="q_failed">—</div><div class="foot" data-k="q_failed_foot"></div></div>
          <div class="card kpi"><div class="lbl">Clusters</div><div class="big tnum" data-k="q_clusters">—</div><div class="foot">distinct question types</div></div>
        </div>
        <div class="section-t">Question clusters — grouped by what people ask</div>
        <div class="grid g4" id="qclusters"></div>
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Intent trend (12 weeks)</h3><div class="sub">volume by top clusters</div></div></div>
            <div id="qtrend"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Failed / low-confidence questions</h3><div class="sub">needs a skill, instruction or data fix</div></div></div>
            <div id="qfail"></div>
          </div>
        </div>
      </div>

      <!-- ============ COST ============ -->
      <div class="view" id="v-cost">
        <div class="grid g4">
          <div class="card kpi"><div class="lbl">Total AI spend</div><div class="big tnum" data-k="c_spend">—</div><div class="foot" data-k="c_spend_foot"></div></div>
          <div class="card kpi"><div class="lbl">Routing savings</div><div class="big tnum" data-k="c_routing">—</div><div class="foot">auto-routed to cheaper models</div></div>
          <div class="card kpi"><div class="lbl">Cost / query</div><div class="big tnum" data-k="c_perquery">—</div><div class="foot">spend ÷ queries</div></div>
          <div class="card kpi"><div class="lbl">Cost / active user</div><div class="big tnum" data-k="c_peruser">—</div><div class="foot">spend ÷ active users</div></div>
        </div>
        <div class="grid g2 mt14">
          <div class="card">
            <div class="chd"><div><h3>Spend by model</h3><div class="sub">from llm_usage_records</div></div></div>
            <div id="costmodel"></div>
          </div>
          <div class="card">
            <div class="chd"><div><h3>Spend by company</h3><div class="sub">chargeback view</div></div></div>
            <div id="costco"></div>
          </div>
        </div>
        <div class="card mt14">
          <div class="chd"><div><h3>Cost vs value delivered</h3><div class="sub">every $1 of AI spend returned $47 in freed labour value</div></div></div>
          <div id="cvv"></div>
        </div>
      </div>

    </div>

    <!-- TREND MODAL -->
    <div class="ov" id="ov">
      <div class="modal">
        <div class="mh">
          <div><h3 id="mtitle">Trend</h3><div class="msub" id="msub"></div></div>
          <div style="display:flex;gap:10px;align-items:center">
            <div class="seg" id="mseg"><button data-r="7">Last 7d</button><button class="on" data-r="12">Full period</button></div>
            <button class="xbtn" id="xbtn"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
          </div>
        </div>
        <div class="mb">
          <div class="mstat" id="mstat"></div>
          <div id="mchart"></div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
// Full-app usage / adoption / ROI analytics. Admin-only, mirrors the
// manage_settings gate the /console/* monitoring pages use.
definePageMeta({
  auth: true,
  permissions: ['manage_settings']
})

// Flag guard: the "App Analytics" nav item is already gated on
// features.app_analytics; this stops direct-URL access when the flag is off.
const { appAnalyticsOn } = useAppSettings()

// ---------- shared helpers (module scope so render + modal reuse) ----------
const AC: Record<string, string> = { blue: '#2563eb', green: '#16a34a', violet: '#7c3aed', amber: '#d97706', teal: '#0d9488', pink: '#db2777', red: '#dc2626', slate: '#64748b' }
const CLUSTER_COLOR: Record<string, string> = { sales: AC.blue, inventory: AC.violet, finance: AC.green, forecast: AC.amber, hr: AC.teal, ops: AC.pink, adhoc: AC.slate, other: '#a3a3a3' }
const AGENT_TYPE_COLOR: Record<string, string> = { 'Analytics': AC.blue, 'Document AI': AC.green, 'ML': AC.amber, 'GenAI / RAG': AC.violet, 'GenAI': AC.violet, 'Predictive': AC.teal, 'Database': AC.blue }
function avColor(s: string) { const p = [AC.blue, AC.violet, AC.teal, AC.amber, AC.pink, AC.green]; let h = 0; for (const c of (s || '?')) h = (h * 31 + c.charCodeAt(0)) & 255; return p[h % p.length] }
function initials(n: string) { return (n || '?').split(' ').map(x => x[0]).slice(0, 2).join('').toUpperCase() }
function nInt(n: any) { return (n == null ? 0 : Math.round(n)).toLocaleString() }
function fmt(n: any) { n = n || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : ('' + Math.round(n)) }
function usd(n: any) { if (n == null) return '—'; const x = Math.abs(n); if (x >= 1000) return '$' + (n / 1000).toFixed(1) + 'k'; return '$' + (x >= 100 ? Math.round(n).toString() : n.toFixed(2)) }
function pctS(v: any) { return v == null ? '—' : Math.round(v) + '%' }
function esc(s: any) { return ('' + (s ?? '')).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' } as any)[c]) }
function fmtDate(s: any) { if (!s) return '—'; const d = new Date(s); if (isNaN(+d)) return '' + s; return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) }
function iconFor(name: string, kind: string) { const t = ((name || '') + ' ' + (kind || '')).toLowerCase(); if (t.includes('fabric')) return '🔷'; if (t.includes('power')) return '📊'; if (t.includes('postgres') || t.includes('sql')) return '🐘'; if (t.includes('duck')) return '🦆'; if (t.includes('excel') || t.includes('sheet')) return '📄'; return '🔌' }
function empty(msg: string) { return `<div class="mut3" style="padding:18px 4px;font-size:12.5px">${msg}</div>` }

const analyticsError = ref('')
const analyticsLoading = ref(true)

onMounted(async () => {
  if (!appAnalyticsOn.value) {
    navigateTo('/')
    return
  }

  // ---------- fetch real data ----------
  const end = new Date()
  const start = new Date(Date.now() - 90 * 864e5)
  let P: any = null
  try {
    const { data, error } = await useMyFetch<any>(
      `/api/console/app-analytics?start_date=${encodeURIComponent(start.toISOString())}&end_date=${encodeURIComponent(end.toISOString())}`,
      { method: 'GET' } as any
    )
    if ((error as any)?.value) throw (error as any).value
    P = (data as any)?.value
  } catch (e: any) {
    analyticsError.value = e?.message || 'Failed to load analytics'
  }
  analyticsLoading.value = false
  if (!P) {
    const host = document.querySelector('.appx .wrap')
    if (host) {
      const note = document.createElement('div')
      note.className = 'card'
      note.style.marginTop = '14px'
      note.innerHTML = `<div class="mut" style="font-size:13px">Couldn't load analytics — ${esc(analyticsError.value || 'no data')}. Try reloading.</div>`
      host.appendChild(note)
    }
    return
  }

  const K: Record<string, string> = {}
  const setK = (root: Document | Element = document) => {
    root.querySelectorAll('[data-k]').forEach(el => {
      const key = (el as HTMLElement).dataset.k as string
      if (K[key] !== undefined) (el as HTMLElement).textContent = K[key]
    })
  }
  const setBar = (key: string, pct: any) => {
    const el = document.querySelector(`[data-k="${key}"]`) as HTMLElement | null
    if (el) el.style.width = (pct == null ? 0 : Math.max(0, Math.min(100, pct))) + '%'
  }
  const byId = (id: string) => document.getElementById(id)

  const k = P.kpis || {}, act = P.activity || {}, ad = P.adoption || {}, roi = P.roi || {}, vd = P.value_dims || {}, cost = P.cost || {}
  const tokens = k.tokens || { total: 0, input: 0, output: 0, cache: 0 }
  const bl = roi.baseline || { minutes_per_query: 30, hourly_rate_usd: 18 }

  // ===== KPI text map =====
  Object.assign(K, {
    active_users: nInt(k.active_users), active_users_foot: `${nInt(k.total_users)} total users`,
    companies: nInt(k.companies), companies_foot: 'by email domain',
    sessions: nInt(k.sessions), sessions_foot: `${nInt(k.messages)} messages`,
    queries: nInt(k.queries), queries_foot: k.query_success_pct == null ? 'success n/a' : `${Math.round(k.query_success_pct)}% success`,
    spend: usd(k.spend_usd), spend_foot: k.routing_savings_usd ? `routing saved ${usd(k.routing_savings_usd)}` : 'live LLM cost',
    quality: pctS(k.quality_pct), quality_foot: k.quality_pct == null ? 'judge not enabled' : 'judge accuracy',
    act_dau: nInt(act.dau), act_wau: nInt(act.wau), act_mau: nInt(act.mau),
    act_chats: nInt(k.sessions), act_messages: nInt(k.messages), act_tokens: fmt(tokens.total),
    act_tokens_foot: `in ${fmt(tokens.input)} · out ${fmt(tokens.output)} · cache ${fmt(tokens.cache)}`,
    roi_hours_v: `${nInt(roi.hours_saved)} h`, roi_hours_n: `${nInt(k.queries)} queries × ${bl.minutes_per_query} min`,
    roi_labour_v: usd(roi.labour_value_usd), roi_labour_n: `${nInt(roi.hours_saved)} h × $${bl.hourly_rate_usd}/h`,
    roi_autocost_v: usd(roi.automation_cost_usd), roi_autocost_n: 'LLM + run-rate',
    roi_net_v: usd(roi.net_usd), roi_net_n: 'value − automation cost',
    roi_roi_v: roi.roi_pct == null ? '—' : `${Math.round(roi.roi_pct)}%`,
    roi_roi_n: roi.payback_months == null ? 'payback n/a' : `payback ${roi.payback_months.toFixed(1)} mo`,
    roi_caption: roi.configured ? '' : `Estimated from a default baseline (${bl.minutes_per_query} min saved/query, $${bl.hourly_rate_usd}/h). Set ANALYTICS_* to refine.`,
    vd_time: `${nInt(vd.time_saved_hours)} h`, vd_money: usd(vd.money_saved_usd),
    vd_revenue: 'Not tracked',
    vd_risk: vd.risk_error_rate_pct == null ? '—' : `${vd.risk_error_rate_pct.toFixed(1)}%`,
    vd_risk_n: vd.risk_delta_pct == null ? 'Failed vs total this period' : `${vd.risk_delta_pct > 0 ? '+' : ''}${vd.risk_delta_pct.toFixed(1)}% vs prev period`,
    vd_csat: 'Not tracked',
    vd_prod: vd.productivity_pct == null ? 'Not tracked' : `+${Math.round(vd.productivity_pct)}%`,
    ad_dau: nInt(ad.dau), ad_wau: nInt(ad.wau), ad_mau: nInt(ad.mau), ad_repeat: pctS(ad.repeat_pct),
    ad_acc: pctS(ad.accuracy_pct), ad_instr: pctS(ad.instruction_coverage_pct), ad_fb: pctS(ad.feedback_pos_pct),
    ret_stick: pctS(ad.stickiness_pct), ret_res: pctS(ad.resolution_pct), ret_r30: pctS(ad.retention_30d_pct),
    ret_ttv: ad.ttv_days == null ? '—' : `${ad.ttv_days.toFixed(1)} d`,
    c_spend: usd(cost.total_usd), c_spend_foot: `${fmt(cost.tokens)} tokens`,
    c_routing: usd(cost.routing_savings_usd),
    c_perquery: cost.cost_per_query_usd == null ? '—' : usd(cost.cost_per_query_usd),
    c_peruser: cost.cost_per_user_usd == null ? '—' : usd(cost.cost_per_user_usd),
  })

  // ===== users tab KPIs =====
  const users = P.users || []
  const powerN = users.filter((u: any) => u.tier === 'pw').length
  const dormN = users.filter((u: any) => u.tier === 'dm').length
  const activeUsers = users.filter((u: any) => (u.sessions || 0) > 0)
  const avgHrs = activeUsers.length ? activeUsers.reduce((s: number, u: any) => s + (u.hours_saved || 0), 0) / activeUsers.length : 0
  Object.assign(K, { u_total: nInt(k.total_users), u_total_foot: `${nInt(k.active_users)} active in period`, u_power: nInt(powerN), u_dormant: nInt(dormN), u_avghrs: avgHrs.toFixed(1) })

  // ===== companies tab KPIs =====
  const cos = P.companies || []
  const topVal = [...cos].sort((a, b) => (b.net_usd || 0) - (a.net_usd || 0))[0]
  const topUsers = [...cos].sort((a, b) => (b.users || 0) - (a.users || 0))[0]
  const topQ = [...cos].sort((a, b) => (b.queries || 0) - (a.queries || 0))[0]
  Object.assign(K, {
    co_count: nInt(cos.length),
    co_top: topVal ? topVal.company : '—', co_top_foot: topVal ? `${usd(topVal.net_usd)} net · ${nInt(topVal.users)} users` : '',
    co_most: topUsers ? topUsers.company : '—', co_most_foot: topUsers ? `${nInt(topUsers.users)} users` : '',
    co_active: topQ ? topQ.company : '—', co_active_foot: topQ ? `${nInt(topQ.queries)} queries` : '',
  })

  // ===== agents tab KPIs =====
  const agents = P.agents || []
  const onN = agents.filter((a: any) => a.status === 'on').length
  const topAgent = [...agents].sort((a, b) => (b.runs || 0) - (a.runs || 0))[0]
  const succVals = agents.map((a: any) => a.success_pct).filter((v: any) => v != null)
  const latVals = agents.map((a: any) => a.avg_latency_ms).filter((v: any) => v != null)
  Object.assign(K, {
    ag_count: nInt(agents.length), ag_count_foot: `${onN} active · ${agents.length - onN} paused`,
    ag_top: topAgent ? topAgent.name : '—', ag_top_foot: topAgent ? `${nInt(topAgent.runs)} runs · ${usd(topAgent.cost_usd)}` : '',
    ag_res: succVals.length ? Math.round(succVals.reduce((s: number, v: number) => s + v, 0) / succVals.length) + '%' : '—',
    ag_lat: latVals.length ? (latVals.reduce((s: number, v: number) => s + v, 0) / latVals.length / 1000).toFixed(1) + 's' : '—',
  })

  // ===== connectors tab KPIs =====
  const conns = P.connectors || []
  const sharedN = conns.filter((c: any) => c.auth !== 'per_user').length
  const mostUsed = [...conns].sort((a, b) => (b.users || 0) - (a.users || 0))[0]
  const cSucc = conns.map((c: any) => c.success_pct).filter((v: any) => v != null)
  Object.assign(K, {
    cn_count: nInt(conns.length), cn_count_foot: `${sharedN} shared · ${conns.length - sharedN} per-user token`,
    cn_most: mostUsed ? mostUsed.name : '—', cn_most_foot: mostUsed ? `${nInt(mostUsed.users)} users · ${nInt(mostUsed.queries)} queries` : '',
    cn_succ: cSucc.length ? Math.round(cSucc.reduce((s: number, v: number) => s + v, 0) / cSucc.length) + '%' : '—',
    cn_queries: nInt(conns.reduce((s: number, c: any) => s + (c.queries || 0), 0)),
  })

  // ===== questions tab KPIs =====
  const Q = P.questions || { total: 0, clusters: [], failed: [], trend: { weeks: [], series: [] } }
  const topCluster = [...(Q.clusters || [])].sort((a, b) => (b.count || 0) - (a.count || 0))[0]
  const failedN = (Q.failed || []).reduce((s: number, f: any) => s + (f.count || 1), 0)
  Object.assign(K, {
    q_classified: nInt(Q.total), q_classified_foot: `into ${(Q.clusters || []).length} intent clusters`,
    q_top: topCluster ? topCluster.name : '—', q_top_foot: topCluster ? `${Math.round(topCluster.pct || 0)}% of questions` : '',
    q_failed: Q.total ? Math.round(failedN / Q.total * 100) + '%' : '0%', q_failed_foot: `${nInt(failedN)} chats · review`,
    q_clusters: nInt((Q.clusters || []).length),
  })

  setK()
  setBar('ad_acc_bar', ad.accuracy_pct); setBar('ad_instr_bar', ad.instruction_coverage_pct); setBar('ad_fb_bar', ad.feedback_pos_pct)
  const roiCap = document.querySelector('[data-k="roi_caption"]') as HTMLElement | null
  if (roiCap && !K.roi_caption) roiCap.style.display = 'none'
  const vdRev = document.querySelector('[data-k="vd_revenue"]') as HTMLElement | null; if (vdRev) vdRev.style.color = 'var(--ink3)'
  const vdCsat = document.querySelector('[data-k="vd_csat"]') as HTMLElement | null; if (vdCsat) vdCsat.style.color = 'var(--ink3)'
  const vdProd = document.querySelector('[data-k="vd_prod"]') as HTMLElement | null; if (vdProd && vd.productivity_pct == null) vdProd.style.color = 'var(--ink3)'

  // ===== activity sparklines (from real series) =====
  const series = act.series || {}
  const seriesVals = (key: string) => {
    const arr = (series[key] || []).map((p: any) => Number(p.v) || 0)
    return arr.length ? arr : [0, 0]
  }
  document.querySelectorAll('.card.kpi.clik').forEach(card => {
    const el = card as HTMLElement
    const sk = el.dataset.series || ''
    el.querySelector('.spk')?.setAttribute('data-s', seriesVals(sk).join(','))
  })
  document.querySelectorAll('.appx .spk').forEach(el => {
    let d = ((el as HTMLElement).dataset.s || '0').split(',').map(Number)
    if (d.length < 2) d = [d[0] || 0, d[0] || 0]
    const col = AC[(el as HTMLElement).dataset.c || 'blue'] || AC.blue
    const W = 150, H = 30, mx = Math.max(...d), mn = Math.min(...d); const rng = (mx - mn) || 1
    const pts = d.map((v, i) => [i / (d.length - 1) * W, H - 2 - (H - 6) * (v - mn) / rng])
    const path = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
    const area = path + `L${W} ${H}L0 ${H}Z`
      ; (el as HTMLElement).innerHTML = `<svg width="100%" height="30" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="display:block;margin-top:8px">
      <path d="${area}" fill="${col}" opacity=".10"/>
      <path d="${path}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="2.4" fill="${col}"/></svg>`
  })

  const tierName: Record<string, string> = { pw: 'Power', rg: 'Regular', dm: 'Dormant' }
  byId('utab')!.innerHTML = users.length ? users.map((u: any) => `<tr>
    <td><div class="who"><div class="av" style="background:${avColor(u.name || u.email)}">${initials(u.name || u.email)}</div>
      <div><div class="nm">${esc(u.name || u.email)}</div><div class="em">${esc(u.email)}</div></div></div></td>
    <td><span class="tag">${esc(u.company)}</span></td><td class="mut">${esc(u.dept || '—')}</td>
    <td class="mut tnum">${fmtDate(u.joined)}</td><td class="mut">${fmtDate(u.last_active)}</td>
    <td class="r tnum">${nInt(u.sessions)}</td><td class="r tnum">${nInt(u.queries)}</td>
    <td class="r tnum">${usd(u.cost_usd)}</td><td class="r tnum">${(u.hours_saved || 0).toFixed(1)} h</td>
    <td><span class="pill ${u.tier}">${tierName[u.tier] || u.tier}</span></td></tr>`).join('') : `<tr><td colspan="10">${empty('No users active in this period yet.')}</td></tr>`

  byId('ctab')!.innerHTML = cos.length ? cos.map((c: any) => `<tr>
    <td><b>${esc(c.company)}</b></td><td class="mut tnum">${esc(c.domain)}</td><td class="r tnum">${nInt(c.users)}</td><td class="r tnum">${nInt(c.active)}</td>
    <td>${(c.connectors || []).map((x: string) => `<span class="tag" style="margin-right:3px">${esc(x)}</span>`).join('') || '<span class="mut3">—</span>'}</td>
    <td class="r tnum">${nInt(c.queries)}</td><td class="r tnum">${(c.hours_saved || 0).toFixed(0)}</td><td class="r tnum"><b>${usd(c.net_usd)}</b></td>
    <td class="r"><span class="pill rg">${c.roi_pct == null ? '—' : Math.round(c.roi_pct) + '%'}</span></td></tr>`).join('') : `<tr><td colspan="9">${empty('No company activity yet.')}</td></tr>`

  const maxNet = Math.max(1, ...cos.map((c: any) => Math.abs(c.net_usd || 0)))
  byId('ovco')!.innerHTML = cos.length ? cos.slice(0, 5).map((c: any) => `<div class="hbar"><div class="lab">${esc(c.company)}</div>
     <div class="track"><div class="fill" style="width:${Math.round(Math.abs(c.net_usd || 0) / maxNet * 100)}%;background:${AC.blue}">${usd(c.net_usd)}</div></div></div>`).join('') : empty('No data yet.')

  byId('conntab')!.innerHTML = conns.length ? conns.map((c: any) => {
    const names = c.user_names || []
    const av = names.slice(0, 4).map((n: string) => `<div class="av" style="background:${avColor(n)}" title="${esc(n)}">${initials(n)}</div>`).join('')
    const more = (c.users || 0) > 4 ? `<div class="more">+${c.users - 4}</div>` : ''
    return `<div class="conn"><div class="ci">${iconFor(c.name, c.kind)}</div>
     <div style="flex:1"><div class="cn">${esc(c.name)}</div><div class="cm">${c.auth === 'per_user' ? 'per-user token' : 'shared'} · ${pctS(c.success_pct)} success · last used ${fmtDate(c.last_used)}</div></div>
     <div class="stack">${av}${more}</div></div>`
  }).join('') : empty('No connectors used yet.')

  const maxCQ = Math.max(1, ...conns.map((c: any) => c.queries || 0))
  byId('connbars')!.innerHTML = conns.length ? conns.map((c: any) => `<div class="hbar"><div class="lab" style="width:130px">${iconFor(c.name, c.kind)} ${esc((c.name || '').split(' (')[0])}</div>
     <div class="track"><div class="fill" style="width:${Math.round((c.queries || 0) / maxCQ * 100)}%;background:${AC.violet}">${nInt(c.queries)}</div></div>
     <div class="val">${usd(c.cost_usd)}</div></div>`).join('') : empty('No data yet.')

  const maxCU = Math.max(1, ...conns.map((c: any) => c.users || 0))
  byId('ovcn')!.innerHTML = conns.length ? conns.slice(0, 5).map((c: any) => `<div class="hbar"><div class="lab" style="width:120px">${iconFor(c.name, c.kind)} ${esc((c.name || '').split(' (')[0])}</div>
     <div class="track"><div class="fill" style="width:${Math.round((c.users || 0) / maxCU * 100)}%;background:${AC.teal}">${nInt(c.users)}</div></div></div>`).join('') : empty('No data yet.')

  const BA = P.automation || []
  byId('ba')!.innerHTML = BA.length ? BA.map((b: any) => {
    const mo = b.manual_min || 0, ai = b.ai_min || 0; const cut = mo ? Math.round((1 - ai / mo) * 100) : 0
    return `<div style="padding:9px 0;border-bottom:1px solid var(--line2)">
     <div class="between" style="font-size:12.5px"><b>${esc(b.workflow)}</b>${b.dept ? `<span class="tag">${esc(b.dept)}</span>` : ''}</div>
     <div class="flex" style="align-items:center;gap:10px;margin-top:7px">
       <div class="mut3 tnum" style="width:66px;font-size:12px">${mo} min</div>
       <div class="track" style="flex:1;height:14px;background:var(--bad-soft);border-radius:5px;position:relative;overflow:hidden">
         <div style="position:absolute;left:0;top:0;bottom:0;width:${mo ? ai / mo * 100 : 0}%;background:var(--good);border-radius:5px"></div></div>
       <div class="tnum" style="width:60px;text-align:right;font-size:12px;color:var(--good);font-weight:650">${ai} min</div>
       <div class="tnum" style="width:44px;text-align:right;font-size:12px;font-weight:700">−${cut}%</div>
     </div></div>`
  }).join('') : empty('Not enough runs to estimate automation yet.')

  // ===== questions =====
  const clusters = (Q.clusters || []).map((c: any) => [c.name, c.pct || 0, CLUSTER_COLOR[c.key] || AC.slate, (c.examples || [])])
  byId('qclusters')!.innerHTML = clusters.length ? clusters.slice(0, 8).map((q: any) => `
   <div class="qc"><div class="qh"><span class="qd" style="background:${q[2]}"></span><span class="qn">${esc(q[0])}</span><span class="qcount tnum">${Math.round(q[1])}%</span></div>
   ${(q[3].length ? q[3] : ['—']).map((e: string) => `<div class="qex"><span class="qq">“</span>${esc(e)}<span class="qq">”</span></div>`).join('')}</div>`).join('') : empty('No questions classified in this period yet.')

  function donut(el: HTMLElement, data: any[], total: number, size = 118) {
    if (!data.length || total <= 0) { el.innerHTML = empty('No data'); return }
    const R = size / 2, r = R * 0.62; let a = -Math.PI / 2; const tot = data.reduce((s, d) => s + d[1], 0) || 1
    let p = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="spark">`
    data.forEach(d => {
      const ang = d[1] / tot * Math.PI * 2, a2 = a + ang
      const x1 = R + R * Math.cos(a), y1 = R + R * Math.sin(a), x2 = R + R * Math.cos(a2), y2 = R + R * Math.sin(a2)
      const xi2 = R + r * Math.cos(a2), yi2 = R + r * Math.sin(a2), xi1 = R + r * Math.cos(a), yi1 = R + r * Math.sin(a)
      const large = ang > Math.PI ? 1 : 0
      p += `<path d="M${x1} ${y1} A${R} ${R} 0 ${large} 1 ${x2} ${y2} L${xi2} ${yi2} A${r} ${r} 0 ${large} 0 ${xi1} ${yi1} Z" fill="${d[2]}"/>`
      a = a2
    })
    p += `<circle cx="${R}" cy="${R}" r="${r - 1}" fill="var(--panel)"/>`
    p += `<text x="${R}" y="${R - 3}" text-anchor="middle" font-size="20" font-weight="700" fill="var(--ink)">${fmt(total)}</text>`
    p += `<text x="${R}" y="${R + 13}" text-anchor="middle" font-size="9.5" fill="var(--ink3)">chats</text></svg>`
    el.innerHTML = p
  }
  donut(byId('qdonut') as HTMLElement, clusters, Q.total || 0)
  byId('qleg')!.innerHTML = clusters.slice(0, 6).map((q: any) => `
   <div class="lg"><span class="sw" style="background:${q[2]}"></span>${esc(q[0])}<span class="lgp">${Math.round(q[1])}%</span></div>`).join('')

  // intent trend from real weekly series
  ;(function () {
    const el = byId('qtrend') as HTMLElement
    const tr = Q.trend || { weeks: [], series: [] }
    const svgSeries = (tr.series || []).slice(0, 4).map((s: any, i: number) => [s.name, [AC.blue, AC.violet, AC.green, AC.amber][i] || AC.slate, (s.values || [])])
    if (!svgSeries.length || !(tr.weeks || []).length) { el.innerHTML = empty('No trend data yet.'); return }
    const W = 440, H = 180, pad = 24; const weeks = Math.max(2, (tr.weeks || []).length)
    const max = Math.max(1, ...svgSeries.flatMap((s: any) => s[2]))
    let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" class="spark">`
    for (let i = 0; i <= 4; i++) { const y = pad + (H - 2 * pad) * i / 4; s += `<line x1="${pad}" y1="${y}" x2="${W - 8}" y2="${y}" stroke="var(--line2)"/>` }
    svgSeries.forEach((se: any) => {
      const vals = se[2].length ? se[2] : [0, 0]
      const pts = vals.map((v: number, i: number) => [pad + (W - pad - 8) * i / (weeks - 1), H - pad - (H - 2 * pad) * v / max])
      const d = pts.map((p: any, i: number) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
      s += `<path d="${d}" fill="none" stroke="${se[1]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`
      if (pts.length) s += `<circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="3" fill="${se[1]}"/>`
    })
    s += `</svg>`
    el.innerHTML = s + `<div class="flex" style="gap:14px;margin-top:8px;flex-wrap:wrap">${svgSeries.map((se: any) => `<span class="lg"><span class="sw" style="background:${se[1]}"></span>${esc(se[0])}</span>`).join('')}</div>`
  })()

  const REASON: Record<string, string> = { error: 'Run errored', no_answer: 'No answer produced', low_confidence: 'Low confidence' }
  const QF = Q.failed || []
  byId('qfail')!.innerHTML = QF.length ? QF.map((q: any) => `
   <div style="padding:10px 0;border-bottom:1px solid var(--line2)">
    <div style="font-size:12.5px;font-weight:550">“${esc(q.q)}”</div>
    <div class="between" style="margin-top:5px"><span class="tag" style="background:var(--bad-soft);color:var(--bad)">${esc(REASON[q.reason] || q.reason)}</span>
     <span class="mut3" style="font-size:11.5px">${esc(q.company || '—')} · ${nInt(q.count)}×</span></div></div>`).join('') : empty('No failed questions — nice.')

  // ===== cost =====
  const cm = cost.by_model || []
  const maxCM = Math.max(1, ...cm.map((m: any) => m.value_usd || 0))
  byId('costmodel')!.innerHTML = cm.length ? cm.map((m: any, i: number) => `<div class="hbar"><div class="lab">${esc(m.name)}</div><div class="track"><div class="fill" style="width:${Math.round((m.value_usd || 0) / maxCM * 100)}%;background:${[AC.blue, AC.teal, AC.violet, AC.amber, AC.slate][i % 5]}">${usd(m.value_usd)}</div></div></div>`).join('') : empty('No model spend yet.')
  const cc = cost.by_company || []
  const maxCC = Math.max(1, ...cc.map((m: any) => m.value_usd || 0))
  byId('costco')!.innerHTML = cc.length ? cc.map((m: any, i: number) => `<div class="hbar"><div class="lab">${esc(m.name)}</div><div class="track"><div class="fill" style="width:${Math.round((m.value_usd || 0) / maxCC * 100)}%;background:${[AC.blue, AC.violet, AC.green, AC.amber, AC.slate][i % 5]}">${usd(m.value_usd)}</div></div></div>`).join('') : empty('No company spend yet.')

  const auto = roi.automation_cost_usd || 0, lab = roi.labour_value_usd || 0
  const perDollar = auto > 0 ? (lab / auto).toFixed(0) : '—'
  const hRatio = Math.max(20, Math.min(240, lab > 0 && auto > 0 ? (lab / auto) * 20 : 20))
  byId('cvv')!.innerHTML = `
   <div class="flex" style="align-items:flex-end;gap:40px;padding:10px 0">
    <div style="text-align:center"><div style="width:80px;height:20px;background:var(--bad);border-radius:6px 6px 0 0;margin:0 auto"></div>
     <div style="font-size:13px;font-weight:700;margin-top:8px">${usd(auto)}</div><div class="mut3" style="font-size:11px">Automation cost</div></div>
    <div style="text-align:center"><div style="width:80px;height:${hRatio}px;background:linear-gradient(var(--good),#0e7a38);border-radius:6px 6px 0 0;margin:0 auto"></div>
     <div style="font-size:13px;font-weight:700;margin-top:8px">${usd(lab)}</div><div class="mut3" style="font-size:11px">Labour value freed</div></div>
    <div style="flex:1">
     <div style="font-size:30px;font-weight:750;letter-spacing:-.02em">${perDollar === '—' ? '—' : '$' + perDollar}<span style="font-size:16px;color:var(--ink3)"> returned per $1 spent</span></div>
     <div class="mut mt14" style="font-size:13px;max-width:440px">Net benefit <b>${usd(roi.net_usd)}</b> over the period. ${roi.payback_months == null ? 'Payback not yet computable.' : `Payback <b>${roi.payback_months.toFixed(1)} months</b>.`} ${roi.configured ? '' : 'Estimated from a default baseline — configure ANALYTICS_* env to refine.'}</div>
    </div>
   </div>`

  // ===== agents table + charts =====
  byId('atab')!.innerHTML = agents.length ? agents.map((a: any) => {
    const q = a.quality_pct; const qc = q == null ? 'var(--ink3)' : q >= 85 ? 'var(--good)' : q >= 75 ? 'var(--warn)' : 'var(--bad)'
    const dotCol = a.status === 'on' ? 'var(--good)' : 'var(--ink3)'
    const tc = AGENT_TYPE_COLOR[a.type] || AC.slate
    return `<tr>
     <td><b><span style="width:7px;height:7px;border-radius:50%;background:${dotCol};display:inline-block;margin-right:5px"></span>${esc(a.name)}</b></td>
     <td><span class="tag" style="background:${tc}1a;color:${tc}">${esc(a.type || '—')}</span></td>
     <td class="mut" style="font-size:12px">${(a.connectors || []).map(esc).join(' · ') || '—'}</td>
     <td class="r tnum">${nInt(a.users)}</td><td class="r tnum">${nInt(a.runs)}</td><td class="r tnum">${pctS(a.success_pct)}</td>
     <td class="r tnum">${a.avg_latency_ms == null ? '—' : (a.avg_latency_ms / 1000).toFixed(1) + 's'}</td><td class="r tnum">${usd(a.cost_usd)}</td><td class="r tnum">${(a.hours_saved || 0).toFixed(0)} h</td>
     <td class="r tnum" style="color:${qc};font-weight:700">${pctS(a.quality_pct)}</td>
     <td class="mut" style="font-size:12px">${fmtDate(a.last_run)}</td></tr>`
  }).join('') : `<tr><td colspan="11">${empty('No agent runs in this period yet.')}</td></tr>`

  const maxRuns = Math.max(1, ...agents.map((a: any) => a.runs || 0))
  byId('abars')!.innerHTML = agents.length ? [...agents].sort((a, b) => (b.runs || 0) - (a.runs || 0)).slice(0, 7).map((a: any) => `<div class="hbar"><div class="lab" style="width:150px">${esc(a.name)}</div>
    <div class="track"><div class="fill" style="width:${Math.round((a.runs || 0) / maxRuns * 100)}%;background:${AGENT_TYPE_COLOR[a.type] || AC.slate}">${nInt(a.runs)}</div></div>
    <div class="val">${usd(a.cost_usd)}</div></div>`).join('') : empty('No agent runs yet.')

  ;(function () {
    const el = byId('aquality') as HTMLElement
    const withQ = agents.filter((a: any) => a.quality_pct != null && a.runs)
    if (!withQ.length) { el.innerHTML = empty('No quality scores yet (judge off or no runs).'); return }
    const W = 440, H = 200, pad = 34
    let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" class="spark">`
    s += `<line x1="${pad}" y1="${H - pad}" x2="${W - 8}" y2="${H - pad}" stroke="var(--line)"/>`
    s += `<line x1="${pad}" y1="8" x2="${pad}" y2="${H - pad}" stroke="var(--line)"/>`
    const gy = H - pad - (H - pad - 8) * (75 - 70) / (100 - 70)
    s += `<line x1="${pad}" y1="${gy}" x2="${W - 8}" y2="${gy}" stroke="var(--bad)" stroke-dasharray="3 3" opacity=".5"/>`
    s += `<text x="${W - 10}" y="${gy - 4}" text-anchor="end" font-size="9" fill="var(--bad)">quality floor 75%</text>`
    const maxR = Math.max(1, ...withQ.map((a: any) => a.runs || 0))
    withQ.forEach((a: any) => {
      const runs = a.runs, q = a.quality_pct
      const x = pad + (W - pad - 16) * runs / maxR, y = H - pad - (H - pad - 8) * (q - 70) / (100 - 70)
      const r = 6 + runs / maxR * 14; const c = q < 75 ? AC.red : (AGENT_TYPE_COLOR[a.type] || AC.slate)
      s += `<circle cx="${x}" cy="${Math.max(10, y)}" r="${r}" fill="${c}" opacity=".7"/>`
    })
    s += `<text x="${pad}" y="${H - 8}" font-size="9" fill="var(--ink3)">→ runs (volume)</text>`
    s += `<text x="6" y="16" font-size="9" fill="var(--ink3)">↑ quality</text>`
    s += `</svg>`; el.innerHTML = s
  })()

  // ===== cohort retention =====
  const COH = P.retention_cohorts || []
  function heat(v: any) { if (v === null || v === undefined) return ''; const t = (v - 55) / 45; return `rgba(37,99,235,${(0.15 + t * 0.75).toFixed(2)})` }
  byId('cohort')!.innerHTML = COH.length ? COH.map((r: any) => `<tr>
   <td class="rowlab">${esc(r.label || r.cohort)}</td><td style="text-align:center;color:var(--ink2);font-weight:600">${nInt(r.users)}</td>
   ${(r.weeks || []).map((v: any) => v === null || v === undefined ? `<td><div class="cell" style="background:var(--line2)"></div></td>`
    : `<td><div class="cell" style="background:${heat(v)};color:${v > 75 ? '#fff' : 'var(--ink)'}">${Math.round(v)}%</div></td>`).join('')}</tr>`).join('') : `<tr><td colspan="8">${empty('Not enough history for cohorts yet.')}</td></tr>`

  // ===== activation funnel =====
  const FUN = P.funnel || []
  const funMax = FUN.length ? Math.max(1, ...FUN.map((f: any) => f.n || 0)) : 1
  const funColors = [AC.slate, AC.blue, AC.violet, AC.teal, AC.green, AC.amber]
  byId('funnel')!.innerHTML = FUN.length ? FUN.map((f: any, i: number) => {
    const w = Math.round((f.n || 0) / funMax * 100); const drop = i && FUN[i - 1].n ? Math.round((FUN[i - 1].n - f.n) / FUN[i - 1].n * 100) : 0
    return `<div class="fstep"><div class="fbar" style="width:${Math.max(8, w)}%;background:${funColors[i % 6]}">${nInt(f.n)}</div>
    <div class="fmeta">${esc(f.step)} <span class="mut3">${w}%</span> ${drop > 0 ? `<span class="fdrop">−${drop}%</span>` : ''}</div></div>`
  }).join('') : empty('No funnel data yet.')

  // ---------- trend modal ----------
  const ov = byId('ov')!
  function bigChart(el: HTMLElement, data: number[], col: string, unit: string) {
    const W = 660, H = 280, px = 44, py = 26; const mx = Math.max(...data), mn = Math.min(...data); const rng = (mx - mn) || 1
    const X = i => px + (W - px - 14) * i / (data.length - 1), Y = v => H - py - (H - 2 * py) * (v - mn) / rng
    let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" class="spark">`
    for (let i = 0; i <= 4; i++) {
      const gv = mn + rng * i / 4, y = Y(gv)
      s += `<line x1="${px}" y1="${y}" x2="${W - 14}" y2="${y}" stroke="var(--line2)"/>`
      s += `<text x="${px - 8}" y="${y + 3}" text-anchor="end" font-size="10" fill="var(--ink3)">${fmt(Math.round(gv))}</text>`
    }
    const pts = data.map((v, i) => [X(i), Y(v)])
    const path = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
    s += `<path d="${path}L${X(data.length - 1)} ${H - py}L${px} ${H - py}Z" fill="${col}" opacity=".10"/>`
    s += `<path d="${path}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`
    pts.forEach((p, i) => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="${i === pts.length - 1 ? 4 : 2.6}" fill="${col}"/>` })
    s += `<text x="${px}" y="${H - 6}" font-size="10" fill="var(--ink3)">start</text>`
    s += `<text x="${W - 14}" y="${H - 6}" text-anchor="end" font-size="10" fill="var(--ink3)">now</text>`
    s += `</svg>`; el.innerHTML = s
  }
  let curSeries = [], curCol = AC.blue, curUnit = ''
  function openModal(card) {
    const spk = card.querySelector('.spk')
    curSeries = spk.dataset.s.split(',').map(Number); curCol = AC[spk.dataset.c] || AC.blue; curUnit = card.dataset.u || ''
    document.getElementById('mtitle').textContent = card.dataset.t
    document.getElementById('msub').textContent = 'Trend over the selected period · ' + curUnit
    renderModal(12)
    document.querySelectorAll('#mseg button').forEach(b => b.classList.toggle('on', b.dataset.r === '12'))
    ov.classList.add('on')
  }
  function renderModal(range) {
    let d = curSeries.slice()
    if (range == 7) d = d.slice(-7)
    if (d.length < 2) d = [d[0] || 0, d[0] || 0]
    const cur = d[d.length - 1], first = d[0]; const chg = first ? Math.round((cur - first) / first * 100) : 0
    const peak = Math.max(...d), avg = Math.round(d.reduce((a, b) => a + b, 0) / d.length)
    document.getElementById('mstat').innerHTML = `
      <div><div class="v tnum" style="color:${curCol}">${fmt(cur)}</div><div class="k">Current</div></div>
      <div><div class="v tnum">${chg >= 0 ? '+' : ''}${chg}%</div><div class="k">vs period start</div></div>
      <div><div class="v tnum">${fmt(peak)}</div><div class="k">Peak</div></div>
      <div><div class="v tnum">${fmt(avg)}</div><div class="k">Average</div></div>`
    bigChart(document.getElementById('mchart'), d, curCol, curUnit)
  }
  document.querySelectorAll('.card.kpi.clik').forEach(c => c.addEventListener('click', () => openModal(c)))
  document.getElementById('xbtn').addEventListener('click', () => ov.classList.remove('on'))
  ov.addEventListener('click', e => { if (e.target === ov) ov.classList.remove('on') })
  document.addEventListener('keydown', e => { if (e.key === 'Escape') ov.classList.remove('on') })
  document.getElementById('mseg').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return
    document.querySelectorAll('#mseg button').forEach(x => x.classList.remove('on')); b.classList.add('on')
    renderModal(parseInt(b.dataset.r))
  })

  // ---------- tabs ----------
  document.getElementById('tabs').addEventListener('click', e => {
    const b = e.target.closest('.tab'); if (!b) return
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'))
    document.querySelectorAll('.view').forEach(v => v.classList.remove('on'))
    b.classList.add('on'); document.getElementById('v-' + b.dataset.v).classList.add('on')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  })
})
</script>

<!--
  NON-scoped style block on purpose: the JS above builds tables/charts/rows via
  innerHTML AFTER mount. Scoped styles add a compile-time data-attribute that
  those dynamically-created nodes never receive, so scoping would drop styling
  on all injected content. Instead EVERY rule is namespaced under `.appx` (the
  single root element) so nothing leaks globally. CSS custom properties live on
  `.appx`; the app's theme toggle stamps data-theme on <html> (`:root`), so the
  dark/light overrides are keyed `:root[data-theme=...] .appx` (plain descendant
  selectors — no `:global()` wrapper, which only exists in scoped blocks).
-->
<style>
.appx {
  --bg:#f7f8fa; --panel:#ffffff; --ink:#0f172a; --ink2:#475569; --ink3:#94a3b8;
  --line:#e6e9ef; --line2:#eef1f5; --accent:#2563eb; --accent-soft:#eff4ff;
  --good:#16a34a; --good-soft:#eafaf0; --warn:#d97706; --warn-soft:#fff5e8;
  --bad:#dc2626; --bad-soft:#fdecec; --violet:#7c3aed; --violet-soft:#f3efff;
  --teal:#0d9488; --amber:#d97706; --pink:#db2777;
  --shadow:0 1px 2px rgba(15,23,42,.04),0 1px 3px rgba(15,23,42,.06);
  --radius:12px;
  background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:14px; line-height:1.45; -webkit-font-smoothing:antialiased;
}
.appx *{box-sizing:border-box}
@media (prefers-color-scheme:dark){
  .appx{
    --bg:#0b0e14; --panel:#121722; --ink:#e8ecf3; --ink2:#a3adbd; --ink3:#6b7686;
    --line:#232b39; --line2:#1a212c; --accent:#5b8cff; --accent-soft:#16203a;
    --good:#3ddc84; --good-soft:#122a1e; --warn:#f0b34e; --warn-soft:#2c2213;
    --bad:#f87171; --bad-soft:#2c1717; --violet:#a78bfa; --violet-soft:#211a3a;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 2px 6px rgba(0,0,0,.3);
  }
}
:root[data-theme="light"] .appx{
  --bg:#f7f8fa; --panel:#ffffff; --ink:#0f172a; --ink2:#475569; --ink3:#94a3b8;
  --line:#e6e9ef; --line2:#eef1f5; --accent:#2563eb; --accent-soft:#eff4ff;
  --good:#16a34a; --good-soft:#eafaf0; --warn:#d97706; --warn-soft:#fff5e8;
  --bad:#dc2626; --bad-soft:#fdecec; --violet:#7c3aed; --violet-soft:#f3efff;
  --shadow:0 1px 2px rgba(15,23,42,.04),0 1px 3px rgba(15,23,42,.06);
}
:root[data-theme="dark"] .appx{
  --bg:#0b0e14; --panel:#121722; --ink:#e8ecf3; --ink2:#a3adbd; --ink3:#6b7686;
  --line:#232b39; --line2:#1a212c; --accent:#5b8cff; --accent-soft:#16203a;
  --good:#3ddc84; --good-soft:#122a1e; --warn:#f0b34e; --warn-soft:#2c2213;
  --bad:#f87171; --bad-soft:#2c1717; --violet:#a78bfa; --violet-soft:#211a3a;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 2px 6px rgba(0,0,0,.3);
}
.appx .wrap{max-width:1280px;margin:0 auto;padding:22px 24px 60px}
.appx h1,.appx h2,.appx h3{margin:0;font-weight:650;letter-spacing:-.01em;color:var(--ink)}
.appx .mut{color:var(--ink2)} .appx .mut3{color:var(--ink3)}
.appx .tnum{font-variant-numeric:tabular-nums}

/* header */
.appx .top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:6px;flex-wrap:wrap}
.appx .title h1{font-size:22px}
.appx .title p{margin:4px 0 0;color:var(--ink2);font-size:13px}
.appx .crumb{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);font-weight:600}
.appx .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.appx .ctl{display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:7px 11px;font-size:13px;color:var(--ink);box-shadow:var(--shadow);cursor:pointer}
.appx .ctl svg{width:14px;height:14px;opacity:.6}
.appx .ctl b{font-weight:600}
.appx .badge{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:20px;letter-spacing:.02em}
.appx .badge.live{background:var(--good-soft);color:var(--good)}
.appx .badge.new{background:var(--accent-soft);color:var(--accent)}

/* tabs */
.appx .tabs{display:flex;gap:2px;margin:18px 0 20px;border-bottom:1px solid var(--line);overflow-x:auto}
.appx .tab{padding:10px 15px;font-size:13.5px;font-weight:550;color:var(--ink2);border:none;background:none;
  cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;display:flex;align-items:center;gap:7px}
.appx .tab:hover{color:var(--ink)}
.appx .tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.appx .tab svg{width:15px;height:15px}
.appx .tab .dot{width:6px;height:6px;border-radius:50%;background:var(--good)}

.appx .view{display:none} .appx .view.on{display:block;animation:appxfade .25s ease}
@keyframes appxfade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

/* grid + cards */
.appx .grid{display:grid;gap:14px}
.appx .g6{grid-template-columns:repeat(6,1fr)}
.appx .g4{grid-template-columns:repeat(4,1fr)}
.appx .g3{grid-template-columns:repeat(3,1fr)}
.appx .g2{grid-template-columns:repeat(2,1fr)}
@media(max-width:1080px){.appx .g6{grid-template-columns:repeat(3,1fr)}.appx .g4{grid-template-columns:repeat(2,1fr)}}
@media(max-width:720px){.appx .g6,.appx .g4,.appx .g3,.appx .g2{grid-template-columns:1fr}}
.appx .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px;box-shadow:var(--shadow)}
.appx .card.pad0{padding:0;overflow:hidden}
.appx .card h3{font-size:14px}
.appx .card .sub{font-size:12px;color:var(--ink3);margin-top:2px}
.appx .chd{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:14px}

/* kpi */
.appx .kpi .lbl{font-size:12px;color:var(--ink2);font-weight:550;display:flex;align-items:center;gap:5px}
.appx .kpi .big{font-size:27px;font-weight:700;letter-spacing:-.02em;margin-top:9px;line-height:1}
.appx .kpi .foot{font-size:11.5px;margin-top:8px;color:var(--ink3);display:flex;align-items:center;gap:5px}
.appx .delta{font-weight:650;display:inline-flex;align-items:center;gap:2px}
.appx .delta.up{color:var(--good)} .appx .delta.down{color:var(--bad)} .appx .delta.flat{color:var(--ink3)}
.appx .ic{width:16px;height:16px;flex:none}

/* ROI band */
.appx .roi{background:linear-gradient(135deg,var(--accent) 0%,#1e40af 100%);border-radius:var(--radius);
  padding:20px 22px;color:#fff;position:relative;overflow:hidden}
:root[data-theme="dark"] .appx .roi{background:linear-gradient(135deg,#1e3a8a 0%,#0f2557 100%)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]) .appx .roi{background:linear-gradient(135deg,#1e3a8a 0%,#0f2557 100%)}}
.appx .roi .eyebrow{font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.8;font-weight:600}
.appx .roi-row{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:14px}
@media(max-width:820px){.appx .roi-row{grid-template-columns:repeat(2,1fr)}}
.appx .roi-cell .v{font-size:24px;font-weight:750;letter-spacing:-.02em}
.appx .roi-cell .k{font-size:11.5px;opacity:.82;margin-top:3px}
.appx .roi-cell .n{font-size:10.5px;opacity:.65;margin-top:2px}
.appx .roi-div{width:1px;background:rgba(255,255,255,.18)}

/* value dimension tiles */
.appx .vd{display:flex;gap:12px;align-items:flex-start}
.appx .vd .ico{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;flex:none}
.appx .vd .ico svg{width:19px;height:19px}
.appx .vd .v{font-size:19px;font-weight:700;letter-spacing:-.01em}
.appx .vd .k{font-size:12px;color:var(--ink2);font-weight:550}
.appx .vd .n{font-size:11px;color:var(--ink3);margin-top:3px}
.appx .tint-blue{background:var(--accent-soft);color:var(--accent)}
.appx .tint-green{background:var(--good-soft);color:var(--good)}
.appx .tint-violet{background:var(--violet-soft);color:var(--violet)}
.appx .tint-amber{background:var(--warn-soft);color:var(--warn)}
.appx .tint-red{background:var(--bad-soft);color:var(--bad)}
.appx .tint-teal{background:#e6f7f4;color:var(--teal)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]) .appx .tint-teal{background:#0f2a27}}
:root[data-theme="dark"] .appx .tint-teal{background:#0f2a27}

/* table */
.appx table{width:100%;border-collapse:collapse;font-size:13px}
.appx thead th{text-align:left;font-size:11px;letter-spacing:.03em;text-transform:uppercase;color:var(--ink3);
  font-weight:600;padding:10px 14px;border-bottom:1px solid var(--line);white-space:nowrap;background:var(--panel)}
.appx tbody td{padding:11px 14px;border-bottom:1px solid var(--line2);vertical-align:middle}
.appx tbody tr:last-child td{border-bottom:none}
.appx tbody tr:hover{background:var(--line2)}
.appx th.r,.appx td.r{text-align:right}
.appx .who{display:flex;align-items:center;gap:10px}
.appx .av{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:11px;font-weight:650;flex:none}
.appx .who .nm{font-weight:600;font-size:13px}
.appx .who .em{font-size:11.5px;color:var(--ink3)}
.appx .pill{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px}
.appx .pill.pw{background:var(--good-soft);color:var(--good)}
.appx .pill.rg{background:var(--accent-soft);color:var(--accent)}
.appx .pill.dm{background:var(--line2);color:var(--ink3)}
.appx .tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;background:var(--line2);color:var(--ink2);font-weight:550}
.appx .bar-mini{height:6px;border-radius:4px;background:var(--line);overflow:hidden;min-width:60px}
.appx .bar-mini>i{display:block;height:100%;border-radius:4px;background:var(--accent)}

/* bars chart */
.appx .hbar{display:flex;align-items:center;gap:12px;padding:8px 0}
.appx .hbar .lab{width:150px;font-size:12.5px;color:var(--ink);display:flex;align-items:center;gap:8px;flex:none}
.appx .hbar .track{flex:1;height:22px;background:var(--line2);border-radius:6px;overflow:hidden;position:relative}
.appx .hbar .fill{height:100%;border-radius:6px;display:flex;align-items:center;justify-content:flex-end;
  padding-right:8px;color:#fff;font-size:11px;font-weight:650}
.appx .hbar .val{width:56px;text-align:right;font-size:12.5px;font-weight:600;flex:none}

/* donut legend */
.appx .legend{display:flex;flex-direction:column;gap:9px}
.appx .lg{display:flex;align-items:center;gap:9px;font-size:12.5px}
.appx .lg .sw{width:10px;height:10px;border-radius:3px;flex:none}
.appx .lg .lgv{margin-left:auto;font-weight:650;font-variant-numeric:tabular-nums}
.appx .lg .lgp{color:var(--ink3);font-size:11.5px;width:38px;text-align:right}

/* connector rows */
.appx .conn{display:flex;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid var(--line2)}
.appx .conn:last-child{border:none}
.appx .conn .ci{width:36px;height:36px;border-radius:9px;display:grid;place-items:center;font-size:15px;flex:none;background:var(--line2)}
.appx .conn .cn{font-weight:600}
.appx .conn .cm{font-size:11.5px;color:var(--ink3)}
.appx .stack{display:flex;align-items:center}
.appx .stack .av{margin-left:-8px;border:2px solid var(--panel)}
.appx .stack .av:first-child{margin-left:0}
.appx .more{margin-left:-8px;width:28px;height:28px;border-radius:50%;background:var(--line2);color:var(--ink2);
  display:grid;place-items:center;font-size:10.5px;font-weight:650;border:2px solid var(--panel)}

/* question cluster */
.appx .qc{border:1px solid var(--line);border-radius:10px;padding:14px;background:var(--panel)}
.appx .qc .qh{display:flex;align-items:center;gap:9px;margin-bottom:8px}
.appx .qc .qd{width:9px;height:9px;border-radius:3px;flex:none}
.appx .qc .qn{font-weight:650;font-size:13px}
.appx .qc .qcount{margin-left:auto;font-size:12px;font-weight:700}
.appx .qc .qex{font-size:11.5px;color:var(--ink2);padding:5px 0;border-top:1px dashed var(--line);display:flex;gap:6px}
.appx .qc .qex:first-of-type{border-top:none}
.appx .qc .qq{color:var(--ink3)}

/* export chips */
.appx .exp{display:flex;gap:6px}
.appx .chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:550;padding:5px 10px;
  border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink2);cursor:pointer}
.appx .chip:hover{border-color:var(--accent);color:var(--accent)}
.appx .chip svg{width:12px;height:12px}

/* cohort retention grid */
.appx .cohort{border-collapse:separate;border-spacing:3px;font-size:11px}
.appx .cohort th{padding:4px 6px;text-transform:none;letter-spacing:0;border:none;background:none;color:var(--ink3);font-weight:600}
.appx .cohort td{padding:0;border:none}
.appx .cell{width:44px;height:30px;border-radius:5px;display:grid;place-items:center;font-weight:650;color:#fff;font-size:11px}
.appx .cohort .rowlab{color:var(--ink2);font-weight:600;text-align:left;font-size:11px;white-space:nowrap;padding-right:6px}

/* funnel */
.appx .funnel{display:flex;flex-direction:column;gap:6px}
.appx .fstep{display:flex;align-items:center;gap:10px}
.appx .fstep .fbar{height:34px;border-radius:7px;display:flex;align-items:center;padding:0 12px;color:#fff;font-weight:650;font-size:12.5px;min-width:70px}
.appx .fstep .fmeta{font-size:12px;color:var(--ink2)}
.appx .fstep .fdrop{font-size:11px;color:var(--bad);font-weight:600}

/* clickable KPI + expand icon */
.appx .clik{cursor:pointer;transition:transform .12s,box-shadow .12s,border-color .12s;position:relative}
.appx .clik:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(15,23,42,.10);border-color:var(--accent)}
.appx .clik .lbl{justify-content:space-between}
.appx .expico{width:14px;height:14px;opacity:0;transition:opacity .12s}
.appx .clik:hover .expico{opacity:.6}
.appx .expico::before{content:"";display:block;width:12px;height:12px;
  background:no-repeat center/contain url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%232563eb" stroke-width="2"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>')}

/* modal */
.appx .ov{position:fixed;inset:0;background:rgba(15,23,42,.45);backdrop-filter:blur(2px);
  display:none;align-items:center;justify-content:center;z-index:50;padding:20px}
.appx .ov.on{display:flex;animation:appxfade .18s ease}
.appx .modal{background:var(--panel);border:1px solid var(--line);border-radius:16px;width:min(720px,100%);
  box-shadow:0 20px 60px rgba(0,0,0,.35);overflow:hidden}
.appx .modal .mh{display:flex;align-items:flex-start;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--line)}
.appx .modal .mh h3{font-size:16px}
.appx .modal .mh .msub{font-size:12px;color:var(--ink3);margin-top:3px}
.appx .xbtn{width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:var(--panel);
  cursor:pointer;display:grid;place-items:center;color:var(--ink2)}
.appx .xbtn:hover{border-color:var(--bad);color:var(--bad)}
.appx .modal .mb{padding:18px 20px}
.appx .mstat{display:flex;gap:24px;margin-bottom:14px;flex-wrap:wrap}
.appx .mstat .v{font-size:24px;font-weight:750;letter-spacing:-.02em}
.appx .mstat .k{font-size:11.5px;color:var(--ink3)}
.appx .seg{display:inline-flex;background:var(--line2);border-radius:8px;padding:3px;gap:2px}
.appx .seg button{border:none;background:none;font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;color:var(--ink2);font-weight:550}
.appx .seg button.on{background:var(--panel);color:var(--ink);box-shadow:var(--shadow)}

.appx .note{font-size:11.5px;color:var(--ink3);margin-top:12px;display:flex;gap:7px;align-items:flex-start}
.appx .note svg{width:14px;height:14px;flex:none;margin-top:1px;opacity:.7}
.appx .section-t{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--ink3);font-weight:650;margin:26px 0 12px}
.appx .spark{display:block}
.appx .flex{display:flex;gap:14px} .appx .col{display:flex;flex-direction:column;gap:14px}
.appx .between{display:flex;align-items:center;justify-content:space-between;gap:10px}
.appx .mt14{margin-top:14px}
</style>
