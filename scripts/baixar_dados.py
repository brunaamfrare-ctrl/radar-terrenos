#!/usr/bin/env python3
"""Baixa do GeoSampa (WFS) as camadas usadas pelo Radar de Terrenos.

Saída (pasta dados_brutos/, não versionada):
  zonas.json           perímetros de zona (Lei 18.177/24), todas as zonas
  tombados.json        bens tombados (polígonos de lote)
  envoltoria_*.json    áreas envoltórias CONPRESP / CONDEPHAAT / IPHAN
  <restrição>.json     área cedida, CAP, passagem, diretriz, desmembramento, conjunto
  <alerta>.json        melhoramento viário, desapropriação, geotécnica, contaminadas
  lotes.ndjson.gz      todos os lotes fiscais (lote_cidadao) da cidade

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
        p = {"typeNames": f"geoportal:{camada}", "count": PAGINA}
        if ordenar:
            p["sortBy"] = ordenar
        if ordenar or inicio:      # camadas sem chave recusam startIndex sem ordenação
            p["startIndex"] = inicio
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


# Camadas que EXCLUEM o lote (além de tombamento) e camadas que só geram ALERTA.
RESTRICOES = {
    "area_cedida": ("GEOSAMPA_area_cedida_cidadao", None),
    "cap": ("GEOSAMPA_cadastro_area_publica_cidadao", None),
    "diretriz": ("diretriz", None),
    "desmembramento_gleba": ("desmembramento_gleba", None),
    "conjunto": ("conjunto", None),
    "operacao_urbana": ("operacao_urbana", None),
}
ALERTAS = {
    "melhoramento_viario": ("geoconvias_lei_melhoramento_vigente", None),
    "desapropriacao": ("planta_dup_dis_pd", None),
    "geotecnica_1": ("carta_geotecnica", "tx_unidade_geotecnica='I'"),
    "contaminada_svma": ("area_contaminada_reabilitada_svma", None),
    "contaminada_sigac": ("GEOSAMPA_area_contaminada_sigac", None),
    "passagem": ("passagem", None),
}


# Equipamentos cujo lote é descartado (pontos de endereço, exceto o complexo de saúde).
EQUIPAMENTOS = {
    "eq_hospital": "equipamento_saude_hospital",
    "eq_complexo_saude": "complexo_saude_existente",
    "eq_escola_publica": "equipamento_educacao_rede_publica",   # o processamento filtra as estaduais
    "eq_policia_civil": "equipamento_policia_civil",
    "eq_policia_militar": "equipamento_policia_militar",
    "eq_guarda_civil": "equipamento_guarda_civil_metropolitana",
}


# Sinais de empreendimento novo ou em aprovação (lançamento): o lote é descartado.
LICENCIAMENTOS = {
    "lic_oodc": "outorga_onerosa_direito_construir",
    "lic_his_hmp": "habitacao_popular",
    "lic_cota": "cota_solidariedade_ponto",
    "lic_fruicao": "fruicao_publica",
    "lic_eiv": "GEOSAMPA_licenca_obra",
    "lic_habi": "habita2geosampa_habi_conjhabitacional2geosampa_ext",
}


def baixar_equipamentos():
    print("Equipamentos e licenciamentos…")
    for nome, camada in {**EQUIPAMENTOS, **LICENCIAMENTOS}.items():
        salvar(f"{nome}.json", baixar_camada(camada, ordenar=None))


def baixar_restricoes():
    print("Restrições e alertas…")
    for nome, (camada, cql) in {**RESTRICOES, **ALERTAS}.items():
        salvar(f"{nome}.json", baixar_camada(camada, cql=cql, ordenar=None))


def main():
    os.makedirs(BRUTOS, exist_ok=True)
    if "--so-apoio" in sys.argv:
        return baixar_apoio()
    if "--so-equipamentos" in sys.argv:
        salvar("operacao_urbana.json", baixar_camada("operacao_urbana", ordenar=None))
        return baixar_equipamentos()

    print("Zoneamento…")
    zonas = baixar_camada(CAMADA_ZONAS)          # todas as zonas da cidade
    salvar("zonas.json", zonas)

    print("Patrimônio…")
    salvar("tombados.json", baixar_camada("patrimonio_cultural_bem_tombado"))
    for org in ("CONPRESP", "CONDEPHAAT", "IPHAN"):
        salvar(f"envoltoria_{org}.json",
               baixar_camada(f"patrimonio_cultural_area_envoltoria_{org}"))
    baixar_restricoes()
    baixar_equipamentos()
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
            b = box(i * CELULA, j * CELULA, (i + 1) * CELULA, (j + 1) * CELULA)
            if any(geoms[k].intersects(b) for k in arv.query(b)):
                celulas.append((i, j))
    print(f"  {len(celulas)} células de {CELULA:.0f} m")

    # Retomada: células já baixadas ficam em lotes.celulas.txt; apague esse
    # arquivo e o lotes.ndjson.gz para baixar tudo de novo.
    arq_lotes = os.path.join(BRUTOS, "lotes.ndjson.gz")
    arq_feitas = os.path.join(BRUTOS, "lotes.celulas.txt")
    feitas, vistos = set(), set()
    if os.path.exists(arq_feitas) and os.path.exists(arq_lotes):
        feitas = {tuple(map(int, l.split(","))) for l in open(arq_feitas) if l.strip()}
        with gzip.open(arq_lotes, "rt") as fh:
            for linha in fh:
                k = linha.find('"cd_identificador":')
                vistos.add(int(linha[k + 19:linha.find(",", k)]))
        print(f"  retomando: {len(feitas)} células e {len(vistos)} lotes já baixados")
    else:
        for arq in (arq_lotes, arq_feitas):
            if os.path.exists(arq):
                os.remove(arq)
    pendentes = [c for c in celulas if c not in feitas]

    n = len(vistos)
    with gzip.open(arq_lotes, "at") as out, open(arq_feitas, "a") as reg, \
            ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(baixar_lotes_celula,
                          (i * CELULA, j * CELULA, (i + 1) * CELULA, (j + 1) * CELULA)): (i, j)
                for i, j in pendentes}
        for k, fut in enumerate(as_completed(futs), 1):
            for f in fut.result():
                i = f["properties"]["cd_identificador"]
                if i in vistos or not f["geometry"]:
                    continue
                vistos.add(i)
                out.write(json.dumps(f, separators=(",", ":")) + "\n")
                n += 1
            reg.write("%d,%d\n" % futs[fut])
            if k % 25 == 0 or k == len(pendentes):
                print(f"  células {k}/{len(pendentes)} — {n} lotes", flush=True)
                out.flush(); reg.flush()
    print("Concluído.")


if __name__ == "__main__":
    main()
