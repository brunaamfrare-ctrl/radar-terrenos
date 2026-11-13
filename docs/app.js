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
// Alertas: não excluem a mancha, mas aparecem ao passar o mouse e na ficha.
const ALERTAS = {
  mv: 'Melhoramento viário',
  de: 'Desapropriação (DUP/DIS)',
  g1: 'Unidade geotécnica I (planície aluvial)',
  cs: 'Área contaminada ou reabilitada',
  cp: 'Potencial ou suspeita de contaminação',
  ps: 'Passagem – PS (parcelamento de vila)',
};
const COR_ZONA = { ZEU: '#7c4dcc', ZC: '#1d84c9', 'ZEIS-5': '#0f9b8e', ZEM: '#d6409f' };
const PALETA = ['#b45309', '#4d7c0f', '#be123c', '#0369a1', '#7e22ce', '#a16207', '#15803d', '#9f1239', '#475569', '#c2410c'];
const PADRAO = { modo: 'faixa', area: 2500, forma: 0, nmax: 61, est: 2100, pnr: 50,
  vila: false, semAlerta: false, ordem: 'area', faixas: { 0: true, 1: true, 2: true, 3: true }, busca: '', soTela: true };
const ZOOM_LOTES = 15;
const NMAX_SEM_LIMITE = 61, EST_SEM_LIMITE = 2100;
const CHAVE_ZONAS = 'radar-terrenos.zonas';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const fmt = n => Math.round(n).toLocaleString('pt-BR');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const semAcento = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const plural = (n, s, p) => `${fmt(n)} ${n === 1 ? s : p}`;

let est = structuredClone(PADRAO);
let meta = null, manchas = [], porId = new Map(), selecionada = null;
const zonaInfo = new Map();          // código -> { cod, arq, nome, manchas, celulas:Set, cor }
let zonasSel = new Set();            // zoneamentos marcados
const cacheManchas = new Map();      // código -> Promise<feições>
const cacheZonas = new Map();        // código -> Promise<feição do perímetro>
const cacheLotes = new Map();        // "ZONA/célula" -> Promise<feições>
let CEL = 0.01;

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
  customAttribution: 'Lotes, zoneamento e restrições: <a href="https://geosampa.prefeitura.sp.gov.br" target="_blank" rel="noopener">GeoSampa / PMSP</a>' }), 'bottom-right');
map.dragRotate.disable();
map.touchZoomRotate.disableRotation();

const fc = feats => ({ type: 'FeatureCollection', features: feats || [] });

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

const exprCor = () => ['match', ['get', est.modo], 3, CORES[3], 2, CORES[2], 1, CORES[1], CORES[0]];
const exprContorno = () => ['match', ['get', est.modo], 3, '#12662f', 2, '#8a6700', 1, '#9c1f24', '#555a63'];

const alertasDe = p => Object.keys(ALERTAS).filter(k => p['a_' + k]);

function passaBusca(m) { return !est.busca || m._texto.includes(est.busca); }
function passa(m) {
  const p = m.properties;
  if (p.area < est.area || p.forma < est.forma) return false;
  if (!est.faixas[p[est.modo]]) return false;
  if (est.nmax < NMAX_SEM_LIMITE && p.n > est.nmax) return false;
  if (est.est < EST_SEM_LIMITE && p.est_m > est.est) return false;
  if (est.pnr < 100 && p.pnr > est.pnr) return false;
  if (est.vila && !p.vila) return false;
  if (est.semAlerta && m._alerta) return false;
  return passaBusca(m);
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
  if (est.vila) f.push(['!=', ['get', 'vila'], '']);
  if (est.semAlerta) f.push(['!', ['boolean', ['get', '_al'], false]]);
  if (est.busca) f.push(['in', ['get', 'id'], ['literal', manchas.filter(passaBusca).map(m => m.properties.id)]]);
  return f;
}

