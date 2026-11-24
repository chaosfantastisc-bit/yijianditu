/* 一键地图 前端逻辑：底图预览、拉框选区和任务进度 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    cfg: null,
    source: null,
    crsMode: 'zone',
    zoom: 17,
    bbox: null,        // [minLon, minLat, maxLon, maxLat]
    rect: null,        // L.Rectangle
    drawing: false,
    taskId: null,
    timer: null,
    polling: false,
    starting: false,
    exited: false,
    baseLayer: null,
    labelLayer: null,
  };

  let map;

  /* ── 初始化 ───────────────────────────────────────────── */
  async function init() {
    const cfg = await fetch('/api/config').then((r) => r.json());
    state.cfg = cfg;
    state.source = cfg.default_source;
    const initialMode = cfg.crs_default_manual_meridian ? 'manual' : (cfg.crs_default_mode || 'zone');
    state.zoom = cfg.default_zoom;

    $('outDir').value = cfg.output_dir;
    if (cfg.tianditu_token !== cfg.tianditu_public_token) $('tkInput').value = cfg.tianditu_token;
    updateTkVisibility();
    $('crsNote').textContent = cfg.crs_note || '';
    const modeName = {zone: '加带号 Zone', cm: '不加带号 CM', manual: '手动设置'}[initialMode];
    $('foot').textContent = `v${cfg.version} · 默认 ${modeName} · JPG ≤ ${cfg.limits.jpg_max_mb}MB`;

    renderSourceTabs();
    setupCrsUI();
    initMap();
    bindEvents();
    syncZoomUI();
    if (cfg.crs_default_manual_meridian) setManualMeridian(cfg.crs_default_manual_meridian);
    setCrsMode(initialMode);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) poll();
    });
    window.addEventListener('pageshow', (e) => { if (e.persisted) poll(); });
    poll();
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
    $('cmPresetBtn').onclick = () => {
      setManualMeridian(state.cfg.crs_manual_preset || 118.5);
      refreshEstimate();
    };
    document.querySelectorAll('#crsModeTabs .tab').forEach((el) => {
      el.onclick = () => setCrsMode(el.dataset.mode);
    });
    ['cmDeg', 'cmMin', 'cmSec'].forEach((id) => {
      $(id).addEventListener('input', refreshEstimate);
    });
    const tkInput = $('tkInput');
    if (tkInput) {
      let tkTimer = null;
      tkInput.addEventListener('input', () => {
        clearTimeout(tkTimer);
        tkTimer = setTimeout(() => {
          if (state.source === 'tianditu_satellite') swapBaseLayer();
        }, 500);
      });
    }
  }

  function bandForLongitude(lon) {
    const [first, last] = state.cfg.crs_zone_range;
    if (lon < first * 3 - 1.5 || lon > last * 3 + 1.5) return null;
    return Math.min(last, Math.floor((lon + 1.5) / 3));
  }

  function setCrsMode(mode) {
    if (mode === 'manual' && (state.crsMode === 'cm' || $('cmDeg').value === '')) {
      const zone = state.bbox ? bandForLongitude(state.bbox[0]) : null;
      const defaultZone = Number(state.cfg.default_target.split(':')[1]) || 39;
      setManualMeridian(3 * (zone ?? defaultZone));
    }
    state.crsMode = mode;
    $('crsManual').style.display = mode === 'manual' ? '' : 'none';
    $('crsNote').style.display = mode === 'manual' ? 'none' : '';
    document.querySelectorAll('#crsModeTabs .tab').forEach((tab) => {
      const selected = tab.dataset.mode === mode;
      tab.classList.toggle('on', selected);
      tab.setAttribute('aria-pressed', String(selected));
    });
    refreshEstimate();
  }

  function setManualMeridian(meridian) {
    const ticks = Math.round(meridian * 360000);
    $('cmDeg').value = Math.floor(ticks / 360000);
    $('cmMin').value = Math.floor((ticks % 360000) / 6000);
    $('cmSec').value = (ticks % 6000) / 100;
  }

  function computeCrsParams() {
    if (state.crsMode !== 'manual') return { crs_mode: state.crsMode };
    const degrees = $('cmDeg').value;
    const d = Number(degrees), m = Number($('cmMin').value), s = Number($('cmSec').value);
    const meridian = d + m / 60 + s / 3600;
    if (degrees === '' || !Number.isInteger(d) || d < 0 || !Number.isInteger(m) ||
        m < 0 || m >= 60 || !(s >= 0 && s < 60 && meridian > 0 && meridian < 180)) return null;
    return { crs_mode: 'manual', manual_meridian: meridian };
  }

  function updateCrsInfo() {
    const info = $('crsInfo');
    if (!state.bbox) {
      info.className = 'crs-info';
      info.textContent = '选择范围后自动识别';
      return;
    }
    const [minLon, , maxLon] = state.bbox;
    const z1 = bandForLongitude(minLon);
    const z2 = bandForLongitude(maxLon);
    if (z1 === null || z2 === null) {
      info.className = 'crs-info warn';
      info.textContent = '⚠ 选择区域超出范围（仅支持 3°带 第25~45带 / 中央经线 75°E~135°E）';
      return;
    }
    const cross = z1 !== z2;
    const zone = z1;
    const meridian = 3 * zone;
    let txt;
    if (state.crsMode === 'zone') {
      txt = `将使用: CGCS2000_3_Degree_GK_Zone_${zone}（中央经线 ${meridian}°E，含带号）`;
    } else if (state.crsMode === 'manual') {
      const manual = computeCrsParams();
      if (!manual) {
        info.className = 'crs-info warn';
        info.textContent = '请输入有效的中央子午线';
        return;
      }
      txt = `将使用: CGCS2000_3_Degree_GK_CM_${manual.manual_meridian.toFixed(4)}E（手动中央经线）`;
    } else {
      txt = `将使用: CGCS2000_3_Degree_GK_CM_${meridian}E（中央经线 ${meridian}°E）`;
    }
    if (cross && state.crsMode !== 'manual') txt += '  ⚠ 横跨多带，已取小编号';
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
    // 天地图预览直接由浏览器请求：浏览器类型 Key 可正常使用，避免
    // 本地 Python 代理把浏览器 Key 当作服务端请求而被天地图拒绝。
    if (srcId === 'tianditu_satellite') {
      const input = $('tkInput');
      const token = (input && input.value || '').trim() || state.cfg.tianditu_public_token;
      const path = layer === 'label' ? 'cia_w' : 'img_w';
      return `https://t{s}.tianditu.gov.cn/${path}/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer === 'label' ? 'cia' : 'img'}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${encodeURIComponent(token)}`;
    }
    return currentSource().preview_url || proxyTileUrl(srcId, layer);
  }

  function proxyTileUrl(srcId, layer) {
    return `/api/tile?src=${encodeURIComponent(srcId)}&layer=${layer}&z={z}&x={x}&y={y}`;
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

    // 必须把数字子域（天地图 0~7）传给 Leaflet，否则默认 a/b/c 字母子域
    // 会被天地图拒绝（t{s} → ta/tb/tc 无法解析），瓦片全黑。
    const opts = {
      maxZoom: 19,
      maxNativeZoom: src.max_zoom,
      attribution: src.attribution,
    };
    if (src.subdomains && src.subdomains.length) opts.subdomains = src.subdomains;

    if (src.preview_url) opts.updateWhenIdle = true;
    state.baseLayer = L.tileLayer(tileUrl(src.id, 'base'), opts);
    if (src.preview_url) {
      const layer = state.baseLayer;
      const fallback = proxyTileUrl(src.id, 'base');
      layer.on('tileerror', ({ tile, coords }) => {
        if (tile.dataset.proxyFallback || layer !== state.baseLayer) return;
        tile.dataset.proxyFallback = '1';
        tile.src = L.Util.template(fallback, coords);
      });
    }
    state.baseLayer.addTo(map);

    if (src.has_label) {
      state.labelLayer = L.tileLayer(tileUrl(src.id, 'label'), opts).addTo(map);
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
      clearTimeout(estTimer);
      doEstimate(estimateRevision);
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
  let estimateRevision = 0;
  let estimateValid = false;
  function refreshEstimate() {
    updateCrsInfo();
    clearTimeout(estTimer);
    const revision = ++estimateRevision;
    estimateValid = false;
    $('startBtn').disabled = true;
    estTimer = setTimeout(() => doEstimate(revision), 120);
  }

  async function doEstimate(revision) {
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
    if (!crs) return;
    const body = {
      min_lon: state.bbox[0], min_lat: state.bbox[1],
      max_lon: state.bbox[2], max_lat: state.bbox[3],
      zoom: state.zoom, source: state.source,
      crs_mode: crs.crs_mode,
    };
    if (crs.manual_meridian !== undefined) body.manual_meridian = crs.manual_meridian;
    const info = await fetch('/api/estimate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json()).catch(() => null);
    if (revision !== estimateRevision) return;

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
      estimateValid = true;
      $('startBtn').disabled = state.starting;
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
    if (!estimateValid || !state.bbox || state.starting || state.taskId) return;
    const crs = computeCrsParams();
    if (!crs) return;
    const token = state.source === 'tianditu_satellite' ? (($('tkInput').value || '').trim()) : '';
    const body = {
      min_lon: state.bbox[0], min_lat: state.bbox[1],
      max_lon: state.bbox[2], max_lat: state.bbox[3],
      zoom: state.zoom, source: state.source,
      crs_mode: crs.crs_mode,
      // 下载时同样显式传空字符串，恢复内置公共 Key。
      tianditu_token: state.source === 'tianditu_satellite' ? token : undefined,
      output_dir: $('outDir').value.trim(),
      name: $('fileName').value.trim(),
    };
    if (crs.manual_meridian !== undefined) body.manual_meridian = crs.manual_meridian;
    state.starting = true;
    $('startBtn').disabled = true;
    $('exitBtn').disabled = true;
    try {
      const res = await post('/api/download', body);
      state.taskId = res.task_id;
      $('resultBlock').style.display = 'none';
      $('pbarFill').style.width = '0%';
      $('pmsg').textContent = '准备下载…';
      $('plog').textContent = '';
      showTask(true);
    } catch (e) {
      showTask(false);
      alert(e.message);
    } finally {
      state.starting = false;
      $('startBtn').disabled = !estimateValid;
      poll();
    }
  }

  async function post(url, body = {}) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(8000),
    });
    const data = await r.json();
    if (!r.ok && !data.already_running) throw new Error(data.error || '请求失败');
    return data;
  }

  function showTask(running) {
    $('startBtn').style.display = running ? 'none' : 'block';
    $('cancelBtn').style.display = running ? 'block' : 'none';
    $('exitBtn').disabled = running;
    if (running) $('progressBlock').style.display = 'flex';
  }

  async function poll() {
    if (state.polling || state.starting || state.exited) return;
    clearTimeout(state.timer);
    const id = state.taskId;
    state.polling = true;
    let delay = 700;
    try {
      const r = await fetch(id ? '/api/task?id=' + encodeURIComponent(id) : '/api/task', {
        signal: AbortSignal.timeout(6000),
      });
      if (!r.ok && r.status !== 404) throw new Error('读取进度失败');
      const t = await r.json();
      if (state.exited || state.starting || state.taskId !== id) return;
      if (!t || t.error) {
        state.taskId = null;
        showTask(false);
        if (id) $('pmsg').textContent = t?.error || '任务不存在';
        return;
      }
      renderTask(t);
    } catch (e) {
      if (state.taskId && !state.exited) $('pmsg').textContent = '暂时无法获取进度，正在重连…';
      delay = 1800;
    } finally {
      state.polling = false;
      if (state.taskId && !state.starting && !state.exited) state.timer = setTimeout(poll, delay);
    }
  }

  function renderTask(t) {
    const running = t.status === 'running';
    state.taskId = running ? t.id : null;
    showTask(running);
    $('progressBlock').style.display = 'flex';
    $('pbarFill').style.width = t.progress + '%';
    $('pmsg').textContent = t.message;
    $('plog').textContent = (t.logs || []).join('\n');
    $('plog').scrollTop = $('plog').scrollHeight;

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
    } else {
      $('resultBlock').style.display = 'none';
    }
  }

  async function cancel() {
    if (!state.taskId) return;
    $('cancelBtn').disabled = true;
    try {
      await post('/api/cancel', { id: state.taskId });
      poll();
    } catch (e) {
      alert(e.message);
    } finally {
      $('cancelBtn').disabled = false;
    }
  }

  async function exit() {
    $('exitBtn').disabled = true;
    try {
      await post('/api/exit');
      state.exited = true;
      clearTimeout(state.timer);
      const message = document.createElement('p');
      message.textContent = '程序已退出。双击 EXE 可重新打开。';
      message.style.padding = '24px';
      document.body.replaceChildren(message);
    } catch (e) {
      $('exitBtn').disabled = false;
      alert(e.message);
      poll();
    }
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
    $('exitBtn').onclick = exit;
    $('openDirBtn').onclick = () => fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: $('outDir').value.trim() }),
    }).then((r) => r.json()).then((r) => { if (r.error) alert(r.error); });
    $('startBtn').disabled = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state.drawing) setDrawMode(false);
    });
  }

  init().catch((e) => {
    document.body.textContent = `初始化失败：${e.message}`;
  });
})();
