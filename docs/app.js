/* Radar de Terrenos — mapa de manchas de lotes (dados pré-processados do GeoSampa) */
(() => {
'use strict';

const CORES = { 0: '#8a8f98', 1: '#e5484d', 2: '#f2b600', 3: '#22a04b' };
const ROTULOS = {
  faixa: {
    3: ['≥ 6.000 m²', 'área equivalente'], 2: ['4.000 – 6.000 m²', 'área equivalente'],
    1: ['2.500 – 4.000 m²', 'área equivalente'], 0: ['Forma compromete', 'equivalente < 2.500 m²'],
  },
  faixa_area: {
    3: ['≥ 6.000 m²', 'área somada'], 2: ['4.000 – 6.000 m²', 'área somada'],
    1: ['2.500 – 4.000 m²', 'área somada'],
  },
};
const PADRAO = { modo: 'faixa', area: 2500, forma: 0, nmax: 61, est: 2100, pnr: 50, zeu: true, zc: true,
  env: false, vila: false, ordem: 'area', faixas: { 0: true, 1: true, 2: true, 3: true }, busca: '', soTela: true };
const ZOOM_LOTES = 15;
const NMAX_SEM_LIMITE = 61, EST_SEM_LIMITE = 2100;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const fmt = n => Math.round(n).toLocaleString('pt-BR');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const semAcento = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

let est = structuredClone(PADRAO);
let manchas = [], porId = new Map(), meta = null, celulas = new Set();
let selecionada = null;
const cacheLotes = new Map();
let CEL = 0.02;

/* ------------------------------------------------------------------ mapa */
const bases = {
  mapa: { tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], maxzoom: 19,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' },
  satelite: { tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    maxzoom: 19, attribution: 'Imagem © Esri, Maxar, Earthstar Geographics' },
};
const map = new maplibregl.Map({
  container: 'mapa', hash: 'mapa', center: [-46.655, -23.57], zoom: 11.6, minZoom: 9.5, maxZoom: 19.5,
  maxBounds: [[-47.35, -24.15], [-45.95, -23.15]], attributionControl: false,
  style: {
    version: 8,
    sources: {
      mapa: { type: 'raster', tileSize: 256, ...bases.mapa },
      satelite: { type: 'raster', tileSize: 256, ...bases.satelite },
    },
    layers: [
      { id: 'fundo', type: 'background', paint: { 'background-color': '#eef0f2' } },
      { id: 'base-mapa', type: 'raster', source: 'mapa',
        paint: { 'raster-saturation': -0.75, 'raster-contrast': -0.08, 'raster-brightness-min': 0.12 } },
      { id: 'base-satelite', type: 'raster', source: 'satelite', layout: { visibility: 'none' } },
    ],
  },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
map.addControl(new maplibregl.AttributionControl({ compact: true,
  customAttribution: 'Lotes, zoneamento e patrimônio: <a href="https://geosampa.prefeitura.sp.gov.br" target="_blank" rel="noopener">GeoSampa / PMSP</a>' }), 'bottom-right');
map.dragRotate.disable();
map.touchZoomRotate.disableRotation();

const vazio = () => ({ type: 'FeatureCollection', features: [] });
// Arquivos .gz são descomprimidos no navegador (GitHub Pages os entrega como binário).
async function dados(nome) {
  const r = await fetch(`dados/${nome}`);
  if (!r.ok) throw new Error(`${nome}: ${r.status}`);
  if (!nome.endsWith('.gz')) return r.json();
  const buf = await r.arrayBuffer(), b = new Uint8Array(buf, 0, 2);
  if (b[0] !== 0x1f || b[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf));
  const fluxo = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(fluxo).json();
}

function exprCor() {
  return ['match', ['get', est.modo], 3, CORES[3], 2, CORES[2], 1, CORES[1], CORES[0]];
}

function exprFiltro() {
  const f = ['all',
    ['>=', ['get', 'area'], est.area],
    ['>=', ['get', 'forma'], est.forma],
    ['in', ['get', est.modo], ['literal', Object.keys(est.faixas).filter(k => est.faixas[k]).map(Number)]],
  ];
  if (est.nmax < NMAX_SEM_LIMITE) f.push(['<=', ['get', 'n'], est.nmax]);
  if (est.est < EST_SEM_LIMITE) f.push(['<=', ['get', 'est_m'], est.est]);
  if (est.pnr < 100) f.push(['<=', ['get', 'pnr'], est.pnr]);
  if (est.env) f.push(['==', ['get', 'env'], '']);
  if (est.vila) f.push(['!=', ['get', 'vila'], '']);
  if (!(est.zeu && est.zc)) {
    const fams = [];
    if (est.zeu) fams.push('ZEU', 'ZC+ZEU');
    if (est.zc) fams.push('ZC', 'ZC+ZEU');
    f.push(['in', ['get', 'fam'], ['literal', fams]]);
  }
  if (est.busca) f.push(['in', ['get', 'id'], ['literal', manchas.filter(passaBusca).map(m => m.properties.id)]]);
  return f;
}

function passaBusca(m) {
  if (!est.busca) return true;
  return m._texto.includes(est.busca);
}
function passa(m) {
  const p = m.properties;
  if (p.area < est.area || p.forma < est.forma) return false;
  if (!est.faixas[p[est.modo]]) return false;
  if (est.nmax < NMAX_SEM_LIMITE && p.n > est.nmax) return false;
  if (est.est < EST_SEM_LIMITE && p.est_m > est.est) return false;
  if (est.pnr < 100 && p.pnr > est.pnr) return false;
  if (est.env && p.env) return false;
  if (est.vila && !p.vila) return false;
  if (!est.zeu && !p.fam.includes('ZC')) return false;
  if (!est.zc && !p.fam.includes('ZEU')) return false;
  return passaBusca(m);
}

map.on('load', async () => {
  let zonas, tombados, envolt, estacoes, fc;
  try {
    [meta, fc, zonas, tombados, envolt, estacoes] = await Promise.all([
      dados('meta.json'), dados('manchas.geojson.gz'), dados('zonas.geojson.gz'),
      dados('tombados.geojson.gz'), dados('envoltorias.geojson.gz'), dados('estacoes.geojson')]);
  } catch (e) {
    $('#subtitulo').textContent = 'Não foi possível carregar os dados.';
    console.error(e);
    return;
  }
  CEL = meta.parametros.celula_lotes;
  manchas = fc.features;
  for (const m of manchas) {
    const p = m.properties;
    porId.set(p.id, m);
    celulas.add(p.cel);
    m._texto = semAcento(`${p.ruas} ${p.distrito} ${p.id} ${p.zonas}`);
  }

  map.addSource('zonas', { type: 'geojson', data: zonas });
  map.addSource('tombados', { type: 'geojson', data: tombados });
  map.addSource('envoltorias', { type: 'geojson', data: envolt });
  map.addSource('estacoes', { type: 'geojson', data: estacoes });
  map.addSource('manchas', { type: 'geojson', data: fc, promoteId: 'id' });
  map.addSource('pontos', { type: 'geojson', data: { type: 'FeatureCollection', features: manchas.map(m => ({
    type: 'Feature', properties: m.properties,
    geometry: { type: 'Point', coordinates: [m.properties.lon, m.properties.lat] } })) } });
  map.addSource('lotes', { type: 'geojson', data: vazio() });
  map.addSource('selecao', { type: 'geojson', data: vazio() });
  map.addSource('lote-sel', { type: 'geojson', data: vazio() });

  const corZona = ['match', ['get', 'fam'], 'ZEU', '#7c4dcc', '#1d84c9'];
  map.addLayer({ id: 'zonas-fill', type: 'fill', source: 'zonas',
    paint: { 'fill-color': corZona, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.14, 15, 0.10] } });
  map.addLayer({ id: 'zonas-line', type: 'line', source: 'zonas', minzoom: 12.5,
    paint: { 'line-color': corZona, 'line-width': 1, 'line-opacity': 0.55 } });
  map.addLayer({ id: 'envoltorias-line', type: 'line', source: 'envoltorias', layout: { visibility: 'none' },
    paint: { 'line-color': '#6b4423', 'line-width': 1.4, 'line-dasharray': [3, 2] } });
  map.addLayer({ id: 'envoltorias-fill', type: 'fill', source: 'envoltorias', layout: { visibility: 'none' },
    paint: { 'fill-color': '#6b4423', 'fill-opacity': 0.06 } });

  map.addLayer({ id: 'manchas-fill', type: 'fill', source: 'manchas', minzoom: 12.5,
    paint: { 'fill-color': exprCor(),
      'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.8, 0.58] } });
  map.addLayer({ id: 'lotes-line', type: 'line', source: 'lotes', minzoom: ZOOM_LOTES,
    paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 18, 1.4], 'line-opacity': 0.9 } });
  map.addLayer({ id: 'vias-fill', type: 'fill', source: 'lotes', minzoom: ZOOM_LOTES, filter: ['==', ['get', 'v'], 1],
    paint: { 'fill-color': '#3b4fd6', 'fill-opacity': 0.5 } });
  map.addLayer({ id: 'lotes-fill', type: 'fill', source: 'lotes', minzoom: ZOOM_LOTES,
    paint: { 'fill-color': '#000', 'fill-opacity': 0 } });
  map.addLayer({ id: 'manchas-line', type: 'line', source: 'manchas', minzoom: 12.5,
    paint: { 'line-color': ['match', ['get', est.modo], 3, '#12662f', 2, '#8a6700', 1, '#9c1f24', '#555a63'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 12.5, 0.6, 17, 2.2] } });
  map.addLayer({ id: 'tombados-fill', type: 'fill', source: 'tombados', minzoom: 13,
    paint: { 'fill-color': '#6b4423', 'fill-opacity': 0.45 } });
  map.addLayer({ id: 'tombados-line', type: 'line', source: 'tombados', minzoom: 13,
    paint: { 'line-color': '#4a2e16', 'line-width': 0.8 } });
  map.addLayer({ id: 'pontos', type: 'circle', source: 'pontos', maxzoom: 12.5,
    layout: { 'circle-sort-key': ['get', 'area_eq'] },
    paint: { 'circle-color': exprCor(), 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.6,
      'circle-opacity': 0.9,
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9.5, 1.6, 12.5, 4.5] } });
  map.addLayer({ id: 'estacoes', type: 'circle', source: 'estacoes', minzoom: 11.5,
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11.5, 2.5, 16, 6], 'circle-color': '#fff',
      'circle-stroke-color': '#1c2430', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 11.5, 1.5, 16, 3] } });
  map.addLayer({ id: 'lote-sel', type: 'line', source: 'lote-sel',
    paint: { 'line-color': '#0f5fd7', 'line-width': 3 } });
  map.addLayer({ id: 'selecao-halo', type: 'line', source: 'selecao',
    paint: { 'line-color': '#fff', 'line-width': 6 } });
  map.addLayer({ id: 'selecao', type: 'line', source: 'selecao',
    paint: { 'line-color': '#1c2430', 'line-width': 2.6 } });
  map.moveLayer('lote-sel');

  $('#subtitulo').textContent = `São Paulo · dados GeoSampa de ${meta.gerado_em.split('-').reverse().join('/')}`;
  preencherCriterios();
  lerHash();
  ligarInterface();
  aplicar();
  interacoes();
  map.on('moveend', () => { atualizarLista(); carregarLotes(); gravarHash(); });
  carregarLotes();
  if (est._abrir && porId.has(est._abrir)) selecionar(est._abrir, true);
});