map.on('load', async () => {
  let tombados, estacoes;
  try {
    [meta, tombados, estacoes] = await Promise.all([
      dados('meta.json'), dados('tombados.geojson.gz'), dados('estacoes.geojson')]);
  } catch (e) {
    $('#subtitulo').textContent = 'Não foi possível carregar os dados.';
    console.error(e);
    return;
  }
  CEL = meta.parametros.celula_lotes;
  let n = 0;
  for (const z of meta.zonas) {
    zonaInfo.set(z.cod, { ...z, celulas: new Set(z.celulas), cor: COR_ZONA[z.cod] || PALETA[n++ % PALETA.length] });
  }

  for (const s of ['zonas', 'tombados', 'restricoes', 'alertas', 'estacoes', 'pontos', 'lotes', 'selecao', 'lote-sel']) {
    map.addSource(s, { type: 'geojson', data: fc() });
  }
  map.addSource('manchas', { type: 'geojson', data: fc(), promoteId: 'id' });
  map.getSource('tombados').setData(tombados);
  map.getSource('estacoes').setData(estacoes);

  map.addLayer({ id: 'zonas-fill', type: 'fill', source: 'zonas',
    paint: { 'fill-color': ['get', 'cor'], 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.14, 15, 0.10] } });
  map.addLayer({ id: 'zonas-line', type: 'line', source: 'zonas', minzoom: 12.5,
    paint: { 'line-color': ['get', 'cor'], 'line-width': 1, 'line-opacity': 0.55 } });
  map.addLayer({ id: 'restricoes-fill', type: 'fill', source: 'restricoes', layout: { visibility: 'none' },
    paint: { 'fill-color': '#4b5563', 'fill-opacity': 0.22 } });
  map.addLayer({ id: 'restricoes-line', type: 'line', source: 'restricoes', layout: { visibility: 'none' },
    paint: { 'line-color': '#374151', 'line-width': 1, 'line-dasharray': [3, 2] } });

  map.addLayer({ id: 'manchas-fill', type: 'fill', source: 'manchas', minzoom: 12.5,
    paint: { 'fill-color': exprCor(),
      'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.8, 0.58] } });
  map.addLayer({ id: 'vias-fill', type: 'fill', source: 'lotes', minzoom: ZOOM_LOTES, filter: ['==', ['get', 'v'], 1],
    paint: { 'fill-color': '#3b4fd6', 'fill-opacity': 0.5 } });
  map.addLayer({ id: 'lotes-line', type: 'line', source: 'lotes', minzoom: ZOOM_LOTES,
    paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.5, 18, 1.4], 'line-opacity': 0.9 } });
  map.addLayer({ id: 'lotes-fill', type: 'fill', source: 'lotes', minzoom: ZOOM_LOTES,
    paint: { 'fill-color': '#000', 'fill-opacity': 0 } });
  map.addLayer({ id: 'manchas-line', type: 'line', source: 'manchas', minzoom: 12.5,
    paint: { 'line-color': exprContorno(),
      'line-width': ['interpolate', ['linear'], ['zoom'], 12.5, 0.6, 17, 2.2] } });
  map.addLayer({ id: 'alertas-line', type: 'line', source: 'alertas', layout: { visibility: 'none' },
    paint: { 'line-color': '#c2410c', 'line-width': 1.2 } });
  map.addLayer({ id: 'alertas-fill', type: 'fill', source: 'alertas', layout: { visibility: 'none' },
    paint: { 'fill-color': '#ea580c', 'fill-opacity': ['match', ['get', 't'], 'g1', 0.08, 0.28] } });
  map.addLayer({ id: 'tombados-fill', type: 'fill', source: 'tombados', minzoom: 13,
    paint: { 'fill-color': '#9aa1ab', 'fill-opacity': 0.55 } });
  map.addLayer({ id: 'tombados-line', type: 'line', source: 'tombados', minzoom: 13,
    paint: { 'line-color': '#6b7280', 'line-width': 0.7 } });
  map.addLayer({ id: 'pontos', type: 'circle', source: 'pontos', maxzoom: 12.5,
    layout: { 'circle-sort-key': ['get', 'area_eq'] },
    paint: { 'circle-color': exprCor(), 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.6,
      'circle-opacity': 0.9,
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9.5, 1.6, 12.5, 4.5] } });
  map.addLayer({ id: 'estacoes', type: 'circle', source: 'estacoes', minzoom: 11.5,
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11.5, 2.5, 16, 6], 'circle-color': '#fff',
      'circle-stroke-color': '#1c2430', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 11.5, 1.5, 16, 3] } });
  map.addLayer({ id: 'selecao-halo', type: 'line', source: 'selecao',
    paint: { 'line-color': '#fff', 'line-width': 6 } });
  map.addLayer({ id: 'selecao', type: 'line', source: 'selecao',
    paint: { 'line-color': '#1c2430', 'line-width': 2.6 } });
  map.addLayer({ id: 'lote-sel', type: 'line', source: 'lote-sel',
    paint: { 'line-color': '#0f5fd7', 'line-width': 3 } });

  $('#subtitulo').textContent = `São Paulo · dados GeoSampa de ${meta.gerado_em.split('-').reverse().join('/')}`;
  preencherCriterios();

  // zoneamentos marcados: link > última escolha neste navegador > padrão
  const h = new URLSearchParams(location.hash.slice(1));
  const [zLink, idLink] = (h.get('m') || '').includes('~') ? h.get('m').split('~') : [null, null];
  let salvas = null;
  try { salvas = JSON.parse(localStorage.getItem(CHAVE_ZONAS)); } catch { /* sem armazenamento */ }
  zonasSel = new Set((Array.isArray(salvas) && salvas.length ? salvas : meta.zonas_padrao).filter(z => zonaInfo.has(z)));
  if (zLink && zonaInfo.has(zLink)) zonasSel.add(zLink);

  ligarInterface();
  interacoes();
  await trocarZonas();
  map.on('moveend', () => { atualizarLista(); carregarLotes(); gravarHash(); });
  if (idLink && porId.has(idLink)) selecionar(idLink, true);
});

