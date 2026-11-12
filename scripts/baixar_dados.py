#!/usr/bin/env python3
"""Baixa do GeoSampa (WFS) as camadas usadas pelo Radar de Terrenos.

Saída (pasta dados_brutos/, não versionada):
  zonas.json           perímetros de zona (Lei 18.177/24) das zonas-alvo
  tombados.json        bens tombados (polígonos de lote)
  envoltoria_*.json    áreas envoltórias CONPRESP / CONDEPHAAT / IPHAN
  lotes.ndjson.gz      lotes fiscais (lote_cidadao) que caem nas zonas-alvo

Uso:  python3 scripts/baixar_dados.py
"""
import gzip
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from shapely.geometry import box, shape
from shapely.strtree import STRtree

WFS = "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRUTOS = os.path.join(RAIZ, "dados_brutos")

# Zonas-alvo: famílias ZEU e ZC (inclui variantes ambientais/previstas).
ZONAS_ALVO = ["ZEU", "ZEUa", "ZEUP", "ZEUPa", "ZC", "ZCa", "ZC-ZEIS"]
CAMADA_ZONAS = "perimetro_zona_lei_18177_24"
PAGINA = 20000
CELULA = 1000.0  # m — grade usada para baixar os lotes por recorte


def wfs_get(params, tentativas=6):
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "outputFormat": "application/json"}
    base.update(params)
    for t in range(tentativas):
        try:
            r = requests.get(WFS, params=base, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # rede instável: tenta de novo
            if t == tentativas - 1:
                raise
            time.sleep(3 * (t + 1))
            print(f"  nova tentativa ({t + 1}) — {type(e).__name__}", file=sys.stderr)


def baixar_camada(camada, cql=None, ordenar="cd_identificador"):
    feats, inicio = [], 0
    while True:
        p = {"typeNames": f"geoportal:{camada}", "count": PAGINA,
             "startIndex": inicio}
        if ordenar:
            p["sortBy"] = ordenar
        if cql:
            p["CQL_FILTER"] = cql
        d = wfs_get(p)
        feats.extend(d["features"])
        print(f"  {camada}: {len(feats)}/{d.get('totalFeatures', '?')}")
        if len(d["features"]) < PAGINA:
            return feats
        inicio += PAGINA


def salvar(nome, feats):
    with open(os.path.join(BRUTOS, nome), "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)


def baixar_lotes_celula(bb):
    x0, y0, x1, y1 = bb
    feats, inicio = [], 0
    while True:
        d = wfs_get({"typeNames": "geoportal:lote_cidadao", "count": PAGINA,
                     "startIndex": inicio, "sortBy": "cd_identificador",
                     "bbox": f"{x0},{y0},{x1},{y1},urn:ogc:def:crs:EPSG::31983"})
        feats.extend(d["features"])
        if len(d["features"]) < PAGINA:
            return feats
        inicio += PAGINA


def baixar_apoio():
    """Camadas de contexto: distritos e estações de metrô/trem."""
    print("Apoio…")
    for camada in ("distrito_municipal", "estacao_metro", "estacao_trem"):
        salvar(f"{camada}.json", baixar_camada(camada, ordenar=None))


def main():
    os.makedirs(BRUTOS, exist_ok=True)
    if "--so-apoio" in sys.argv:
        return baixar_apoio()

    print("Zoneamento…")
    lista = ",".join(f"'{z}'" for z in ZONAS_ALVO)
    zonas = baixar_camada(CAMADA_ZONAS, cql=f"cd_zoneamento_perimetro IN ({lista})")
    salvar("zonas.json", zonas)

    print("Patrimônio…")
    salvar("tombados.json", baixar_camada("patrimonio_cultural_bem_tombado"))
    for org in ("CONPRESP", "CONDEPHAAT", "IPHAN"):
        salvar(f"envoltoria_{org}.json",
               baixar_camada(f"patrimonio_cultural_area_envoltoria_{org}"))

    baixar_apoio()

    if "--sem-lotes" in sys.argv:
        return
    print("Lotes…")
    geoms = [shape(f["geometry"]) for f in zonas if f["geometry"]]
    arv = STRtree(geoms)
    minx = min(g.bounds[0] for g in geoms); miny = min(g.bounds[1] for g in geoms)
    maxx = max(g.bounds[2] for g in geoms); maxy = max(g.bounds[3] for g in geoms)
    celulas = []
    for i in range(int(math.floor(minx / CELULA)), int(math.ceil(maxx / CELULA))):
        for j in range(int(math.floor(miny / CELULA)), int(math.ceil(maxy / CELULA))):
            bb = (i * CELULA, j * CELULA, (i + 1) * CELULA, (j + 1) * CELULA)
            b = box(*bb)
            if any(geoms[k].intersects(b) for k in arv.query(b)):
                celulas.append(bb)
    print(f"  {len(celulas)} células de {CELULA:.0f} m")

    vistos, n = set(), 0
    with gzip.open(os.path.join(BRUTOS, "lotes.ndjson.gz"), "wt") as out, \
            ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(baixar_lotes_celula, bb) for bb in celulas]
        for k, fut in enumerate(as_completed(futs), 1):
            for f in fut.result():
                i = f["properties"]["cd_identificador"]
                if i in vistos or not f["geometry"]:
                    continue
                vistos.add(i)
                out.write(json.dumps(f, separators=(",", ":")) + "\n")
                n += 1
            if k % 25 == 0 or k == len(celulas):
                print(f"  células {k}/{len(celulas)} — {n} lotes", flush=True)
    print("Concluído.")


if __name__ == "__main__":
    main()