/* -------------------------------------------------------- filtros / cores */
function aplicar() {
  const f = exprFiltro();
  for (const id of ['manchas-fill', 'manchas-line', 'pontos']) map.setFilter(id, f);
  map.setPaintProperty('manchas-fill', 'fill-color', exprCor());
  map.setPaintProperty('pontos', 'circle-color', exprCor());
  map.setPaintProperty('manchas-line', 'line-color',
    ['match', ['get', est.modo], 3, '#12662f', 2, '#8a6700', 1, '#9c1f24', '#555a63']);
  desenharLegenda();
  atualizarLista();
  desenharLotes();
  gravarHash();
}

function desenharLegenda() {
  const filtradas = manchas.filter(m => { const f = est.faixas; est.faixas = { 0: 1, 1: 1, 2: 1, 3: 1 }; const r = passa(m); est.faixas = f; return r; });
  const cont = { 0: 0, 1: 0, 2: 0, 3: 0 };
  for (const m of filtradas) cont[m.properties[est.modo]]++;
  const rot = ROTULOS[est.modo];
  $('#legenda').innerHTML = [3, 2, 1, 0].filter(k => rot[k]).map(k => `
    <button type="button" data-faixa="${k}" class="${est.faixas[k] ? '' : 'off'}" aria-pressed="${!!est.faixas[k]}">
      <span class="cor" style="background:${CORES[k]}"></span>
      <span><b>${rot[k][0]}</b><small>${fmt(cont[k])} manchas · ${rot[k][1]}</small></span>
    </button>`).join('');
}