/* ------------------------------------------------------------ zoneamentos */
function buscarManchas(z) {
  if (!cacheManchas.has(z)) {
    cacheManchas.set(z, dados(`manchas/${zonaInfo.get(z).arq}.geojson.gz`).then(d => {
      for (const m of d.features) {
        const p = m.properties;
        m._alerta = alertasDe(p).length > 0;
        p._al = m._alerta;
        m._texto = semAcento(`${p.ruas} ${p.distrito} ${p.id} ${p.zona}`);
      }
      return d.features;
    }).catch(e => { cacheManchas.delete(z); console.error(e); return []; }));
  }
  return cacheManchas.get(z);
}
function buscarPerimetro(z) {
  if (!cacheZonas.has(z)) {
    cacheZonas.set(z, dados(`zonas/${zonaInfo.get(z).arq}.geojson.gz`).then(d => {
      d.features.forEach(f => { f.properties.cor = zonaInfo.get(z).cor; });
      return d.features;
    }).catch(() => { cacheZonas.delete(z); return []; }));
  }
  return cacheZonas.get(z);
}

let trocaAtual = 0;
async function trocarZonas() {
  const minha = ++trocaAtual;
  const lista = [...zonasSel];
  try { localStorage.setItem(CHAVE_ZONAS, JSON.stringify(lista)); } catch { /* sem armazenamento */ }
  desenharSeletor();
  $('#contagem').textContent = 'Carregando zoneamentos…';
  const [ms, ps] = await Promise.all([
    Promise.all(lista.map(buscarManchas)), Promise.all(lista.map(buscarPerimetro))]);
  if (minha !== trocaAtual) return;
  manchas = ms.flat();
  porId = new Map(manchas.map(m => [m.properties.id, m]));
  map.getSource('manchas').setData(fc(manchas));
  map.getSource('pontos').setData(fc(manchas.map(m => ({ type: 'Feature', properties: m.properties,
    geometry: { type: 'Point', coordinates: [m.properties.lon, m.properties.lat] } }))));
  map.getSource('zonas').setData(fc(ps.flat()));
  if (selecionada && !porId.has(selecionada)) fecharDetalhe();
  aplicar();
  carregarLotes();
}

