(function () {
  "use strict";

  function _fmt(t, v) {
    return (t || "").replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ""));
  }

  const _locale = window.TM_LOCALE || "en";
  const _T = window.TRANSLATIONS || {};
  const W = (_T[_locale] || _T["en"] || {}).web || {};

  const S = {
    locale:      W.locale      || undefined,
    connecting:  W.connecting  || "",
    connected:   W.connected   || "",
    fetchFailed: (msg) => _fmt(W.fetchFailed, {msg}),
    dataStale:   (min) => _fmt(W.dataStale,   {min}),
    paused:      W.paused      || "",
    nextIn:      (s)   => _fmt(W.nextIn,      {s}),
    chipOk:      W.chipOk      || "",
    chipWarn:    W.chipWarn    || "",
    chipCrit:    W.chipCrit    || "",
    chipErr:     W.chipErr     || "",
    chipSources: W.chipSources || "",
    chipSensors: W.chipSensors || "",
    hosts:       (n)   => _fmt(W.hosts,       {n}),
    status:      W.status      || {},
  };

  // ── state ──────────────────────────────────────────────
  let data = null;
  let timer = null;
  let nextFetch = 0;
  let expanded = new Set();
  let userCollapsed = new Set();
  let collapsedGroups = new Set();
  let expandedGroups  = new Set();
  let sortCol = "name";
  let sortAsc = true;

  // ── DOM refs ───────────────────────────────────────────
  const $tbody       = document.getElementById("tbody");
  const $placeholder = document.getElementById("placeholder");
  const $summary     = document.getElementById("summary");
  const $statusDot   = document.getElementById("statusDot");
  const $statusText  = document.getElementById("statusText");
  const $tsText      = document.getElementById("tsText");
  const $countdown   = document.getElementById("countdown");
  const $interval    = document.getElementById("refreshInterval");
  const $jsonPath    = document.getElementById("jsonPath");
  const $refreshBtn  = document.getElementById("refreshBtn");

  // ── helpers ────────────────────────────────────────────
  const statusOrd = { OK: 0, WARN: 1, CRIT: 2, ERROR: 3 };
  const statusCls = { OK: "ok", WARN: "warn", CRIT: "crit", ERROR: "err" };

  function tempClass(status) {
    if (status === "CRIT") return "temp-crit";
    if (status === "WARN") return "temp-warn";
    return "temp-ok";
  }

  function fmtTemp(v) {
    return v == null ? "---" : v.toFixed(1) + "\u00b0C";
  }

  function badge(key) {
    return (S.status && S.status[key]) || key;
  }

  // ── sorting ────────────────────────────────────────────
  function compareSources(a, b) {
    let va, vb;
    switch (sortCol) {
      case "temp":
        va = a.max_temp ?? -Infinity;
        vb = b.max_temp ?? -Infinity;
        break;
      case "warn":
        va = a.sensors.length ? Math.min(...a.sensors.filter(s=>!s.error).map(s => s.warn)) : Infinity;
        vb = b.sensors.length ? Math.min(...b.sensors.filter(s=>!s.error).map(s => s.warn)) : Infinity;
        break;
      case "crit":
        va = a.sensors.length ? Math.min(...a.sensors.filter(s=>!s.error).map(s => s.crit)) : Infinity;
        vb = b.sensors.length ? Math.min(...b.sensors.filter(s=>!s.error).map(s => s.crit)) : Infinity;
        break;
      case "status":
        va = statusOrd[a.status] ?? 0;
        vb = statusOrd[b.status] ?? 0;
        break;
      default: {
        const aIsGroup = !!a.group;
        const bIsGroup = !!b.group;
        if (aIsGroup !== bIsGroup) return aIsGroup ? 1 : -1;
        if (aIsGroup) {
          const cmp = sortAsc ? a.group.localeCompare(b.group) : b.group.localeCompare(a.group);
          if (cmp !== 0) return cmp;
        }
        return sortAsc ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
      }
    }
    return sortAsc ? va - vb : vb - va;
  }

  // ── render ─────────────────────────────────────────────
  function render() {
    if (!data) return;

    if (data.timestamp) {
      const ageMs = Date.now() - new Date(data.timestamp).getTime();
      if (ageMs > 5 * 60 * 1000) {
        $statusDot.className = "dot stale";
        $statusText.textContent = S.dataStale(Math.round(ageMs / 60000));
      }
    }

    const s = data.summary;
    $summary.innerHTML = [
      `<span class="chip ok"><span class="n">${s.ok}</span> ${S.chipOk}</span>`,
      s.warn  ? `<span class="chip warn"><span class="n">${s.warn}</span> ${S.chipWarn}</span>` : "",
      s.crit  ? `<span class="chip crit"><span class="n">${s.crit}</span> ${S.chipCrit}</span>` : "",
      s.error ? `<span class="chip err"><span class="n">${s.error}</span> ${S.chipErr}</span>` : "",
      `<span class="chip"><span class="n">${s.total_sources}</span> ${S.chipSources}</span>`,
      `<span class="chip"><span class="n">${s.total_sensors}</span> ${S.chipSensors}</span>`,
    ].filter(Boolean).join("");

    if (data.timestamp) {
      const d = new Date(data.timestamp);
      $tsText.textContent = d.toLocaleString(S.locale);
    }

    const sources = [...data.sources].sort(compareSources);

    const groupStats = {};
    const statusKeys = ['OK', 'WARN', 'CRIT', 'ERROR'];
    for (const src of sources) {
      const grp = src.group;
      if (!grp) continue;
      if (!groupStats[grp]) groupStats[grp] = {
        count: 0, worstOrd: 0, maxPrimaryTemp: null,
        primaryWarn: null, primaryCrit: null,
        nonOkCount: 0, alertHint: null
      };
      const gs = groupStats[grp];
      gs.count++;
      gs.worstOrd = Math.max(gs.worstOrd, statusOrd[src.status] ?? 0);
      if (src.primary_temp != null && (gs.maxPrimaryTemp == null || src.primary_temp > gs.maxPrimaryTemp)) {
        gs.maxPrimaryTemp = src.primary_temp;
        gs.primaryWarn = src.primary_warn;
        gs.primaryCrit = src.primary_crit;
      }
      if ((statusOrd[src.status] ?? 0) > 0) gs.nonOkCount++;
    }
    for (const gs of Object.values(groupStats)) gs.status = statusKeys[gs.worstOrd];

    for (const [grp, gs] of Object.entries(groupStats)) {
      if (gs.worstOrd === 0) continue;
      if (gs.nonOkCount > 1) {
        gs.alertHint = S.hosts(gs.nonOkCount);
      } else if (gs.nonOkCount === 1) {
        const bad = sources.find(s => s.group === grp && (statusOrd[s.status] ?? 0) > 0);
        if (bad) {
          const short = bad.short_name || bad.name;
          gs.alertHint = bad.alert_hint ? `${short}: ${bad.alert_hint}` : short;
        }
      }
    }

    for (const [grp, gs] of Object.entries(groupStats)) {
      if (gs.worstOrd > 0) {
        collapsedGroups.delete(grp);
      } else if (!expandedGroups.has(grp)) {
        collapsedGroups.add(grp);
      }
    }

    const rows = [];
    let lastGroupKey = undefined;

    for (const src of sources) {
      const grp = src.group || null;
      const sectionKey = grp || src.name;

      if (sectionKey !== lastGroupKey) {
        if (lastGroupKey !== undefined) {
          rows.push(`<tr class="group-gap"><td colspan="5"></td></tr>`);
        }
        if (grp) {
          const gs       = groupStats[grp];
          const isCol    = collapsedGroups.has(grp);
          const colCls   = isCol ? "collapsed" : "";
          const gCls     = statusCls[gs.status];
          const gTempStr = gs.maxPrimaryTemp != null ? gs.maxPrimaryTemp.toFixed(1) + "\u00b0C" : "---";
          const gTCls    = tempClass(gs.status);
          const hintHtml = gs.alertHint
            ? ` <span class="alert-hint">(${esc(gs.alertHint)})</span>`
            : "";
          const gWarnStr = gs.primaryWarn != null ? gs.primaryWarn.toFixed(0) + "\u00b0" : "";
          const gCritStr = gs.primaryCrit != null ? gs.primaryCrit.toFixed(0) + "\u00b0" : "";
          rows.push(
            `<tr class="group-header ${colCls} row-${gCls}" data-group="${esc(grp)}">` +
            `<td><span class="group-toggle">\u25bc</span> ${esc(grp)}<span class="group-count"> · ${esc(S.hosts(gs.count))}</span></td>` +
            `<td class="${gTCls}" style="text-align:right">${gTempStr}</td>` +
            `<td style="text-align:right">${gWarnStr}</td>` +
            `<td style="text-align:right">${gCritStr}</td>` +
            `<td><span class="badge ${gCls}">${badge(gs.status)}</span>${hintHtml}</td>` +
            `</tr>`
          );
        }
        lastGroupKey = sectionKey;
      }

      if (grp && collapsedGroups.has(grp)) continue;

      const isNonOk    = (statusOrd[src.status] ?? 0) > 0;
      if (isNonOk && !userCollapsed.has(src.name)) expanded.add(src.name);
      if (!isNonOk) { expanded.delete(src.name); userCollapsed.delete(src.name); }
      const isExp      = expanded.has(src.name);
      const cls        = statusCls[src.status];
      const hasDetail  = src.sensors.length > 0;
      const expandable = hasDetail ? "expandable" : "";
      const expCls     = isExp ? "expanded" : "";
      const inGroup    = grp ? "in-group" : "";
      const displayName = src.short_name || src.name;
      const expandHint = hasDetail ? `<span class="expand-hint">\u25be</span>` : ``;
      const tempStr    = src.primary_temp != null ? fmtTemp(src.primary_temp) : fmtTemp(src.max_temp);
      const tCls       = tempClass(src.status);

      let hintHtml = "";
      if (src.alert_hint)
        hintHtml = ` <span class="alert-hint">(${esc(src.alert_hint)})</span>`;

      let errSnippet = "";
      if (src.primary_temp == null && src.max_temp == null && src.sensors.length) {
        const errSensor = src.sensors.find(s => s.error);
        if (errSensor) errSnippet = ` <span class="error-msg">${esc(errSensor.error)}</span>`;
      }

      const pWarn = src.primary_warn != null ? src.primary_warn.toFixed(0) + "\u00b0" : "";
      const pCrit = src.primary_crit != null ? src.primary_crit.toFixed(0) + "\u00b0" : "";

      rows.push(
        `<tr class="source ${expandable} ${expCls} ${inGroup} row-${cls}" data-src="${esc(src.name)}">` +
        `<td>${esc(displayName)}${expandHint}</td>` +
        `<td class="${tCls}">${tempStr}</td>` +
        `<td>${pWarn}</td>` +
        `<td>${pCrit}</td>` +
        `<td><span class="badge ${cls}">${badge(src.status)}</span>${hintHtml}${errSnippet}</td>` +
        `</tr>`
      );

      if (isExp && hasDetail) {
        for (const sn of src.sensors) {
          const sCls   = statusCls[sn.status];
          const sv     = sn.value != null ? sn.value.toFixed(1) + "\u00b0C" : "---";
          const stCls  = tempClass(sn.status);
          const errMsg = sn.error ? ` <span class="error-msg">${esc(sn.error)}</span>` : "";
          rows.push(
            `<tr class="sensor">` +
            `<td>${esc(sn.name)}</td>` +
            `<td class="${stCls}">${sv}</td>` +
            `<td>${sn.warn.toFixed(0)}\u00b0</td>` +
            `<td>${sn.crit.toFixed(0)}\u00b0</td>` +
            `<td><span class="badge ${sCls}">${badge(sn.status)}</span>${errMsg}</td>` +
            `</tr>`
          );
        }
      }
    }

    $tbody.innerHTML = rows.join("");
    $placeholder.style.display = rows.length ? "none" : "";
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ── fetch ──────────────────────────────────────────────
  async function fetchData() {
    const path = $jsonPath.value.trim() || "readings.json";
    try {
      const resp = await fetch(path + "?_=" + Date.now());
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      $statusDot.className = "dot";
      $statusText.textContent = S.connected;
      render();
    } catch (e) {
      $statusDot.className = "dot error";
      $statusText.textContent = S.fetchFailed(e.message);
    }
  }

  // ── auto-refresh ───────────────────────────────────────
  function scheduleRefresh() {
    if (timer) clearInterval(timer);
    const sec = parseInt($interval.value, 10);
    if (sec <= 0) {
      $countdown.textContent = S.paused;
      return;
    }
    nextFetch = Date.now() + sec * 1000;
    timer = setInterval(() => {
      if (Date.now() >= nextFetch) {
        fetchData();
        nextFetch = Date.now() + sec * 1000;
      }
      const left = Math.max(0, Math.ceil((nextFetch - Date.now()) / 1000));
      $countdown.textContent = S.nextIn(left);
    }, 1000);
  }

  // ── events ─────────────────────────────────────────────
  $tbody.addEventListener("click", (e) => {
    const groupRow = e.target.closest("tr.group-header");
    if (groupRow) {
      const grp = groupRow.dataset.group;
      if (collapsedGroups.has(grp)) {
        collapsedGroups.delete(grp);
        expandedGroups.add(grp);
      } else {
        collapsedGroups.add(grp);
        expandedGroups.delete(grp);
      }
      render();
      return;
    }
    const row = e.target.closest("tr.source.expandable");
    if (!row) return;
    const name = row.dataset.src;
    if (expanded.has(name)) {
      expanded.delete(name);
      userCollapsed.add(name);
    } else {
      expanded.add(name);
      userCollapsed.delete(name);
    }
    render();
  });

  document.querySelectorAll("th[id^='sort-']").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.id.replace("sort-", "");
      if (sortCol === col) sortAsc = !sortAsc;
      else { sortCol = col; sortAsc = true; }
      render();
    });
  });

  $interval.addEventListener("change", scheduleRefresh);
  $refreshBtn.addEventListener("click", () => { fetchData(); scheduleRefresh(); });

  const params = new URLSearchParams(location.search);
  if (params.has("refresh")) {
    const v = parseInt(params.get("refresh"), 10);
    for (const opt of $interval.options) {
      if (parseInt(opt.value, 10) === v) { opt.selected = true; break; }
    }
  }
  if (params.has("json")) {
    $jsonPath.value = params.get("json");
  }

  // ── init ───────────────────────────────────────────────
  $statusText.textContent = S.connecting;
  fetchData();
  scheduleRefresh();
})();