function visiveis() {
  const lista = manchas.filter(passa);
  if (!est.soTela || est.busca) return lista;
  const b = map.getBounds();
  return lista.filter(m => b.contains([m.properties.lon, m.properties.lat]));
}

const titulo = p => p.ruas ? p.ruas.split(' · ').slice(0, 2).join(' × ') : `Quadra ${p.id.slice(0, 7)}`;

function atualizarLista() {
  const campo = est.modo === 'faixa' ? 'area_eq' : 'area';
  const chave = {
    area: p => -p[campo],
    forma: p => -(p.forma * 1e7 + p[campo]),
    lote: p => -p.area / p.n,
    metro: p => p.est_m,
  }[est.ordem];
  const lista = visiveis().sort((a, b) => chave(a.properties) - chave(b.properties));
  const total = manchas.filter(passa).length;
  $('#contagem').textContent = est.soTela && !est.busca
    ? `${fmt(lista.length)} na tela · ${fmt(total)} no total` : `${fmt(total)} manchas`;
  const top = lista.slice(0, 150);
  $('#lista').innerHTML = top.length ? top.map(m => {
    const p = m.properties;
    return `<li data-id="${esc(p.id)}" class="${selecionada === p.id ? 'sel' : ''}">
      <span class="faixa" style="background:${CORES[p[est.modo]]}"></span>
      <span class="corpo"><span class="tit">${esc(titulo(p))}</span>
        <span class="sub">${esc(p.distrito)} · ${esc(p.zona)} · ${p.n} lote${p.n > 1 ? 's' : ''} · forma ${p.forma}${p.vila ? ' · vila' : ''}${p.env ? ' · ⚠ envoltória' : ''}</span></span>
      <span class="num"><b>${fmt(p.area)} m²</b><span>${est.modo === 'faixa' ? `equiv. ${fmt(p.area_eq)}` : `IPTU ${fmt(p.area_iptu)}`}</span></span>
    </li>`; }).join('') + (lista.length > top.length ? `<li class="vazio">+ ${fmt(lista.length - top.length)} manchas — aproxime o mapa ou refine os filtros.</li>` : '')
    : '<li class="vazio">Nenhuma mancha com esses filtros nesta área. Afaste o mapa ou relaxe os filtros.</li>';
}

