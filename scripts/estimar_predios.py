#!/usr/bin/env python3
"""Estima a área construída dos prédios (lotes em condomínio, os "CD").

O cadastro público não traz a área construída dos condomínios, então a
estimativa sai da volumetria: para cada edificação desenhada dentro do lote
(camada "Edificações" do GeoSampa, que tem a altura de cada volume),

    pavimentos = altura / ALTURA_PAVIMENTO   (mínimo 1)
    área       = projeção dentro do lote x pavimentos

e a área do prédio é a soma dos volumes. Subsolos não entram, e a camada é de
um levantamento antigo: prédios mais novos que ele saem sem estimativa certa.

Saída: dados_brutos/predios.json  {id do lote: {ac, pav, h, proj, n}}
Uso:   python3 scripts/estimar_predios.py     (depois de baixar_dados.py)
"""
import gzip
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import shapely
from shapely import STRtree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baixar_dados import BRUTOS, CELULA, PAGINA, wfs_get  # noqa: E402

ALTURA_PAVIMENTO = 2.9   # m por andar (pé-direito + laje)
INTERSECAO_MIN = 5.0     # m² — ignora edificação que só encosta no lote


def condominios():
    """Lotes fiscais em condomínio: (ids, geometrias)."""
    ids, geoms = [], []
    with gzip.open(os.path.join(BRUTOS, "lotes.ndjson.gz"), "rt") as fh:
        for linha in fh:
            if '"cd_condominio":"00"' in linha and '"Condomínio"' not in linha:
                continue
            f = json.loads(linha)
            p, g = f["properties"], f["geometry"]
            if p["cd_tipo_lote"] != "F" or g["type"] != "Polygon":
                continue
            ids.append(p["cd_identificador"])
            geoms.append(shapely.Polygon(g["coordinates"][0], g["coordinates"][1:]))
    return ids, shapely.make_valid(np.array(geoms))


def baixar_celula(c):
    i, j = c
    feats, inicio = [], 0
    while True:
        d = wfs_get({"typeNames": "geoportal:edificacao", "count": PAGINA, "startIndex": inicio,
                     "sortBy": "cd_identificador",
                     "propertyName": "cd_identificador,qt_altura_edificacao,ge_poligono",
                     "bbox": f"{i * CELULA},{j * CELULA},{(i + 1) * CELULA},{(j + 1) * CELULA},"
                             "urn:ogc:def:crs:EPSG::31983"})
        feats.extend(d["features"])
        if len(d["features"]) < PAGINA:
            return feats
        inicio += PAGINA


def main():
    print("Lendo condomínios…", flush=True)
    ids, lotes = condominios()
    arv = STRtree(lotes)
    celulas = set()
    for x0, y0, x1, y1 in shapely.bounds(lotes):
        for i in range(int(math.floor(x0 / CELULA)), int(math.floor(x1 / CELULA)) + 1):
            for j in range(int(math.floor(y0 / CELULA)), int(math.floor(y1 / CELULA)) + 1):
                celulas.add((i, j))
    celulas = sorted(celulas)
    print(f"  {len(ids)} condomínios em {len(celulas)} células", flush=True)

    acum = {}            # índice do lote -> [área, pavimentos máx, altura máx, projeção, nº volumes]
    vistos = set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(baixar_celula, c) for c in celulas]
        for k, fut in enumerate(as_completed(futs), 1):
            for f in fut.result():
                p = f["properties"]
                if p["cd_identificador"] in vistos or not f["geometry"]:
                    continue
                vistos.add(p["cd_identificador"])
                try:
                    g = shapely.make_valid(shapely.geometry.shape(f["geometry"]))
                except Exception:
                    continue
                h = float(p["qt_altura_edificacao"] or 0)
                pav = max(1, round(h / ALTURA_PAVIMENTO))
                for n in arv.query(g, predicate="intersects"):
                    a = g.intersection(lotes[n]).area
                    if a < INTERSECAO_MIN:
                        continue
                    r = acum.setdefault(int(n), [0.0, 0, 0.0, 0.0, 0])
                    r[0] += a * pav
                    r[1] = max(r[1], pav)
                    r[2] = max(r[2], h)
                    r[3] += a
                    r[4] += 1
            if k % 50 == 0 or k == len(celulas):
                print(f"  células {k}/{len(celulas)} — {len(vistos)} edificações, "
                      f"{len(acum)} condomínios com volume", flush=True)

    saida = {str(ids[n]): {"ac": round(r[0]), "pav": r[1], "h": round(r[2], 1),
                           "proj": round(r[3]), "n": r[4]} for n, r in acum.items()}
    with open(os.path.join(BRUTOS, "predios.json"), "w") as fh:
        json.dump({"altura_pavimento": ALTURA_PAVIMENTO, "condominios": len(ids), "predios": saida}, fh)
    print(f"Concluído: {len(saida)} de {len(ids)} condomínios com estimativa.")


if __name__ == "__main__":
    main()