function desenharSeletor() {
  const sel = [...zonasSel];
  $('#zonas-resumo').textContent = sel.length ? sel.join(', ') : 'nenhum';
  $('#zonas-lista').innerHTML = meta.zonas.map(z => {
    const i = zonaInfo.get(z.cod);
    return `<label title="${esc(z.nome)}"><input type="checkbox" value="${esc(z.cod)}" ${zonasSel.has(z.cod) ? 'checked' : ''}>
      <i class="am" style="background:${i.cor}33;border:1.5px solid ${i.cor}"></i>
      <span class="zc">${esc(z.cod)}</span><span class="zn">${esc(z.nome)}</span><span class="zq">${fmt(z.manchas)}</span></label>`;
  }).join('');
}

/* -------------------------------------------------------- filtros / cores */
function aplicar() {
  const f = exprFiltro();
  for (const id of ['manchas-fill', 'manchas-line', 'pontos']) map.setFilter(id, f);
  map.setPaintProperty('manchas-fill', 'fill-color', exprCor());
  map.setPaintProperty('pontos', 'circle-color', exprCor());
  map.setPaintProperty('manchas-line', 'line-color', exprContorno());
  desenharLegenda();
  atualizarLista();
  desenharLotes();
  gravarHash();
}

function desenharLegenda() {
  const guarda = est.faixas;
  est.faixas = { 0: 1, 1: 1, 2: 1, 3: 1 };
  const cont = { 0: 0, 1: 0, 2: 0, 3: 0 };
  for (const m of manchas) if (passa(m)) cont[m.properties[est.modo]]++;
  est.faixas = guarda;
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
  $('#contagem').textContent = !zonasSel.size ? 'Marque ao menos um zoneamento'
    : est.soTela && !est.busca ? `${fmt(lista.length)} na tela · ${fmt(total)} no total` : `${fmt(total)} manchas`;
  const top = lista.slice(0, 150);
  $('#lista').innerHTML = top.length ? top.map(m => {
    const p = m.properties;
    return `<li data-id="${esc(p.id)}" class="${selecionada === p.id ? 'sel' : ''}">
      <span class="faixa" style="background:${CORES[p[est.modo]]}"></span>
      <span class="corpo"><span class="tit">${esc(titulo(p))}</span>
        <span class="sub">${esc(p.distrito)} · ${esc(p.zona)} · ${p.n} lote${p.n > 1 ? 's' : ''} · forma ${p.forma}${p.vila ? ' · vila' : ''}${m._alerta ? ' · ⚠' : ''}</span></span>
      <span class="num"><b>${fmt(p.area)} m²</b><span>${est.modo === 'faixa' ? `equiv. ${fmt(p.area_eq)}` : `IPTU ${fmt(p.area_iptu)}`}</span></span>
    </li>`; }).join('') + (lista.length > top.length ? `<li class="vazio">+ ${fmt(lista.length - top.length)} manchas — aproxime o mapa ou refine os filtros.</li>` : '')
    : '<li class="vazio">Nenhuma mancha com esses filtros nesta área. Afaste o mapa, relaxe os filtros ou marque outros zoneamentos.</li>';
}

/* ------------------------------------------------------------------ lotes */
function arquivosVisiveis() {
  const b = map.getBounds(), out = [];
  const x0 = Math.floor(b.getWest() / CEL) - 1, x1 = Math.floor(b.getEast() / CEL) + 1;
  const y0 = Math.floor(b.getSouth() / CEL) - 1, y1 = Math.floor(b.getNorth() / CEL) + 1;
  for (const z of zonasSel) {
    const cels = zonaInfo.get(z).celulas;
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) if (cels.has(`${x}_${y}`)) out.push(`${z}/${x}_${y}`);
  }
  return out;
}
function buscarLotes(chave) {
  if (!cacheLotes.has(chave)) {
    const [z, cel] = chave.split('/');
    cacheLotes.set(chave, dados(`lotes/${zonaInfo.get(z).arq}/${cel}.geojson.gz`).then(d => d.features)
      .catch(() => { cacheLotes.delete(chave); return []; }));
  }
  return cacheLotes.get(chave);
}
let pedido = 0;
async function carregarLotes() {
  $('#aviso').hidden = map.getZoom() >= 12.5;
  $('#aviso').textContent = 'Aproxime para ver o contorno das manchas e dos lotes';
  if (map.getZoom() < ZOOM_LOTES - 0.5) return;
  const meu = ++pedido;
  const arqs = arquivosVisiveis();
  if (arqs.length > 80) return;
  await Promise.all(arqs.map(buscarLotes));
  if (meu === pedido) desenharLotes();
}
async function desenharLotes() {
  if (!map.getSource('lotes') || map.getZoom() < ZOOM_LOTES - 0.5) return;
  const arqs = arquivosVisiveis().filter(c => cacheLotes.has(c));
  const partes = await Promise.all(arqs.map(c => cacheLotes.get(c)));
  const feats = partes.flat().filter(l => { const m = porId.get(l.properties.m); return m && passa(m); });
  map.getSource('lotes').setData(fc(feats));
}