/* ------------------------------------------------------------------ lotes */
function celulasVisiveis() {
  const b = map.getBounds(), out = [];
  const x0 = Math.floor(b.getWest() / CEL) - 1, x1 = Math.floor(b.getEast() / CEL) + 1;
  const y0 = Math.floor(b.getSouth() / CEL) - 1, y1 = Math.floor(b.getNorth() / CEL) + 1;
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) if (celulas.has(`${x}_${y}`)) out.push(`${x}_${y}`);
  return out;
}
function buscarCelula(c) {
  if (!cacheLotes.has(c)) cacheLotes.set(c, dados(`lotes/${c}.geojson.gz`).then(d => d.features).catch(() => { cacheLotes.delete(c); return []; }));
  return cacheLotes.get(c);
}
let pedido = 0;
async function carregarLotes() {
  const perto = map.getZoom() >= ZOOM_LOTES - 0.5;
  $('#aviso').hidden = map.getZoom() >= 12.5;
  $('#aviso').textContent = 'Aproxime para ver o contorno das manchas e dos lotes';
  if (!perto) return;
  const meu = ++pedido;
  const cels = celulasVisiveis();
  if (cels.length > 30) return;
  await Promise.all(cels.map(buscarCelula));
  if (meu === pedido) desenharLotes();
}
async function desenharLotes() {
  if (!map.getSource('lotes') || map.getZoom() < ZOOM_LOTES - 0.5) return;
  const cels = celulasVisiveis().filter(c => cacheLotes.has(c));
  const partes = await Promise.all(cels.map(c => cacheLotes.get(c)));
  const feats = partes.flat().filter(l => { const m = porId.get(l.properties.m); return m && passa(m); });
  map.getSource('lotes').setData({ type: 'FeatureCollection', features: feats });
}

