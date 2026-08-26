/* 一键地图 前端逻辑：底图预览（走本地代理）+ 拉框选区 + 任务轮询 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    cfg: null,
    source: null,
    crsMode: 'zone',    // 坐标系模式：zone(加带号) / cm(不加带号)
    zoom: 17,
    bbox: null,        // [minLon, minLat, maxLon, maxLat]
    rect: null,        // L.Rectangle
    drawing: false,
    taskId: null,
    timer: null,
    baseLayer: null,
    labelLayer: null,
  };

  let map;

  /* ── 初始化 ───────────────────────────────────────────── */
  async function init() {
    const cfg = await fetch('/api/config').then((r) => r.json());
    state.cfg = cfg;
    state.source = cfg.default_source;
    state.crsMode = cfg.crs_default_mode || 'zone';
    state.zoom = cfg.default_zoom;

    $('outDir').value = cfg.output_dir;
    updateTkVisibility();
    $('crsNote').textContent = cfg.crs_note || '';
    const modeName = state.crsMode === 'zone' ? '加带号 Zone' : '不加带号 CM';
    $('foot').textContent = `v${cfg.version} · 默认 ${modeName} · JPG ≤ ${cfg.limits.jpg_max_mb}MB`;

    renderSourceTabs();
    setupCrsUI();
    initMap();
    bindEvents();
    syncZoomUI();
    startHeartbeat();
    bindUnload();
  }

  function renderSourceTabs() {
    const box = $('sourceTabs');
    box.innerHTML = '';
    state.cfg.sources.forEach((s) => {
      const el = document.createElement('div');
      el.className = 'tab' + (s.id === state.source ? ' on' : '');
      el.textContent = s.name;
      el.title = `${s.attribution} · 最高 ${s.max_zoom} 级`;
      el.onclick = () => selectSource(s.id);
      box.appendChild(el);
    });
  }

  function currentSource() {
    return state.cfg.sources.find((s) => s.id === state.source);
  }

  // ── 坐标系（自动按 3°带分带）─────────────────────────────
  function setupCrsUI() {
    document.querySelectorAll('#crsModeTabs .tab').forEach((el) => {
      el.onclick = () => {
        state.crsMode = el.dataset.mode;
        document.querySelectorAll('#crsModeTabs .tab')
          .forEach((t) => t.classList.toggle('on', t === el));
        const isCm = state.crsMode === 'cm';
        $('crsManual').style.display = isCm ? '' : 'none';
        if (isCm) syncManualDefault();
        refreshEstimate();
      };
    });
    const chk = $('cmManualChk');
    if (chk) {
      chk.onchange = () => {
        const on = chk.checked;
        ['cmDeg', 'cmMin', 'cmSec'].forEach((id) => { $(id).disabled = !on; });
        if (on) syncManualDefault();
        refreshEstimate();
      };
    }
    ['cmDeg', 'cmMin', 'cmSec'].forEach((id) => {
      $(id).addEventListener('input', refreshEstimate);
    });
    $('crsManual').style.display = state.crsMode === 'cm' ? '' : 'none';
    if (state.crsMode === 'cm') syncManualDefault();
  }

  function syncManualDefault() {
    // 未手动填写时，把自动识别的中央经线（度）预填到度框
    const b = state.bbox;
    if (!b || $('cmDeg').value) return;
    const zone = Math.min(Math.floor((b[0] + 1.5) / 3), Math.floor((b[2] + 1.5) / 3));
    $('cmDeg').value = 3 * zone;
  }

  function computeCrsParams() {
    if (state.crsMode === 'zone') return { crs_mode: 'zone' };
    const chk = $('cmManualChk');
    if (chk && chk.checked) {
      const d = parseFloat($('cmDeg').value) || 0;
      const m = parseFloat($('cmMin').value) || 0;
      const s = parseFloat($('cmSec').value) || 0;
      const dec = d + m / 60 + s / 3600;
      if (dec > 0) return { crs_mode: 'manual', manual_meridian: dec };
    }
    return { crs_mode: 'cm' };
  }

  function updateCrsInfo() {
    const info = $('crsInfo');
    if (!state.bbox) {
      info.className = 'crs-info';
      info.textContent = '选择范围后自动识别';
      return;
    }
    const [minLon, , maxLon] = state.bbox;
    if (minLon < 73.5 || maxLon > 136.5) {
      info.className = 'crs-info warn';
      info.textContent = '⚠ 选择区域超出范围（仅支持 3°带 第25~45带 / 中央经线 75°E~135°E）';
      return;
    }
    const z1 = Math.floor((minLon + 1.5) / 3);
    const z2 = Math.floor((maxLon + 1.5) / 3);
    const cross = z1 !== z2;
    const zone = Math.min(z1, z2);
    const meridian = 3 * zone;
    let txt;
    if (state.crsMode === 'zone') {
      txt = `将使用: CGCS2000_3_Degree_GK_Zone_${zone}（中央经线 ${meridian}°E，含带号）`;
    } else if ($('cmManualChk') && $('cmManualChk').checked) {
      const d = parseFloat($('cmDeg').value) || 0;
      const m = parseFloat($('cmMin').value) || 0;
      const s = parseFloat($('cmSec').value) || 0;
      txt = `将使用: CGCS2000_3_Degree_GK_CM_${(d + m / 60 + s / 3600).toFixed(4)}E（手动中央经线）`;
    } else {
      txt = `将使用: CGCS2000_3_Degree_GK_CM_${meridian}E（中央经线 ${meridian}°E）`;
    }
    if (cross) txt += '  ⚠ 横跨多带，已取小编号';
    info.className = 'crs-info';
    info.textContent = txt;
  }

  function selectSource(id) {
    state.source = id;
    renderSourceTabs();
    updateTkVisibility();
    const src = currentSource();
    if (state.zoom > src.max_zoom) state.zoom = src.max_zoom;
    $('zoom').max = src.max_zoom;
    $('zoom').min = src.min_zoom;
    syncZoomUI();
    swapBaseLayer();
    refreshEstimate();
  }

  function updateTkVisibility() {
    // 仅天地图需要 Key；ArcGIS 等无需 Key，隐藏输入框
    const show = state.source === 'tianditu_satellite';
    const sec = $('tkSection');
    if (sec) sec.style.display = show ? '' : 'none';
  }

  /* ── 地图 ─────────────────────────────────────────────── */
  function tileUrl(srcId, layer) {
    return `/api/tile?src=${srcId}&layer=${layer}&z={z}&x={x}&y={y}`;
  }

  function initMap() {
    map = L.map('map', {
      center: [32.06, 118.78],
      zoom: 12,
      zoomControl: true,
      attributionControl: true,
      boxZoom: false,       // 让位给我们自己的 Shift 框选
    });
    swapBaseLayer();
    map.on('mousedown', onMouseDown);
  }

  function swapBaseLayer() {
    const src = currentSource();
    if (state.baseLayer) map.removeLayer(state.baseLayer);
    if (state.labelLayer) { map.removeLayer(state.labelLayer); state.labelLayer = null; }

    state.baseLayer = L.tileLayer(tileUrl(src.id, 'base'), {
      maxZoom: 19,
      maxNativeZoom: src.max_zoom,
      attribution: src.attribution,
    }).addTo(map);

    if (src.has_label) {
      state.labelLayer = L.tileLayer(tileUrl(src.id, 'label'), {
        maxZoom: 19,
        maxNativeZoom: src.max_zoom,
      }).addTo(map);
    }
  }

  /* ── 拉框选区 ─────────────────────────────────────────── */
  function setDrawMode(on) {
    state.drawing = on;
    $('drawBtn').classList.toggle('on', on);
    const c = map.getContainer();
    c.style.cursor = on ? 'crosshair' : '';
    if (on) map.dragging.disable(); else map.dragging.enable();
    $('mapTip').classList.toggle('hide', false);
    $('mapTip').textContent = on
      ? '按住鼠标左键拖拽画出下载范围'
      : '点击「在地图上框选」或按住 Shift 拖拽即可选范围';
  }

  function onMouseDown(e) {
    const shift = e.originalEvent.shiftKey;
    if (!state.drawing && !shift) return;
    if (shift) map.dragging.disable();

    const start = e.latlng;
    let temp = L.rectangle([start, start], {
      color: '#3fb8f5', weight: 1.5, fillColor: '#3fb8f5', fillOpacity: 0.12, dashArray: '5,4',
    }).addTo(map);

    const move = (ev) => temp.setBounds(L.latLngBounds(start, ev.latlng));
    const up = (ev) => {
      map.off('mousemove', move);
      map.off('mouseup', up);
      const b = L.latLngBounds(start, ev.latlng);
      map.removeLayer(temp);
      temp = null;
      setDrawMode(false);
      const dx = Math.abs(b.getEast() - b.getWest());
      const dy = Math.abs(b.getNorth() - b.getSouth());
      if (dx < 1e-6 || dy < 1e-6) { refreshEstimate(); return; }
      applyBBox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], false);
      clearTimeout(estTimer); doEstimate();   // 框选完成立即预估，不等防抖
    };
    map.on('mousemove', move);
    map.on('mouseup', up);
  }

  function applyBBox(bbox, fromInputs) {
    state.bbox = bbox.map((v) => Number(v.toFixed(6)));
    $('minLon').value = state.bbox[0];
    $('minLat').value = state.bbox[1];
    $('maxLon').value = state.bbox[2];
    $('maxLat').value = state.bbox[3];
    drawRect();
    if (!fromInputs) {
      map.fitBounds(L.latLngBounds([state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]), { padding: [40, 40] });
    }
    $('mapTip').classList.add('hide');
    refreshEstimate();
  }

  function drawRect() {
    if (state.rect) map.removeLayer(state.rect);
    if (!state.bbox) return;
    state.rect = L.rectangle(
      [[state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]],
      { color: '#3fd68c', weight: 2, fillColor: '#3fd68c', fillOpacity: 0.1 },
    ).addTo(map);
  }

  function bboxFromInputs() {
    const v = ['minLon', 'minLat', 'maxLon', 'maxLat'].map((id) => parseFloat($(id).value));
    if (v.some((n) => !isFinite(n))) return null;
    if (v[2] <= v[0] || v[3] <= v[1]) return null;
    return v;
  }

  /* ── 规模预估 ─────────────────────────────────────────── */
  let estTimer = null;
  function refreshEstimate() {
    updateCrsInfo();
    clearTimeout(estTimer);
    estTimer = setTimeout(doEstimate, 80);
  }

  async function doEstimate() {
    const src = currentSource();
    $('zoomHint').textContent = `${src.name} 支持 ${src.min_zoom}~${src.max_zoom} 级`;
    if (!state.bbox) {
      $('estTiles').textContent = '—';
      $('estPixels').textContent = '—';
      $('estSize').textContent = '—';
      $('estWarn').classList.remove('show');
      $('startBtn').disabled = true;
      return;
    }
    const crs = computeCrsParams();
    const token = state.source === 'tianditu_satellite' ? (($('tkInput').value || '').trim()) : '';
    const body = {
      min_lon: state.bbox[0], min_lat: state.bbox[1],
      max_lon: state.bbox[2], max_lat: state.bbox[3],
      zoom: state.zoom, source: state.source,
      crs_mode: crs.crs_mode,
      tianditu_token: token || undefined,
    };
    if (crs.manual_meridian !== undefined) body.manual_meridian = crs.manual_meridian;
    const info = await fetch('/api/estimate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json()).catch(() => null);

    if (!info || info.error) {
      if (info && info.out_of_range) {
        $('crsInfo').className = 'crs-info warn';
        $('crsInfo').textContent = '⚠ ' + info.error;
      }
      $('estWarn').textContent = info ? info.error : '预估失败';
      $('estWarn').classList.add('show');
      $('startBtn').disabled = true;
      return;
    }
    // 用后端权威识别结果覆盖前端预览
    $('crsInfo').className = 'crs-info';
    $('crsInfo').textContent = '将使用: ' + info.target_label;
    $('estTiles').textContent = info.tile_count.toLocaleString() + ` (${info.cols}×${info.rows})`;
    $('estPixels').textContent = `${info.width.toLocaleString()} × ${info.height.toLocaleString()}`;
    $('estSize').textContent = `${info.est_download_mb} MB`;
    $('zoomHint').textContent = `地面分辨率约 ${info.ground_resolution} m/像素 · 峰值内存约 ${info.est_memory_mb} MB`;

    if (info.limit_error) {
      $('estWarn').textContent = info.limit_error;
      $('estWarn').classList.add('show');
      $('startBtn').disabled = true;
    } else {
      $('estWarn').classList.remove('show');
      $('startBtn').disabled = false;
    }
  }

  function syncZoomUI() {
    const el = $('zoom');
    el.value = state.zoom;
    $('zoomVal').textContent = state.zoom;
    const pct = ((state.zoom - el.min) / (el.max - el.min)) * 100;
    el.style.setProperty('--pct', pct + '%');
  }

  /* ── 下载任务 ─────────────────────────────────────────── */
  async function start() {
    if (!state.bbox) return;
    const crs = computeCrsParams();
    const token = state.source === 'tianditu_satellite' ? (($('tkInput').value || '').trim()) : '';
    const body = {
      min_lon: state.bbox[0], min_lat: state.bbox[1],
      max_lon: state.bbox[2], max_lat: state.bbox[3],
      zoom: state.zoom, source: state.source,
      crs_mode: crs.crs_mode,
      tianditu_token: token || undefined,
      output_dir: $('outDir').value.trim(),
      name: $('fileName').value.trim(),
    };
    if (crs.manual_meridian !== undefined) body.manual_meridian = crs.manual_meridian;
    const res = await fetch('/api/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json());

    if (res.error) { alert(res.error); return; }

    state.taskId = res.task_id;
    $('progressBlock').style.display = 'flex';
    $('resultBlock').style.display = 'none';
    $('startBtn').style.display = 'none';
    $('cancelBtn').style.display = 'block';
    $('plog').textContent = '';
    state.timer = setInterval(poll, 400);
  }

  async function poll() {
    if (!state.taskId) return;
    const t = await fetch('/api/task?id=' + state.taskId).then((r) => r.json()).catch(() => null);
    if (!t || t.error) return;

    $('pbarFill').style.width = t.progress + '%';
    $('pmsg').textContent = t.message;
    $('plog').textContent = (t.logs || []).join('\n');
    $('plog').scrollTop = $('plog').scrollHeight;

    if (t.status === 'running') return;

    clearInterval(state.timer);
    state.timer = null;
    $('startBtn').style.display = 'block';
    $('cancelBtn').style.display = 'none';

    if (t.status === 'done') {
      const r = t.result;
      $('resultBlock').style.display = 'block';
      $('resInfo').innerHTML = [
        `坐标系 ${r.target_crs}`,
        `影像 ${r.pixels[0]} × ${r.pixels[1]} 像素，实地 ${r.size_m[0]} × ${r.size_m[1]} m`,
        `左下 ${r.ll[0].toFixed(3)}, ${r.ll[1].toFixed(3)}`,
        `右上 ${r.ur[0].toFixed(3)}, ${r.ur[1].toFixed(3)}`,
        `${r.dxf_path.split('\\').pop()} + ${r.jpg_path.split('\\').pop()}`,
        r.missing_tiles ? `<span style="color:#f0b45e">${r.missing_tiles} 张瓦片下载失败（已留黑）</span>` : '',
      ].filter(Boolean).join('<br>');
      $('resOpenBtn').onclick = () => fetch('/api/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: r.dxf_path }),
      });
    } else if (t.status === 'error') {
      alert('下载失败：' + (t.result ? t.result.error : t.message));
    }
  }

  async function cancel() {
    if (!state.taskId) return;
    await fetch('/api/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: state.taskId }),
    });
  }

  /* ── 事件绑定 ─────────────────────────────────────────── */
  function bindEvents() {
    $('zoom').addEventListener('input', (e) => {
      state.zoom = parseInt(e.target.value, 10);
      syncZoomUI();
      refreshEstimate();
    });
    $('drawBtn').onclick = () => setDrawMode(!state.drawing);
    $('clearBtn').onclick = () => {
      state.bbox = null;
      ['minLon', 'minLat', 'maxLon', 'maxLat'].forEach((id) => ($(id).value = ''));
      if (state.rect) { map.removeLayer(state.rect); state.rect = null; }
      refreshEstimate();
    };
    ['minLon', 'minLat', 'maxLon', 'maxLat'].forEach((id) => {
      $(id).addEventListener('change', () => {
        const b = bboxFromInputs();
        if (b) applyBBox(b, true);
      });
    });
    $('startBtn').onclick = start;
    $('cancelBtn').onclick = cancel;
    $('openDirBtn').onclick = () => fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: $('outDir').value.trim() }),
    }).then((r) => r.json()).then((r) => { if (r.error) alert(r.error); });
    $('startBtn').disabled = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state.drawing) setDrawMode(false);
    });
  }

  /* ── 关闭网页即退出（避免后台进程残留）────────────────── */
  function startHeartbeat() {
    const ping = () => {
      fetch('/api/ping', { keepalive: true }).catch(() => {});
    };
    ping();
    setInterval(ping, 5000);   // 页面开着就持续刷新活动，关闭后停发
  }

  function notifyClose() {
    // sendBeacon 专为「页面卸载」设计，比 unload 时的 fetch 可靠
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/close', new Blob(['{}'], { type: 'application/json' }));
        return;
      }
    } catch (e) { /* ignore */ }
    fetch('/api/close', {
      method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: '{}',
    }).catch(() => {});
  }

  function bindUnload() {
    // pagehide 在关闭标签/导航时触发；切到别的标签不会触发，避免误杀
    window.addEventListener('pagehide', notifyClose);
    window.addEventListener('beforeunload', notifyClose);
  }

  init().catch((e) => {
    document.body.innerHTML = `<pre style="padding:24px;color:#f2685f">初始化失败：${e}</pre>`;
  });
})();