/* -------------------------------------------------------------- interação */
// Ao passar o mouse: só área total, área construída, nº de lotes, zoneamento e alertas.
function dicaMancha(p) {
  const al = alertasDe(p).map(k => `<div class="al">⚠ ${ALERTAS[k]} — ${plural(p['a_' + k], 'lote', 'lotes')}</div>`).join('');
  return `<table class="dica-t">
    <tr><td>Área total</td><td><b>${fmt(p.area)} m²</b></td></tr>
    <tr><td>Área construída</td><td>${fmt(p.ac)} m²</td></tr>
    <tr><td>Lotes</td><td>${p.n}</td></tr>
    <tr><td>Zoneamento</td><td>${esc(p.zona)}</td></tr></table>${al}`;
}

function interacoes() {
  const dica = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'dica', offset: 12, maxWidth: '300px' });
  let hoverId = null;
  const soltar = () => {
    if (hoverId !== null) map.setFeatureState({ source: 'manchas', id: hoverId }, { hover: false });
    hoverId = null;
  };
  map.on('mousemove', 'manchas-fill', e => {
    const p = e.features[0].properties;
    if (hoverId !== p.id) {
      soltar(); hoverId = p.id;
      map.setFeatureState({ source: 'manchas', id: hoverId }, { hover: true });
      dica.setHTML(dicaMancha(p));
    }
    map.getCanvas().style.cursor = 'pointer';
    dica.setLngLat(e.lngLat).addTo(map);
  });
  map.on('mouseleave', 'manchas-fill', () => { soltar(); dica.remove(); map.getCanvas().style.cursor = ''; });
  const rotRestr = p => `<b>Restrição</b><br>${esc(p.t)}`;
  for (const [camada, rot] of [['pontos', dicaMancha], ['estacoes', p => `<b>${esc(p.nome)}</b>`],
    ['tombados-fill', p => `<b>Bem tombado</b><br>${esc(p.nome)}${p.sit && p.sit !== 'TOMBADO' ? ' (' + esc(p.sit) + ')' : ''}`],
    ['restricoes-fill', rotRestr], ['alertas-fill', p => `<b>Alerta</b><br>${esc(ALERTAS[p.t] || p.t)}`]]) {
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
  map.getSource('lote-sel').setData(fc());
  if (voar) {
    const xs = m.geometry.coordinates[0].map(c => c[0]), ys = m.geometry.coordinates[0].map(c => c[1]);
    const estreito = matchMedia('(max-width: 760px)').matches;
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { padding: estreito ? 50 : 110, maxZoom: 17.6, duration: 700 });
  }
  $$('#lista li').forEach(li => li.classList.toggle('sel', li.dataset.id === id));
  desenharDetalhe(m, null);
  gravarHash();
  const lotes = (await buscarLotes(`${m.properties.zona}/${m.properties.cel}`)).filter(l => l.properties.m === id);
  if (selecionada === id) {
    desenharDetalhe(m, lotes);
    if (sqlDestaque) destacarLote(lotes, sqlDestaque);
  }
}