/* -------------------------------------------------------------- interação */
function interacoes() {
  const dica = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'dica', offset: 12 });
  let hoverId = null;
  const soltar = () => {
    if (hoverId !== null) map.setFeatureState({ source: 'manchas', id: hoverId }, { hover: false });
    hoverId = null;
  };
  map.on('mousemove', 'manchas-fill', e => {
    const f = e.features[0], p = f.properties;
    if (hoverId !== p.id) { soltar(); hoverId = p.id; map.setFeatureState({ source: 'manchas', id: hoverId }, { hover: true }); }
    map.getCanvas().style.cursor = 'pointer';
    let html = `<b>${fmt(p.area)} m²</b> · ${p.n} lote${p.n > 1 ? 's' : ''} · forma ${p.forma}<br>${esc(p.zonas)}${p.vila ? ' · inclui vila' : ''}`;
    if (map.getZoom() >= ZOOM_LOTES) {
      const l = map.queryRenderedFeatures(e.point, { layers: ['lotes-fill'] })[0];
      if (l && l.properties.v) html = `<b>Passagem de vila</b> · ${fmt(l.properties.ag)} m²<br><span style="color:#5b6675">mancha: ${fmt(p.area)} m² · ${p.n} lotes · forma ${p.forma}</span>`;
      else if (l) html = `<b>${esc(l.properties.end || 'Lote ' + l.properties.sql)}</b><br>terreno ${fmt(l.properties.at || l.properties.ag)} m² · construído ${fmt(l.properties.ac)} m²<br><span style="color:#5b6675">mancha: ${fmt(p.area)} m² · ${p.n} lotes · forma ${p.forma}</span>`;
    }
    dica.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });
  map.on('mouseleave', 'manchas-fill', () => { soltar(); dica.remove(); map.getCanvas().style.cursor = ''; });
  for (const [camada, rot] of [['pontos', p => `<b>${fmt(p.area)} m²</b> · ${p.n} lotes · forma ${p.forma}<br>${esc(p.distrito)} · ${esc(p.zona)}`],
    ['estacoes', p => `<b>${esc(p.nome)}</b>`], ['tombados-fill', p => `<b>Bem tombado</b><br>${esc(p.nome)}${p.sit && p.sit !== 'TOMBADO' ? ' (' + esc(p.sit) + ')' : ''}`]]) {
    map.on('mousemove', camada, e => {
      if (camada !== 'pontos' && map.queryRenderedFeatures(e.point, { layers: ['manchas-fill'] }).length) return;
      map.getCanvas().style.cursor = camada === 'pontos' ? 'pointer' : '';
      dica.setLngLat(e.lngLat).setHTML(rot(e.features[0].properties)).addTo(map);
    });
    map.on('mouseleave', camada, () => { dica.remove(); map.getCanvas().style.cursor = ''; });
  }
  map.on('click', e => {
    const f = map.queryRenderedFeatures(e.point, { layers: ['manchas-fill', 'pontos'] })[0];
    if (!f) return;
    const l = map.getZoom() >= ZOOM_LOTES ? map.queryRenderedFeatures(e.point, { layers: ['lotes-fill'] })[0] : null;
    selecionar(f.properties.id, f.layer.id === 'pontos', l ? l.properties.sql : null);
  });
}

async function selecionar(id, voar = true, sqlDestaque = null) {
  const m = porId.get(id);
  if (!m) return;
  selecionada = id;
  map.getSource('selecao').setData(m);
  map.getSource('lote-sel').setData(vazio());
  if (voar) {
    const xs = m.geometry.coordinates[0].map(c => c[0]), ys = m.geometry.coordinates[0].map(c => c[1]);
    const estreito = matchMedia('(max-width: 760px)').matches;
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { padding: estreito ? 50 : 110, maxZoom: 17.6, duration: 700 });
  }
  $$('#lista li').forEach(li => li.classList.toggle('sel', li.dataset.id === id));
  desenharDetalhe(m, null);
  gravarHash();
  const lotes = (await buscarCelula(m.properties.cel)).filter(l => l.properties.m === id);
  if (selecionada === id) {
    desenharDetalhe(m, lotes);
    if (sqlDestaque) destacarLote(lotes, sqlDestaque);
  }
}

function destacarLote(lotes, sql) {
  const l = lotes.find(x => x.properties.sql === sql);
  map.getSource('lote-sel').setData(l || vazio());
  $$('table.lotes tr[data-sql]').forEach(tr => tr.style.background = tr.dataset.sql === sql ? '#e8f0fd' : '');
  const tr = $(`table.lotes tr[data-sql="${CSS.escape(sql)}"]`);
  if (tr) tr.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function desenharDetalhe(m, lotes) {
  const p = m.properties, el = $('#detalhe');
  const fx = p.faixa, par = meta.parametros;
  const fator = Math.min(1, p.forma / (par.forma_sem_penalidade * 100));
  const barra = (nome, v, txt) => `<div class="metrica"><span>${nome}</span><span class="trilho"><i style="width:${Math.max(2, Math.min(100, v))}%"></i></span><em>${txt ?? v}</em></div>`;
  const notaVert = Math.round(100 * Math.min(1, Math.max(0, 1 - (p.nv - par.vert_ideal) / (par.vert_ruim - par.vert_ideal))));
  const rotFaixa = { 3: 'Verde · equiv. ≥ 6.000 m²', 2: 'Amarelo · equiv. ≥ 4.000 m²', 1: 'Vermelho · equiv. ≥ 2.500 m²', 0: 'Forma compromete' }[fx];
  el.innerHTML = `
    <div class="topo"><button class="link" id="voltar" type="button">← Voltar à lista</button>
      <button class="link" id="copiar" type="button">Copiar link</button></div>
    <h3>${esc(titulo(p))}</h3>
    <p class="onde">${esc(p.distrito)} · setor-quadra ${esc(p.id.slice(0, 7))}${p.ruas.split(' · ').length > 2 ? ' · também ' + esc(p.ruas.split(' · ').slice(2).join(', ')) : ''}</p>
    <div class="tags"><span class="tag t${fx}">${rotFaixa}</span>${p.zonas.split(' · ').map(z => `<span class="tag zona">${esc(z)}</span>`).join('')}${p.vila ? `<span class="tag vila">Vila ${p.vila}</span>` : ''}</div>
    <div class="cartoes">
      <div class="cartao"><span>Área somada</span><b>${fmt(p.area)}</b> <small>m²</small></div>
      <div class="cartao"><span>Área equivalente</span><b>${fmt(p.area_eq)}</b> <small>m²</small></div>
      <div class="cartao"><span>Lotes</span><b>${p.n}</b></div>
    </div>
    ${p.vila === 'completa' ? `<div class="alerta info">Inclui uma <b>vila completa</b>: a passagem (em azul no mapa) e todas as casas que dão para ela estão na mancha. A área da passagem está somada.</div>` : ''}
    ${p.vila === 'parcial' ? `<div class="alerta">Inclui <b>passagem de vila</b> (em azul no mapa), mas alguma casa que encosta nela ficou de fora da mancha — tombada, prédio ou fora de ZEU/ZC. Confira se a vila pode ser comprada inteira.</div>` : ''}
    ${p.env ? `<div class="alerta">⚠ Parte da mancha está em <b>área envoltória de bem tombado</b> (${esc(p.env)}). Projetos aqui dependem de aprovação do órgão de patrimônio.</div>` : ''}
    <h2>Forma do terreno unificado · nota ${p.forma}/100</h2>
    <div class="metricas">
      ${barra('Retangularidade', p.ret)}
      ${barra('Miolo aproveitável', p.miolo)}
      ${barra('Perímetro × área', p.comp)}
      ${barra('Vértices', notaVert, p.nv + ' v')}
    </div>
    <p class="nota" style="margin:8px 0 0">${fator >= 1 ? 'Forma boa: a área conta integralmente.' : `Fator de forma ${fator.toFixed(2).replace('.', ',')}: ${fmt(p.area)} m² × ${fator.toFixed(2).replace('.', ',')} = ${fmt(p.area_eq)} m² equivalentes.`}</p>
    <h2>Dados</h2>
    <dl class="fatos">
      <dt>Área pelo IPTU</dt><dd>${fmt(p.area_iptu)} m²</dd>
      <dt>Construído hoje</dt><dd>${fmt(p.ac)} m² · CA médio ${String(p.ca).replace('.', ',')}</dd>
      <dt>Usos</dt><dd>${esc(p.usos)}</dd>
      <dt>Transporte</dt><dd>${esc(p.est)} a ${fmt(p.est_m)} m</dd>
    </dl>
    <div class="acoes">
      <a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lon}">Google Maps</a>
      <a target="_blank" rel="noopener" href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${p.lat},${p.lon}">Street View</a>
      <button type="button" id="csv-lotes" ${lotes ? '' : 'disabled'}>CSV dos lotes</button>
    </div>
    <h2>Lotes da mancha</h2>
    ${lotes ? `<table class="lotes"><thead><tr><th>Endereço / SQL</th><th class="n">Terreno</th><th class="n">Constr.</th><th>Uso</th></tr></thead><tbody>
      ${lotes.map(l => { const q = l.properties; return `<tr data-sql="${esc(q.sql)}"><td>${esc(q.end || '—')}<div class="sqlc">${esc(q.sql)} · ${esc(q.zona)}</div></td>
        <td class="n">${fmt(q.at || q.ag)}</td><td class="n">${fmt(q.ac)}</td><td>${esc(q.uso.replace('Não residencial', 'Não resid.'))}</td></tr>`; }).join('')}
      </tbody></table>` : '<p class="nota">Carregando lotes…</p>'}`;
  el.hidden = false;
  el.scrollTop = 0;
  document.getElementById('app').classList.add('aberto');
  $('#voltar').onclick = fecharDetalhe;
  $('#copiar').onclick = async ev => {
    try { await navigator.clipboard.writeText(location.href); ev.target.textContent = 'Link copiado ✓'; }
    catch { prompt('Copie o link:', location.href); }
  };
  if (lotes) {
    $('#csv-lotes').onclick = () => baixarCSV(`lotes_${p.id}.csv`,
      ['SQL', 'Endereço', 'Zona', 'Uso', 'Área terreno IPTU (m²)', 'Área do desenho (m²)', 'Área construída (m²)', 'Situação'],
      lotes.map(l => { const q = l.properties; return [q.sql, q.end, q.zona, q.uso, q.at, q.ag, q.ac, q.sit]; }));
    $$('table.lotes tr[data-sql]').forEach(tr => tr.onclick = () => destacarLote(lotes, tr.dataset.sql));
  }
}

function fecharDetalhe() {
  selecionada = null;
  $('#detalhe').hidden = true;
  map.getSource('selecao').setData(vazio());
  map.getSource('lote-sel').setData(vazio());
  $$('#lista li.sel').forEach(li => li.classList.remove('sel'));
  gravarHash();
}

function baixarCSV(nome, cab, linhas) {
  const cel = v => { const s = String(v ?? ''); return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const txt = '﻿' + [cab, ...linhas].map(l => l.map(cel).join(';')).join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([txt], { type: 'text/csv;charset=utf-8' }));
  a.download = nome;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* -------------------------------------------------------------- interface */
function sincronizarControles() {
  $('#f-area').value = est.area; $('#f-forma').value = est.forma;
  $('#f-nmax').value = est.nmax; $('#f-est').value = est.est; $('#f-pnr').value = est.pnr;
  $('#o-pnr').textContent = est.pnr >= 100 ? 'sem limite' : `${est.pnr}%`;
  $('#f-zeu').checked = est.zeu; $('#f-zc').checked = est.zc; $('#f-env').checked = est.env; $('#f-vila').checked = est.vila;
  $('#so-tela').checked = est.soTela; $('#ordem').value = est.ordem;
  $('#o-area').textContent = fmt(est.area); $('#o-forma').textContent = est.forma;
  $('#o-nmax').textContent = est.nmax >= NMAX_SEM_LIMITE ? 'sem limite' : est.nmax;
  $('#o-est').textContent = est.est >= EST_SEM_LIMITE ? 'sem limite' : `${fmt(est.est)} m`;
  $$('#modo-cor button').forEach(b => b.classList.toggle('ativa', b.dataset.modo === est.modo));
}

function ligarInterface() {
  sincronizarControles();
  const liga = (sel, chave, conv = Number) => $(sel).addEventListener('input', e => {
    est[chave] = e.target.type === 'checkbox' ? e.target.checked : conv(e.target.value);
    sincronizarControles(); aplicar();
  });
  liga('#f-area', 'area'); liga('#f-forma', 'forma'); liga('#f-nmax', 'nmax'); liga('#f-est', 'est'); liga('#f-pnr', 'pnr');
  liga('#f-zeu', 'zeu'); liga('#f-zc', 'zc'); liga('#f-env', 'env'); liga('#f-vila', 'vila'); liga('#so-tela', 'soTela');
  liga('#ordem', 'ordem', String);
  let t;
  $('#busca').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { est.busca = semAcento(e.target.value.trim()); aplicar(); }, 180);
  });
  $('#modo-cor').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    est.modo = b.dataset.modo;
    if (est.modo === 'faixa_area') est.faixas[0] = true;
    sincronizarControles(); aplicar();
  });
  $('#legenda').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    est.faixas[b.dataset.faixa] = !est.faixas[b.dataset.faixa];
    aplicar();
  });
  $('#base').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    $$('#base button').forEach(x => x.classList.toggle('ativa', x === b));
    const sat = b.dataset.base === 'satelite';
    map.setLayoutProperty('base-mapa', 'visibility', sat ? 'none' : 'visible');
    map.setLayoutProperty('base-satelite', 'visibility', sat ? 'visible' : 'none');
    map.setPaintProperty('manchas-fill', 'fill-opacity',
      ['case', ['boolean', ['feature-state', 'hover'], false], sat ? 0.7 : 0.8, sat ? 0.42 : 0.58]);
  });
  $$('[data-camada]').forEach(c => c.addEventListener('change', () => {
    const v = c.checked ? 'visible' : 'none';
    const ids = { zonas: ['zonas-fill', 'zonas-line'], tombados: ['tombados-fill', 'tombados-line'],
      envoltorias: ['envoltorias-fill', 'envoltorias-line'], estacoes: ['estacoes'] }[c.dataset.camada];
    ids.forEach(id => map.setLayoutProperty(id, 'visibility', v));
  }));
  $('#btn-limpar').onclick = () => { est = structuredClone(PADRAO); $('#busca').value = ''; sincronizarControles(); aplicar(); };
  $('#abas').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    $$('#abas button').forEach(x => x.classList.toggle('ativa', x === b));
    $('#aba-lista').hidden = b.dataset.aba !== 'lista';
    $('#aba-filtros').hidden = b.dataset.aba !== 'filtros';
  });
  $('#lista').addEventListener('click', e => {
    const li = e.target.closest('li[data-id]'); if (li) selecionar(li.dataset.id);
  });
  $('#btn-csv').onclick = () => {
    const l = manchas.filter(passa);
    baixarCSV('manchas.csv',
      ['ID (setor-quadra-lote)', 'Distrito', 'Ruas', 'Zonas', 'Área somada (m²)', 'Área IPTU (m²)', 'Área equivalente (m²)',
        'Faixa', 'Lotes', 'Nota de forma', 'Retangularidade', 'Miolo aproveitável', 'Perímetro×área', 'Vértices',
        'Área construída (m²)', 'CA médio', 'Usos', 'Vila', 'Envoltória', 'Estação mais próxima', 'Distância (m)', 'Latitude', 'Longitude', 'Link'],
      l.map(m => { const p = m.properties; return [p.id, p.distrito, p.ruas, p.zonas, p.area, p.area_iptu, p.area_eq,
        ['forma compromete', 'vermelho', 'amarelo', 'verde'][p.faixa], p.n, p.forma, p.ret, p.miolo, p.comp, p.nv, p.ac,
        String(p.ca).replace('.', ','), p.usos, p.vila, p.env, p.est, p.est_m, String(p.lat).replace('.', ','), String(p.lon).replace('.', ','),
        `${location.origin}${location.pathname}#m=${p.id}`]; }));
  };
  $('#btn-criterios').onclick = () => $('#criterios').showModal();
  $('#criterios').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  $('#alternar').onclick = () => document.getElementById('app').classList.toggle('aberto');
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && selecionada && !$('#criterios').open) fecharDetalhe(); });
}