function destacarLote(lotes, sql) {
  const l = lotes.find(x => x.properties.sql === sql);
  map.getSource('lote-sel').setData(l || fc());
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
  const al = alertasDe(p);
  const rotLote = q => (q.al || '').split(',').filter(Boolean)
    .map(k => (k === 'cs' || k === 'cp') && q.cont ? q.cont : ALERTAS[k]).join(' · ');
  el.innerHTML = `
    <div class="topo"><button class="link" id="voltar" type="button">← Voltar à lista</button>
      <button class="link" id="copiar" type="button">Copiar link</button></div>
    <h3>${esc(titulo(p))}</h3>
    <p class="onde">${esc(p.distrito)} · setor-quadra ${esc(p.id.slice(0, 7))}${p.ruas.split(' · ').length > 2 ? ' · também ' + esc(p.ruas.split(' · ').slice(2).join(', ')) : ''}</p>
    <div class="tags"><span class="tag t${fx}">${rotFaixa}</span><span class="tag zona" title="${esc(zonaInfo.get(p.zona)?.nome || '')}">${esc(p.zona)}</span>${p.vila ? `<span class="tag vila">Vila ${p.vila}</span>` : ''}</div>
    <div class="cartoes">
      <div class="cartao"><span>Área somada</span><b>${fmt(p.area)}</b> <small>m²</small></div>
      <div class="cartao"><span>Área equivalente</span><b>${fmt(p.area_eq)}</b> <small>m²</small></div>
      <div class="cartao"><span>Lotes</span><b>${p.n}</b></div>
    </div>
    ${al.length ? `<div class="alerta"><b>⚠ Pontos de atenção</b><ul>${al.map(k => `<li>${ALERTAS[k]} — ${plural(p['a_' + k], 'lote', 'lotes')}</li>`).join('')}</ul>Os lotes atingidos estão marcados na tabela abaixo.</div>` : ''}
    ${p.vila === 'completa' ? `<div class="alerta info">Inclui uma <b>vila completa</b>: a passagem (em azul no mapa) e todas as casas que dão para ela estão na mancha. A área da passagem está somada.</div>` : ''}
    ${p.vila === 'parcial' ? `<div class="alerta info">Inclui <b>passagem de vila</b> (em azul no mapa), mas alguma casa que encosta nela ficou de fora da mancha — tombada, prédio, com restrição ou em outro zoneamento.</div>` : ''}
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
      <dt>Zoneamento</dt><dd>${esc(p.zona)}${zonaInfo.get(p.zona)?.nome ? ' — ' + esc(zonaInfo.get(p.zona).nome) : ''}</dd>
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
      ${lotes.map(l => { const q = l.properties; return `<tr data-sql="${esc(q.sql)}"><td>${esc(q.end || '—')}<div class="sqlc">${esc(q.sql)}</div>${q.al ? `<div class="lote-al">⚠ ${esc(rotLote(q))}</div>` : ''}</td>
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
      ['SQL', 'Endereço', 'Zona', 'Uso', 'Área terreno IPTU (m²)', 'Área do desenho (m²)', 'Área construída (m²)', 'Alertas'],
      lotes.map(l => { const q = l.properties; return [q.sql, q.end, p.zona, q.uso, q.at, q.ag, q.ac, rotLote(q)]; }));
    $$('table.lotes tr[data-sql]').forEach(tr => tr.onclick = () => destacarLote(lotes, tr.dataset.sql));
  }
}

function fecharDetalhe() {
  selecionada = null;
  $('#detalhe').hidden = true;
  map.getSource('selecao').setData(fc());
  map.getSource('lote-sel').setData(fc());
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
  $('#f-vila').checked = est.vila; $('#f-sem-alerta').checked = est.semAlerta;
  $('#so-tela').checked = est.soTela; $('#ordem').value = est.ordem;
  $('#o-area').textContent = fmt(est.area); $('#o-forma').textContent = est.forma;
  $('#o-nmax').textContent = est.nmax >= NMAX_SEM_LIMITE ? 'sem limite' : est.nmax;
  $('#o-est').textContent = est.est >= EST_SEM_LIMITE ? 'sem limite' : `${fmt(est.est)} m`;
  $$('#modo-cor button').forEach(b => b.classList.toggle('ativa', b.dataset.modo === est.modo));
}