function preencherCriterios() {
  const p = meta.parametros, pct = v => Math.round(v * 100);
  const v = { ca: String(p.ca_max_casa).replace('.', ','), divisa: p.divisa_min, amin: fmt(p.area_min),
    'p-ret': pct(p.pesos.ret), 'p-miolo': pct(p.pesos.miolo), 'p-comp': pct(p.pesos.comp), 'p-vert': pct(p.pesos.vert),
    largura: p.raio_miolo * 2, vi: p.vert_ideal, vr: p.vert_ruim, fsp: pct(p.forma_sem_penalidade) };
  $$('[data-p]').forEach(s => s.textContent = v[s.dataset.p]);
  $('#meta-rodape').textContent = `${fmt(meta.lotes_analisados)} lotes analisados · ${fmt(meta.lotes_elegiveis)} elegíveis · ${fmt(meta.manchas)} manchas. Fonte: ${meta.fonte}.`;
}

/* ------------------------------------------------------------------- hash */
function lerHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get('m')) est._abrir = h.get('m');
}
function gravarHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (selecionada) h.set('m', selecionada); else h.delete('m');
  const s = '#' + [...h].map(([k, v]) => `${k}=${v}`).join('&');
  if (s !== location.hash) history.replaceState(null, '', s);
}
})();