const camadaTardia = {};   // camadas pesadas só são baixadas quando ligadas
function ligarInterface() {
  sincronizarControles();
  const liga = (sel, chave, conv = Number) => $(sel).addEventListener('input', e => {
    est[chave] = e.target.type === 'checkbox' ? e.target.checked : conv(e.target.value);
    sincronizarControles(); aplicar();
  });
  liga('#f-area', 'area'); liga('#f-forma', 'forma'); liga('#f-nmax', 'nmax'); liga('#f-est', 'est'); liga('#f-pnr', 'pnr');
  liga('#f-vila', 'vila'); liga('#f-sem-alerta', 'semAlerta'); liga('#so-tela', 'soTela');
  liga('#ordem', 'ordem', String);
  let t;
  $('#busca').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { est.busca = semAcento(e.target.value.trim()); aplicar(); }, 180);
  });
  $('#zonas-lista').addEventListener('change', e => {
    if (e.target.checked) zonasSel.add(e.target.value); else zonasSel.delete(e.target.value);
    trocarZonas();
  });
  $('#zonas-padrao').onclick = () => { zonasSel = new Set(meta.zonas_padrao.filter(z => zonaInfo.has(z))); trocarZonas(); };
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
  $$('[data-camada]').forEach(c => c.addEventListener('change', async () => {
    const nome = c.dataset.camada;
    if (c.checked && (nome === 'restricoes' || nome === 'alertas') && !camadaTardia[nome]) {
      camadaTardia[nome] = dados(`${nome}.geojson.gz`).then(d => map.getSource(nome).setData(d)).catch(console.error);
      await camadaTardia[nome];
    }
    const v = c.checked ? 'visible' : 'none';
    const ids = { zonas: ['zonas-fill', 'zonas-line'], tombados: ['tombados-fill', 'tombados-line'],
      restricoes: ['restricoes-fill', 'restricoes-line'], alertas: ['alertas-fill', 'alertas-line'], estacoes: ['estacoes'] }[nome];
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
      ['ID (setor-quadra-lote)', 'Zoneamento', 'Distrito', 'Ruas', 'Área somada (m²)', 'Área IPTU (m²)', 'Área equivalente (m²)',
        'Faixa', 'Lotes', 'Nota de forma', 'Retangularidade', 'Miolo aproveitável', 'Perímetro×área', 'Vértices',
        'Área construída (m²)', 'CA médio', 'Usos', 'Vila', 'Alertas', 'Estação mais próxima', 'Distância (m)', 'Latitude', 'Longitude', 'Link'],
      l.map(m => { const p = m.properties; return [p.id, p.zona, p.distrito, p.ruas, p.area, p.area_iptu, p.area_eq,
        ['forma compromete', 'vermelho', 'amarelo', 'verde'][p.faixa], p.n, p.forma, p.ret, p.miolo, p.comp, p.nv, p.ac,
        String(p.ca).replace('.', ','), p.usos, p.vila,
        alertasDe(p).map(k => `${ALERTAS[k]} (${p['a_' + k]})`).join(' | '),
        p.est, p.est_m, String(p.lat).replace('.', ','), String(p.lon).replace('.', ','),
        `${location.origin}${location.pathname}#m=${p.zona}~${p.id}`]; }));
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
  $('#meta-rodape').textContent = `${fmt(meta.lotes_analisados)} lotes analisados · ${fmt(meta.lotes_elegiveis)} elegíveis · ${fmt(meta.manchas)} manchas em ${meta.zonas.length} zoneamentos. Fonte: ${meta.fonte}.`;
}

/* ------------------------------------------------------------------- hash */
function gravarHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const m = selecionada && porId.get(selecionada);
  if (m) h.set('m', `${m.properties.zona}~${selecionada}`); else h.delete('m');
  const s = '#' + [...h].map(([k, v]) => `${k}=${v}`).join('&');
  if (s !== location.hash) history.replaceState(null, '', s);
}
})();
