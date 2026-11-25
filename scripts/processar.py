#!/usr/bin/env python3
"""Processa os dados brutos do GeoSampa e gera os arquivos do mapa (docs/dados/).

Regras (premissas de novos negócios):
  1. Lote elegível = lote fiscal, fora de condomínio ("CD"), com
     área construída / área do terreno <= CA_MAX_CASA, não tombado, fora de
     área envoltória e não atingido por área cedida, CAP, diretriz (DZ),
     desmembramento de gleba (DG) ou conjunto (CJ). Também saem os lotes de
     hospitais, escolas estaduais, polícia/guarda civil (cadastros de
     equipamentos do GeoSampa); os lotes dentro de operação urbana (perímetro
     principal, não o expandido); e os lançamentos: lotes com empreendimento
     licenciado (outorga onerosa, alvará HIS/HMP, cota de solidariedade,
     fruição pública, EIV, habitação pública) ou terreno sem construção, sem
     número e com LANCAMENTO_AREA_MIN m² ou mais.
  2. Lotes elegíveis contíguos (divisa comum >= DIVISA_MIN m) E DO MESMO
     ZONEAMENTO formam uma "mancha". Passagens de vila (lotes "V") entram como
     elo: unem as casas dos dois lados e somam área, sem contar como lote.
  3. Mancha entra no mapa se área somada >= AREA_MIN. Todas as zonas são
     processadas; o mapa mostra as que o usuário marcar.
  4. Nota de forma (0-100) combina retangularidade, compacidade
     (perímetro x área), nº de vértices e miolo aproveitável.
  5. Área equivalente = área x fator de forma -> faixa de cor.
  7. Prédios (lotes em condomínio, "CD"): a área construída é estimada pela
     volumetria (scripts/estimar_predios.py). O prédio entra nas manchas quando
     área estimada / terreno <= valor da régua do mapa (0 a CA_MAX_PREDIO, de
     PASSO_CA em PASSO_CA). As manchas são calculadas para cada posição da régua;
     cada uma guarda a faixa da régua em que existe (propriedades "na" e "nb").
  8. Metrô e trem (linhas e estações, atuais e projetados): sai o lote que uma
     linha atravessa ou que contém uma estação; fica, com alerta, o lote a até
     METRO_DIST_ALERTA m de uma linha ou estação.
  6. Alertas (não excluem): faixa não edificável (área não edificante), melhoramento viário, desapropriação (DUP/DIS),
     unidade geotécnica I, área contaminada ou com potencial de contaminação,
     Passagem - PS (parcelamento aprovado como passagem; cobre vilas inteiras).

Uso:  python3 scripts/processar.py
"""
import collections
import datetime
import gzip
import json
import math
import os
import re
import shutil
import sys

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRUTOS = os.path.join(RAIZ, "dados_brutos")
SAIDA = os.path.join(RAIZ, "docs", "dados")

# ----------------------------------------------------------------- parâmetros
CA_MAX_CASA = 4.0        # construída/terreno acima disso = prédio
FRACAO_TOMBADO = 0.3     # sobreposição com bem tombado que descarta o lote
IMPACTO_AREA = 10.0      # m² — sobreposição mínima para um lote ser "atingido"…
IMPACTO_FRACAO = 0.05    # …ou esta fração do lote (o que der menos), para lotes pequenos
IMPACTO_NUCLEO = 0.75    # m — a sobreposição precisa ter mais de 2x isso de largura
                         #     (ignora filetes de desenho na divisa)
FOLGA = 0.30             # m — tolerância para considerar lotes encostados
DIVISA_MIN = 3.0         # m — divisa comum mínima para serem contíguos
AREA_MIN = 2500.0        # m² — área mínima da mancha
FAIXAS = (2500.0, 4000.0, 6000.0)   # vermelho / amarelo / verde
RAIO_MIOLO = 10.0        # m — miolo aproveitável = trechos com largura >= 2x
TOL_VERTICES = 2.0       # m — simplificação antes de contar vértices
VERT_IDEAL, VERT_RUIM = 7, 22
PESOS = dict(ret=0.30, comp=0.20, vert=0.20, miolo=0.30)
FORMA_SEM_PENALIDADE = 0.80   # nota de forma a partir da qual não há desconto
CELULA_LOTES = 0.01      # graus — tamanho do recorte dos arquivos de lotes

CA_MAX_PREDIO = 4.0      # prédio (condomínio) com área estimada / terreno acima disso nunca entra
PASSO_CA = 0.5           # passo da régua "CA máximo dos prédios" no mapa (0 = sem prédios)
LANCAMENTO_AREA_MIN = 1000.0   # m² — terreno vago, sem número, a partir disso = provável lançamento
EQUIP_DIST_MAX = 15.0          # m — ponto de equipamento fora de lote casa com o lote mais próximo até aqui
SEM_NUMERO = {"", "S/N", "SN", "S/Nº"}

# Equipamentos cujo lote é descartado: arquivo -> (rótulo, filtro sobre os atributos)
EQUIPAMENTOS = {
    "eq_hospital": ("hospital", None),
    "eq_complexo_saude": ("hospital", lambda p: p.get("dc_tipo_perimetro") == "COMPLEXO DE SAUDE EXISTENTE"),
    "eq_escola_publica": ("escola estadual",
                          lambda p: (p.get("nm_esfera_administrativa_equipamento") or "").startswith("ESTADUAL")),
    "eq_policia_civil": ("polícia / guarda civil", None),
    "eq_policia_militar": ("polícia / guarda civil", None),
    "eq_guarda_civil": ("polícia / guarda civil", None),
    # Sinais de empreendimento novo, em obra ou em aprovação (lançamento)
    "lic_oodc": ("empreendimento licenciado (outorga onerosa)",
                 lambda p: p.get("tx_situacao_pagamento") not in ("INDEFERIDO", "DESISTÊNCIA")),
    "lic_his_hmp": ("empreendimento licenciado (alvará HIS/HMP)", None),
    "lic_cota": ("empreendimento licenciado (cota de solidariedade)", None),
    "lic_fruicao": ("empreendimento licenciado (fruição pública)", None),
    "lic_eiv": ("empreendimento licenciado (EIV)", lambda p: p.get("st_licenca_empreendimento") == "DEFERIDO"),
    # habitação pública: só o que está em projeto ou obra (o já entregue não é lançamento)
    "lic_habi": ("empreendimento habitacional público em obra/projeto",
                 lambda p: not (p.get("Estagio") or "").startswith(("00", "01"))),
}
# Onde o cadastro traz o SQL do terreno, casa também pelo código (setor, quadra, lote)
SQL_DO_REGISTRO = {
    "lic_oodc": lambda p: ((p.get("cd_setor_quadra") or "")[:3], (p.get("cd_setor_quadra") or "")[3:6],
                           (p.get("tx_codigo_lote") or "")[:4]),
    "lic_cota": lambda p: ((p.get("cd_setor_quadra") or "")[:3], (p.get("cd_setor_quadra") or "")[3:6],
                           (p.get("tx_codigo_lote") or "")[:4]),
    "lic_fruicao": lambda p: ((p.get("cd_setor_quadra") or "")[:3], (p.get("cd_setor_quadra") or "")[3:6],
                              (p.get("tx_codigo_lote") or "")[:4]),
    "lic_his_hmp": lambda p: ((p.get("cd_sql_incra") or "")[:3], (p.get("cd_sql_incra") or "")[3:6],
                              (p.get("cd_sql_incra") or "")[6:10]),
}
# Camadas em que o lote só sai se estiver DENTRO (metade ou mais da área)
RESTRICOES_DENTRO = {"operacao_urbana": "operação urbana"}

# Metrô e trem (atuais "mt" e projetados "mf"): o lote que a linha atravessa sai;
# o lote a até METRO_DIST_ALERTA m de uma linha ou estação fica, com alerta.
METRO_DIST_ALERTA = 20.0
METRO_SOB_MIN = 1.0       # m de linha dentro do lote (miolo) para valer como "passa por baixo"
METRO_LINHAS = {"linha_metro": "mt", "linha_trem": "mt",
                "linha_metro_projetada": "mf", "linha_trem_projetada": "mf"}
METRO_ESTACOES = {"estacao_metro": "mt", "estacao_trem": "mt",
                  "estacao_metro_projetada": "mf", "estacao_trem_projetada": "mf"}
METRO_ROTULO = {"mt": "metrô/trem atual", "mf": "metrô/trem projetado"}

COR_LINHA = {   # metrô (atuais e projetadas) e trens da CPTM/ViaMobilidade
    "AZUL": "#0455a1", "VERDE": "#007e5e", "VERMELHA": "#ee3524", "AMARELA": "#e0ae00", "LILAS": "#9b3894",
    "PRATA": "#8d9aa5", "RUBI": "#a60f43", "DIAMANTE": "#7d8790", "ESMERALDA": "#00927f", "TURQUESA": "#00a5a8",
    "CORAL": "#f26a5c", "SAFIRA": "#0b3c8c", "JADE": "#5fb45f", "OURO": "#c8a200", "LARANJA": "#f58220",
    "ROSA": "#ef5fa7", "MARROM": "#8b5a2b", "CELESTE": "#4fb4e0", "VIOLETA": "#7a2fbf", "ONIX": "#2b2b2b",
}
NOME_CORREGO = {"CORREGO": "Córrego", "RIBEIRAO": "Ribeirão", "BRACO": "Braço", "RIACHO": "Riacho", "RIO": "Rio",
                "DRENO": "Dreno", "CAN.": "Can.", "DA": "da", "DE": "de", "DO": "do", "DAS": "das", "DOS": "dos"}

ZONAS_FORA = {"Praça/Canteiro", "VETADO"}       # não são zonas de uso
ZONAS_PADRAO = ["ZEU", "ZC", "ZEIS-5", "ZEM"]   # marcadas ao abrir o mapa

# camada -> rótulo. As de RESTRICOES excluem o lote; as de ALERTAS só avisam.
RESTRICOES = {
    "envoltoria_CONPRESP": "área envoltória", "envoltoria_CONDEPHAAT": "área envoltória",
    "envoltoria_IPHAN": "área envoltória", "area_cedida": "área cedida",
    "cap": "área pública (CAP)", "diretriz": "diretriz (DZ)",
    "desmembramento_gleba": "desmembramento de gleba (DG)", "conjunto": "conjunto (CJ)",
}
ALERTAS = {   # camada -> código curto gravado no lote / na mancha
    "melhoramento_viario": "mv", "desapropriacao": "de", "geotecnica_1": "g1",
    "contaminada_svma": "cs", "contaminada_sigac": "cp",
    "passagem": "ps",     # Passagem - PS cobre vilas inteiras: avisa, não exclui
    "faixa_nao_edificavel": "fn",   # área não edificante (Códigos de Obras 1992/2017): avisa, não exclui
}

para_wgs = Transformer.from_crs(31983, 4326, always_xy=True)


def log(*a):
    print(*a, flush=True)


def carregar(nome):
    with open(os.path.join(BRUTOS, nome)) as f:
        feats = json.load(f)["features"]
    feats = [f for f in feats if f["geometry"]]
    geoms = shapely.make_valid(np.array([shapely.geometry.shape(f["geometry"]) for f in feats]))
    return geoms, [f["properties"] for f in feats]


def wgs(geom, casas=6):
    """Geometria UTM -> dict GeoJSON em lon/lat arredondado."""
    g = shapely.transform(geom, lambda xy: np.column_stack(para_wgs.transform(xy[:, 0], xy[:, 1])))
    if g.geom_type == "GeometryCollection":
        g = shapely.MultiPolygon([p for p in g.geoms if p.geom_type == "Polygon"])
    if g.is_empty:
        return None
    gj = json.loads(shapely.to_geojson(g))

    def arred(c):
        if not c:
            return c
        if isinstance(c[0], (int, float)):
            return [round(c[0], casas), round(c[1], casas)]
        return [arred(x) for x in c]
    gj["coordinates"] = arred(gj["coordinates"])
    return gj


def so_poligonos(arr):
    """make_valid pode devolver coleções; fica só com o maior polígono de cada."""
    arr = shapely.make_valid(arr)
    tipos = shapely.get_type_id(arr)
    for i in np.flatnonzero(tipos != 3):
        arr[i] = maior_poligono(arr[i]) or shapely.Polygon()
    return arr


def maior_poligono(g):
    if g.geom_type == "Polygon":
        return g
    partes = [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    return max(partes, key=lambda p: p.area) if partes else None


def nota_forma(u):
    """Devolve (nota 0-1, detalhes) para o polígono unificado u."""
    a = u.area
    s = u.simplify(0.5)
    ret = a / max(shapely.minimum_rotated_rectangle(u).area, 1e-9)
    comp = min(1.0, 4.0 * math.sqrt(a) / max(s.length, 1e-6))      # 1 = quadrado
    sv = u.simplify(TOL_VERTICES)
    nv = len(sv.exterior.coords) - 1 + sum(len(i.coords) - 1 for i in sv.interiors)
    vert = min(1.0, max(0.0, 1.0 - (nv - VERT_IDEAL) / (VERT_RUIM - VERT_IDEAL)))
    miolo_g = u.buffer(-RAIO_MIOLO, join_style="mitre").buffer(RAIO_MIOLO, join_style="mitre")
    try:
        miolo = min(1.0, miolo_g.intersection(u).area / a)
    except shapely.errors.GEOSException:      # geometria degenerada: usa a área direta
        miolo = min(1.0, miolo_g.area / a)
    nota = (PESOS["ret"] * ret + PESOS["comp"] * comp
            + PESOS["vert"] * vert + PESOS["miolo"] * miolo)
    return nota, dict(ret=ret, comp=comp, nv=nv, miolo=miolo)


def subdividir(geoms, max_coords=1500, lado_min=150.0):
    """Quebra polígonos muito grandes em pedaços (quadtree) para acelerar cruzamentos."""
    saida, origem = [], []

    def rec(g, k, nivel):
        if g.is_empty:
            return
        x0, y0, x1, y1 = g.bounds
        if shapely.get_num_coordinates(g) <= max_coords or max(x1 - x0, y1 - y0) <= lado_min or nivel > 12:
            saida.append(g)
            origem.append(k)
            return
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        for bb in ((x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)):
            rec(g.intersection(shapely.box(*bb)), k, nivel + 1)
    for k, g in enumerate(geoms):
        rec(g, k, 0)
    return np.array(saida, dtype=object), np.array(origem)


def atingidos(geoms, idx, camada):
    """Índices globais dos lotes de idx atingidos pela camada, com a feição que atinge.

    Atingido = sobreposição somada >= IMPACTO_AREA (ou IMPACTO_FRACAO do lote) e
    com "corpo" (não é só um filete na divisa), ou metade do lote coberta.
    """
    pedacos, origem = subdividir(camada)
    li, ci = STRtree(pedacos).query(geoms[idx], predicate="intersects")
    if not len(li):
        return {}
    inter = shapely.intersection(geoms[idx][li], pedacos[ci])
    area = shapely.area(inter)
    m = area > 0.5
    li, ci, inter, area = li[m], ci[m], inter[m], area[m]
    nucleo = shapely.area(shapely.buffer(inter, -IMPACTO_NUCLEO))
    soma, corpo, quem = collections.Counter(), set(), {}
    for l, c, a, nu in zip(li, ci, area, nucleo):
        soma[l] += a
        if nu > 0:
            corpo.add(l)
        if a > quem.get(l, (0, 0))[0]:
            quem[l] = (a, origem[c])
    area_lote = shapely.area(geoms[idx])
    return {idx[l]: quem[l][1] for l, a in soma.items()
            if a >= min(IMPACTO_AREA, IMPACTO_FRACAO * area_lote[l])
            and (l in corpo or a >= 0.5 * area_lote[l])}


def metro_geoms():
    """Linhas e estações de metrô/trem: {"mt": {"linhas", "pontos"}, "mf": {...}}."""
    out = {"mt": {"linhas": [], "pontos": []}, "mf": {"linhas": [], "pontos": []}}
    for nome, cod in METRO_LINHAS.items():
        out[cod]["linhas"].extend(carregar(nome + ".json")[0])
    for nome, cod in METRO_ESTACOES.items():
        out[cod]["pontos"].extend(carregar(nome + ".json")[0])
    return {c: {k: np.array(v, dtype=object) for k, v in d.items()} for c, d in out.items()}


def metro_sob(geoms, idx, m):
    """Índices dos lotes de idx que têm linha passando por baixo ou estação dentro."""
    achados = set()
    if len(m["linhas"]):
        trechos, _ = subdividir(m["linhas"], max_coords=300, lado_min=40.0)
        li, ci = STRtree(trechos).query(geoms[idx], predicate="intersects")
        if len(li):
            miolo = shapely.buffer(geoms[idx][li], -0.3)      # ignora linha só encostada na divisa
            comp = shapely.length(shapely.intersection(miolo, trechos[ci]))
            soma = np.zeros(len(idx))
            np.add.at(soma, li, comp)
            achados.update(idx[np.flatnonzero(soma >= METRO_SOB_MIN)].tolist())
    if len(m["pontos"]):
        li, _ = STRtree(m["pontos"]).query(geoms[idx], predicate="contains")
        achados.update(idx[np.unique(li)].tolist())
    return achados


def metro_perto(geoms, idx, m, dist):
    """Índices dos lotes de idx a até dist metros de uma linha ou estação."""
    achados = set()
    if len(m["linhas"]):
        trechos, _ = subdividir(m["linhas"], max_coords=300, lado_min=40.0)
        li, _ = STRtree(trechos).query(geoms[idx], predicate="dwithin", distance=dist)
        achados.update(idx[np.unique(li)].tolist())
    if len(m["pontos"]):
        li, _ = STRtree(m["pontos"]).query(geoms[idx], predicate="dwithin", distance=dist)
        achados.update(idx[np.unique(li)].tolist())
    return achados


def dissolver(geoms, tol=2.5):
    """Junta polígonos vizinhos e simplifica: bom para desenhar, não para medir."""
    u = shapely.union_all(shapely.buffer(geoms, 0.3)).buffer(-0.3).simplify(tol)
    return np.array(list(u.geoms) if hasattr(u, "geoms") else [u], dtype=object)


def camadas_de_linhas(gravar, feicoes):
    """Linhas de metrô/trem, estações projetadas e córregos, para ligar e desligar no mapa."""
    # ---- metrô e trem (linhas atuais e projetadas)
    pares = []
    for nome, tipo, proj in (("linha_metro", "metro", 0), ("linha_trem", "trem", 0),
                             ("linha_metro_projetada", "metro", 1), ("linha_trem_projetada", "trem", 1)):
        g, pr = carregar(nome + ".json")
        for x, p in zip(g, pr):
            linha = (p.get("nm_linha_metro_trem") or "").strip()
            rot = (p.get("nr_nome_linha") or "").strip().title() or f"Linha {linha.title()}"
            if tipo == "trem" and (p.get("nm_empresa_metro_trem") or "").strip():
                rot += f" ({p['nm_empresa_metro_trem'].strip()})"
            pares.append((x.simplify(3.0), {"n": rot, "t": tipo, "p": proj,
                                            "c": COR_LINHA.get(linha.upper(), "#6b7280")}))
    gravar("metro.geojson.gz", {"type": "FeatureCollection", "features": feicoes(pares)})
    est = []
    for nome, tipo in (("estacao_metro_projetada", "Metrô"), ("estacao_trem_projetada", "Trem")):
        g, pr = carregar(nome + ".json")
        for x, p in zip(g, pr):
            est.append((x, {"nome": f"{tipo} {(p.get('nm_estacao_metro_trem') or '').title()} (projetada)"}))
    gravar("estacoes_proj.geojson", {"type": "FeatureCollection", "features": feicoes(est)})

    # ---- córregos (camada "Drenagem"): natural, canalizado a céu aberto e canalizado subterrâneo
    tipo_curso = {"Trecho em estado natural": 0, "Trecho canalizado a céu aberto": 1, "Trecho canalizado subterrâneo": 2}
    g, pr = carregar("drenagem.json")
    cor = []
    for x, p in zip(g, pr):
        t = tipo_curso.get((p.get("nm_tipo_curso_hidrografia") or "").strip())
        if t is None:                              # lagos e reservatórios não entram
            continue
        nome = " ".join(NOME_CORREGO.get(w, w.capitalize()) for w in (p.get("nm_acidente") or "").split())
        cor.append((x.simplify(2.0), {"n": nome, "t": t}))
    gravar("corregos.geojson.gz", {"type": "FeatureCollection", "features": feicoes(cor)})


def nome_arquivo(zona):
    return re.sub(r"[^A-Za-z0-9_-]", "_", zona)


def faixa(area):
    return 3 if area >= FAIXAS[2] else 2 if area >= FAIXAS[1] else 1 if area >= FAIXAS[0] else 0


def main():
    # ------------------------------------------------------------- lotes
    log("Lendo lotes…")
    props, geoms = [], []
    with gzip.open(os.path.join(BRUTOS, "lotes.ndjson.gz"), "rt") as fh:
        for linha in fh:
            f = json.loads(linha)
            c = f["geometry"]["coordinates"]
            if f["geometry"]["type"] != "Polygon" or len(c[0]) < 4:
                continue
            geoms.append(shapely.Polygon(c[0], c[1:]))
            props.append(f["properties"])
    geoms = so_poligonos(np.array(geoms))
    n_total = len(geoms)
    area_geo = shapely.area(geoms)
    log(f"  {n_total} lotes lidos")

    # ------------------------------------------- filtro cadastral (casas)
    ok = np.zeros(n_total, bool)
    motivo = collections.Counter()
    via = np.array([p["cd_tipo_lote"] == "V" for p in props])   # passagem de vila
    with open(os.path.join(BRUTOS, "predios.json")) as fh:
        vol = json.load(fh)
    predio = np.zeros(n_total, bool)          # lote em condomínio com volume estimado
    nivel = np.zeros(n_total, np.int32)       # posição da régua em que o lote entra (0 = sempre)
    ac_est = np.zeros(n_total)                # área construída estimada dos prédios
    pav_est = np.zeros(n_total, np.int32)
    alt_est = np.zeros(n_total)
    for i, p in enumerate(props):
        if via[i]:
            ok[i] = area_geo[i] >= 20
        elif p["cd_tipo_lote"] != "F":
            motivo["não fiscal (municipal)"] += 1
        elif p["cd_condominio"] != "00" or p["dc_tipo_uso_imovel"] == "Condomínio":
            e = vol["predios"].get(str(p["cd_identificador"]))
            if not e or e["ac"] <= 0 or area_geo[i] < 20:
                motivo["condomínio sem volume no levantamento"] += 1
            elif e["ac"] / area_geo[i] > CA_MAX_PREDIO:
                motivo["prédio (CA estimado > %g)" % CA_MAX_PREDIO] += 1
            else:
                ok[i] = predio[i] = True
                ac_est[i], pav_est[i], alt_est[i] = e["ac"], e["pav"], e["h"]
                nivel[i] = max(1, math.ceil(e["ac"] / area_geo[i] / PASSO_CA - 1e-9))
        elif area_geo[i] < 20:
            motivo["geometria ínfima"] += 1
        else:
            at = p["qt_area_terreno"] or area_geo[i]
            ac = p["qt_area_construida"] or 0
            num = (p["cd_numero_porta"] or "").strip().upper()
            sem_num = num in SEM_NUMERO or (num.isdigit() and int(num) == 0)
            if ac / max(at, 1) > CA_MAX_CASA or ac / max(area_geo[i], 1) > CA_MAX_CASA * 1.5:
                motivo["prédio (CA > %g)" % CA_MAX_CASA] += 1
            elif ac == 0 and sem_num and max(at, area_geo[i]) >= LANCAMENTO_AREA_MIN:
                motivo["provável lançamento (terreno grande sem número)"] += 1
            else:
                ok[i] = True

    # --------------------------------- equipamentos (hospital, escola, polícia)
    log("Cruzando com equipamentos…")
    arv_lotes = STRtree(geoms)
    por_sql = {}
    for i, p in enumerate(props):
        if p["cd_tipo_lote"] == "F" and p["cd_condominio"] == "00":
            por_sql[(p["cd_setor_fiscal"], p["cd_quadra_fiscal"], p["cd_lote"])] = i
    for arq, (rotulo, filtro) in EQUIPAMENTOS.items():
        eg, ep = carregar(arq + ".json")
        if filtro:
            m = [bool(filtro(p)) for p in ep]
            eg, ep = eg[m], [p for p, v in zip(ep, m) if v]
        achados = set()
        if arq in SQL_DO_REGISTRO:
            for p in ep:
                k = por_sql.get(SQL_DO_REGISTRO[arq](p))
                if k is not None:
                    achados.add(k)
        for x in eg:
            if x.geom_type == "Point":
                dentro = arv_lotes.query(x, predicate="intersects")
                if not len(dentro):                      # ponto na calçada/rua: lote mais próximo
                    k = arv_lotes.nearest(x)
                    dentro = [k] if geoms[k].distance(x) <= EQUIP_DIST_MAX else []
                achados.update(int(k) for k in dentro)
            else:
                for k in arv_lotes.query(x, predicate="intersects"):
                    if geoms[k].intersection(x).area >= 0.5 * area_geo[k]:
                        achados.add(int(k))
        n_eq = 0
        for k in achados:
            if ok[k] and not via[k]:
                ok[k] = False
                motivo[rotulo] += 1
                n_eq += 1
        log(f"  {arq}: {len(eg)} equipamentos -> {len(achados)} lotes ({n_eq} eram elegíveis)")

    # ------------------------------------------------------------- zonas
    log("Cruzando com zoneamento…")
    zg, zp = carregar("zonas.json")
    zcod = np.array([p["cd_zoneamento_perimetro"] for p in zp], dtype=object)
    znome = {}
    for p in zp:
        if p["tx_zoneamento_perimetro"]:
            znome.setdefault(p["cd_zoneamento_perimetro"], p["tx_zoneamento_perimetro"].strip())
    idx = np.flatnonzero(ok)
    li, zi = STRtree(zg).query(geoms[idx], predicate="intersects")
    inter = shapely.area(shapely.intersection(geoms[idx][li], zg[zi]))
    por_lote = collections.defaultdict(collections.Counter)
    for a, b, c in zip(li, zi, inter):
        por_lote[idx[a]][zcod[b]] += c
    zona = np.full(n_total, "", dtype=object)
    for i in idx:
        cz = por_lote.get(i)
        z = cz.most_common(1)[0][0] if cz else ""
        if not z or z in ZONAS_FORA or sum(cz.values()) < 0.5 * area_geo[i]:
            ok[i] = False
            motivo["sem zona de uso"] += 1
        else:
            zona[i] = z
    del por_lote

    # ---------------------------------------------------------- tombados
    log("Cruzando com tombamento e restrições…")
    tg, tp = carregar("tombados.json")
    sql_tomb = {(p["cd_setor_fiscal"], p["cd_quadra_fiscal"], p["cd_lote"])
                for p in tp if p["cd_lote"] and p["cd_lote"].strip("0 ")}
    idx = np.flatnonzero(ok)
    li, ti = STRtree(tg).query(geoms[idx], predicate="intersects")
    inter = shapely.area(shapely.intersection(geoms[idx][li], tg[ti]))
    sob = np.zeros(n_total)
    np.add.at(sob, idx[li], inter)
    for i in idx:
        p = props[i]
        if sob[i] / area_geo[i] >= FRACAO_TOMBADO or \
                (p["cd_setor_fiscal"], p["cd_quadra_fiscal"], p["cd_lote"]) in sql_tomb:
            ok[i] = False
            motivo["tombado"] += 1

    # ------------------------------------------- restrições que excluem
    camadas_restr = {}
    for camada, rotulo in RESTRICOES.items():
        cg, cprops = carregar(camada + ".json")
        if camada == "cap":                      # só áreas públicas com cadastro ativo
            cg = cg[[p.get("dc_tipo_status_projeto_cap") == "Ativo" for p in cprops]]
        camadas_restr[camada] = cg
        idx = np.flatnonzero(ok)
        for i in atingidos(geoms, idx, cg):
            ok[i] = False
            motivo[rotulo] += 1

    for camada, rotulo in RESTRICOES_DENTRO.items():
        cg, cprops = carregar(camada + ".json")
        if camada == "operacao_urbana":          # só o perímetro principal de cada operação
            cg = cg[["expandido" not in (p.get("nm_operacao_urbana") or "").lower() for p in cprops]]
        camadas_restr[camada] = cg
        pedacos, _ = subdividir(cg)
        idx = np.flatnonzero(ok)
        li, ci = STRtree(pedacos).query(geoms[idx], predicate="intersects")
        coberta = np.zeros(len(idx))
        np.add.at(coberta, li, shapely.area(shapely.intersection(geoms[idx][li], pedacos[ci])))
        for l in np.flatnonzero(coberta >= 0.5 * area_geo[idx]):
            ok[idx[l]] = False
            motivo[rotulo] += 1

    # ---------------------------------- metrô e trem sob o lote (excluem)
    metro = metro_geoms()
    for cod in ("mt", "mf"):
        idx = np.flatnonzero(ok)
        for i in metro_sob(geoms, idx, metro[cod]):
            ok[i] = False
            motivo[f"{METRO_ROTULO[cod]} sob o lote"] += 1
    camadas_restr["metro_mt"] = shapely.buffer(metro["mt"]["linhas"], 3.0)
    camadas_restr["metro_mf"] = shapely.buffer(metro["mf"]["linhas"], 3.0)

    # ------------------------------------------ duplicatas sobrepostas
    idx = np.flatnonzero(ok)
    g = geoms[idx]
    a, b = STRtree(g).query(g, predicate="intersects")
    m = a < b
    a, b = a[m], b[m]
    inter = shapely.area(shapely.intersection(g[a], g[b]))
    menor = np.minimum(area_geo[idx][a], area_geo[idx][b])
    for x, y in zip(a[inter > 0.5 * menor], b[inter > 0.5 * menor]):
        ix, iy = idx[x], idx[y]
        if not (ok[ix] and ok[iy]):
            continue
        chave = lambda i: (props[i]["tx_situ_lote"] == "ATIVO", area_geo[i])
        ok[ix if chave(ix) < chave(iy) else iy] = False
        motivo["duplicado/sobreposto"] += 1
    log("  descartes:", dict(motivo))
    log(f"  lotes elegíveis: {ok.sum()}")

    # ------------------------------------------------- alertas (não excluem)
    log("Cruzando com alertas…")
    alerta = collections.defaultdict(list)      # índice do lote -> [códigos]
    detalhe_cont = {}
    camadas_alerta = {}
    idx = np.flatnonzero(ok)
    for camada, cod in ALERTAS.items():
        cg, cp = carregar(camada + ".json")
        camadas_alerta[cod] = cg
        hits = atingidos(geoms, idx, cg)
        for i, k in hits.items():
            alerta[i].append(cod)
            if cod == "cs":
                detalhe_cont[i] = (cp[k].get("dc_classificacao_area_contaminada") or "").strip()
            elif cod == "cp" and i not in detalhe_cont:
                detalhe_cont[i] = (cp[k].get("dc_tipo_situacao") or "").strip().capitalize()
        log(f"  {camada}: {len(hits)} lotes")

    if "fn" in camadas_alerta:                  # a medição usa os 51 mil polígonos; o desenho, a versão dissolvida
        camadas_alerta["fn"] = dissolver(camadas_alerta["fn"])
    for cod in ("mt", "mf"):                    # metrô/trem a até METRO_DIST_ALERTA m
        idx = np.flatnonzero(ok)
        hits = metro_perto(geoms, idx, metro[cod], METRO_DIST_ALERTA)
        for i in hits:
            alerta[i].append(cod)
        log(f"  {METRO_ROTULO[cod]} a até {METRO_DIST_ALERTA:.0f} m: {len(hits)} lotes")
        zona_m = shapely.union_all(np.concatenate([shapely.buffer(metro[cod]["linhas"], METRO_DIST_ALERTA),
                                                   shapely.buffer(metro[cod]["pontos"], METRO_DIST_ALERTA)]))
        camadas_alerta[cod] = np.array([zona_m], dtype=object)

    # ------------------------------------------------------- contiguidade
    log("Calculando contiguidade…")
    idx = np.flatnonzero(ok)
    g = geoms[idx]
    zl = zona[idx]
    buf = shapely.buffer(g, FOLGA, join_style="mitre")
    a, b = STRtree(buf).query(buf, predicate="intersects")
    m = (a < b) & (zl[a] == zl[b])               # só une lotes do mesmo zoneamento
    a, b = a[m], b[m]
    divisa = shapely.area(shapely.intersection(buf[a], buf[b])) / (2 * FOLGA)
    m = divisa >= DIVISA_MIN
    a, b = a[m], b[m]
    # Manchas em cada posição da régua de prédios. Os componentes só crescem
    # conforme a régua sobe, então basta ir acrescentando as divisas (union-find
    # incremental) e registrar, para cada mancha distinta, a faixa em que existe.
    niv = nivel[idx]
    via_l = via[idx]
    area_l = area_geo[idx]
    nivel_divisa = np.maximum(niv[a], niv[b])
    n_niveis = int(round(CA_MAX_PREDIO / PASSO_CA))
    pai = np.arange(len(idx))

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    unicas = {}                                   # membros -> [nível inicial, nível final, membros]
    for L in range(n_niveis + 1):
        m = nivel_divisa == L
        for x, y in zip(a[m], b[m]):
            rx, ry = raiz(x), raiz(y)
            if rx != ry:
                pai[ry] = rx
        ativos = np.flatnonzero(niv <= L)
        r = np.fromiter((raiz(k) for k in ativos), dtype=np.int64, count=len(ativos))
        soma = np.bincount(r, weights=area_l[ativos], minlength=len(idx))
        bons = soma[r] >= AREA_MIN
        ordem = np.argsort(r[bons], kind="stable")
        rk, ak = r[bons][ordem], ativos[bons][ordem]
        n_L = 0
        for v in np.split(ak, np.flatnonzero(np.diff(rk)) + 1) if len(ak) else []:
            if via_l[v].all():
                continue
            n_L += 1
            chave = v.tobytes()
            if chave in unicas:
                unicas[chave][1] = L
            else:
                unicas[chave] = [L, L, v]
        log(f"  régua {L * PASSO_CA:.1f}: {n_L} manchas")
    grupos = list(unicas.values())
    log(f"  {len(grupos)} manchas distintas com >= {AREA_MIN:.0f} m²")

    # ------------------------------------------------- vilas (passagens "V")
    # Vila "completa": toda casa que encosta na passagem está na mesma mancha
    # (se sobra alguma de fora — tombada, prédio, outra zona — a vila é "parcial").
    vias_ok = np.flatnonzero(via & ok)
    vizinhas_via = collections.defaultdict(list)
    if len(vias_ok):
        bv = shapely.buffer(geoms[vias_ok], FOLGA, join_style="mitre")
        va, vb = STRtree(geoms).query(bv, predicate="intersects")
        toque = shapely.area(shapely.intersection(
            bv[va], shapely.buffer(geoms[vb], FOLGA, join_style="mitre"))) / (2 * FOLGA)
        for x, y, t in zip(va, vb, toque):
            i = vias_ok[x]
            # condomínios encostados na passagem têm frente própria para a rua: não contam
            if y == i or t < 1.5 or props[y]["cd_tipo_lote"] != "F" \
                    or props[y]["cd_condominio"] != "00":
                continue
            vizinhas_via[i].append(int(y))

    # ------------------------------------------------------ apoio espacial
    dg, dp = carregar("distrito_municipal.json")
    arv_dist = STRtree(dg)
    est_g, est_n = [], []
    for nome, tipo in (("estacao_metro", "Metrô"), ("estacao_trem", "Trem")):
        gg, pp = carregar(nome + ".json")
        for x, p in zip(gg, pp):
            if (p.get("tx_situacao_metro_trem") or "OPERANDO").upper().startswith("OPER"):
                est_g.append(x)
                est_n.append(f"{tipo} {p['nm_estacao_metro_trem'].title()}")
    arv_est = STRtree(np.array(est_g))

    # ------------------------------------------------------------ manchas
    log("Montando manchas…")
    manchas = collections.defaultdict(list)                 # zona -> feições
    lotes_idx = collections.defaultdict(dict)               # (zona, célula) -> {lote: [ids das manchas]}
    degeneradas = 0
    for nivel_a, nivel_b, membros in grupos:
        area = float(area_geo[idx[membros]].sum())
        try:
            u = shapely.union_all(buf[membros]).buffer(-FOLGA, join_style="mitre")
            u = maior_poligono(u)
            if u is not None and not u.is_empty:
                u = shapely.Polygon(u.exterior, [h for h in u.interiors if shapely.Polygon(h).area >= 20])
                if not u.is_valid:
                    u = maior_poligono(shapely.make_valid(u)) or u
            # desenho que não fecha (lotes sobrepostos ou inválidos na origem): descarta
            if u is None or u.is_empty or u.area < 0.5 * area:
                degeneradas += 1
                continue
            nota, det = nota_forma(u)
        except shapely.errors.GEOSException:
            degeneradas += 1
            continue
        fator = min(1.0, nota / FORMA_SEM_PENALIDADE)
        area_eq = area * fator

        ms = sorted(membros.tolist(), key=lambda k: (via[idx[k]], predio[idx[k]],
                                                     props[idx[k]]["cd_setor_fiscal"],
                                                     props[idx[k]]["cd_quadra_fiscal"],
                                                     props[idx[k]]["cd_lote"],
                                                     props[idx[k]]["cd_condominio"]))
        p0 = props[idx[ms[0]]]          # primeiro lote comum (prédios e vias ficam no fim)
        mid = f"{p0['cd_setor_fiscal']}-{p0['cd_quadra_fiscal']}-" + \
            (f"CD{p0['cd_condominio']}" if predio[idx[ms[0]]] else p0["cd_lote"])
        if nivel_a > 0:                  # mancha que só existe a partir de certo ponto da régua
            mid += f".{nivel_a}"
        z = zl[ms[0]]
        vias_m = [idx[k] for k in ms if via[idx[k]]]
        n_predios = sum(1 for k in ms if predio[idx[k]])
        dentro = set(idx[ms].tolist())
        vila = "" if not vias_m else \
            "completa" if all(y in dentro for i in vias_m for y in vizinhas_via.get(i, ())) else "parcial"
        usos = collections.Counter()
        ruas = collections.Counter()
        avisos = collections.Counter()
        at_iptu = ac_tot = a_nres = 0.0
        for k in ms:
            i = idx[k]
            p = props[i]
            for cod in alerta.get(i, ()):
                avisos[cod] += 1
            if via[i]:
                usos["Passagem de vila"] += 1
                at_iptu += area_geo[i]
                continue
            if predio[i]:
                usos["Prédio"] += 1
                at_iptu += p["qt_area_terreno"] or area_geo[i]
                ac_tot += ac_est[i]
                if p["nm_logradouro_completo"]:
                    ruas[p["nm_logradouro_completo"].strip()] += 1
                continue
            usos[p["dc_tipo_uso_imovel"] or "Sem informação"] += 1
            if p["dc_tipo_uso_imovel"] == "Não residencial":
                a_nres += area_geo[i]
            if p["nm_logradouro_completo"]:
                ruas[p["nm_logradouro_completo"].strip()] += 1
            at_iptu += p["qt_area_terreno"] or area_geo[i]
            ac_tot += p["qt_area_construida"] or 0

        rp = u.representative_point()
        lon, lat = para_wgs.transform(rp.x, rp.y)
        dk = arv_dist.query(rp, predicate="intersects")
        distrito = dp[dk[0]]["nm_distrito_municipal"].title() if len(dk) else ""
        ne = arv_est.nearest(rp)
        cel = f"{math.floor(lon / CELULA_LOTES)}_{math.floor(lat / CELULA_LOTES)}"
        pr = {
            "id": mid, "zona": z, "area": round(area), "area_iptu": round(at_iptu),
            "n": len(ms) - len(vias_m), "np": n_predios, "vila": vila,
            "na": nivel_a * PASSO_CA, "nb": nivel_b * PASSO_CA, "forma": round(nota * 100),
            "ret": round(det["ret"] * 100), "comp": round(det["comp"] * 100),
            "nv": det["nv"], "miolo": round(det["miolo"] * 100),
            "area_eq": round(area_eq), "faixa": faixa(area_eq), "faixa_area": faixa(area),
            "distrito": distrito, "ruas": " · ".join(r for r, _ in ruas.most_common(4)),
            "pnr": round(100 * a_nres / area),
            "ac": round(ac_tot), "ca": round(ac_tot / max(at_iptu, 1), 2),
            "usos": " · ".join(f"{v} {k.lower()}" for k, v in usos.most_common()),
            "est": est_n[ne], "est_m": round(float(est_g[ne].distance(rp))),
            "lon": round(lon, 6), "lat": round(lat, 6), "cel": cel}
        quadras = sorted({f"{props[idx[k]]['cd_setor_fiscal']}.{props[idx[k]]['cd_quadra_fiscal']}" for k in ms})
        if len(quadras) > 1:                     # mancha que cruza quadras (1,7%): lista todas
            q0 = f"{p0['cd_setor_fiscal']}.{p0['cd_quadra_fiscal']}"
            pr["sq"] = " · ".join([q0] + [q for q in quadras if q != q0])
        for cod, v in avisos.items():
            pr["a_" + cod] = v                   # nº de lotes com cada alerta
        manchas[z].append({"type": "Feature", "geometry": wgs(u.simplify(0.4)), "properties": pr})
        for k in ms:
            lotes_idx[(z, cel)].setdefault(k, []).append(mid)

    if degeneradas:
        log(f"  {degeneradas} manchas descartadas por geometria degenerada")

    # ------------------------------------------------------------- gravação
    def gravar(nome, obj):
        """Grava JSON compacto; nomes terminados em .gz saem comprimidos
        (o navegador descomprime com DecompressionStream)."""
        txt = obj if isinstance(obj, bytes) else \
            json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        caminho = os.path.join(SAIDA, nome)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        if nome.endswith(".gz"):
            with open(caminho, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
                gz.write(txt)
        else:
            with open(caminho, "wb") as fh:
                fh.write(txt)

    shutil.rmtree(SAIDA, ignore_errors=True)
    os.makedirs(SAIDA)
    log("Gravando manchas e lotes…")
    for z, feats in manchas.items():
        feats.sort(key=lambda f: -f["properties"]["area_eq"])
        gravar(f"manchas/{nome_arquivo(z)}.geojson.gz", {"type": "FeatureCollection", "features": feats})

    # lotes: converte tudo de uma vez (vetorizado) e escreve o GeoJSON como texto
    todos = sorted({k for lst in lotes_idx.values() for k in lst})
    gl = shapely.transform(g[todos], lambda xy: np.column_stack(para_wgs.transform(xy[:, 0], xy[:, 1])))
    gl = shapely.set_precision(gl, 1e-6, mode="pointwise")
    gj = dict(zip(todos, shapely.to_geojson(gl)))
    n_lotes = 0
    for (z, cel), lst in lotes_idx.items():
        partes = []
        for k, mids in lst.items():
            i = idx[k]
            p = props[i]
            num = (p["cd_numero_porta"] or "").strip()
            # "m" lista as manchas do lote: ele muda de mancha conforme a régua de prédios
            pr = {"m": ",".join(mids),
                  "sql": (f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.V{p['cd_lote']}" if via[i] else
                          f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.CD{p['cd_condominio']}" if predio[i] else
                          f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.{p['cd_lote']}-{p['cd_digito_sql'] or ''}"),
                  "end": "Passagem de vila" if via[i] else
                         ((p["nm_logradouro_completo"] or "").strip() + (", " + num if num else "")).strip(", "),
                  "at": p["qt_area_terreno"] or 0, "ag": round(float(area_geo[i])),
                  "ac": round(float(ac_est[i])) if predio[i] else (p["qt_area_construida"] or 0),
                  "uso": "Via de acesso" if via[i] else "Prédio" if predio[i] else (p["dc_tipo_uso_imovel"] or "")}
            if via[i]:
                pr["v"] = 1
            if predio[i]:                       # área construída é estimativa pela volumetria
                pr["p"] = 1
                pr["pav"] = int(pav_est[i])
                pr["h"] = float(alt_est[i])
            if i in alerta:
                pr["al"] = ",".join(alerta[i])
                if i in detalhe_cont:
                    pr["cont"] = detalhe_cont[i]
            partes.append('{"type":"Feature","geometry":%s,"properties":%s}'
                          % (gj[k], json.dumps(pr, ensure_ascii=False, separators=(",", ":"))))
            n_lotes += 1
        gravar(f"lotes/{nome_arquivo(z)}/{cel}.geojson.gz",
               ('{"type":"FeatureCollection","features":[' + ",".join(partes) + "]}").encode())

    # ---------------------------------------------------- camadas de apoio
    ROTULO_RESTR = {**RESTRICOES, **RESTRICOES_DENTRO,
                    "metro_mt": "metrô/trem (linha sob o lote)",
                    "metro_mf": "metrô/trem projetado (linha sob o lote)"}

    def feicoes(pares):
        """[(geometria, propriedades)] -> feições GeoJSON, pulando geometrias vazias."""
        saida = []
        for x, pr in pares:
            gj = wgs(x, 5)
            if gj and gj.get("coordinates"):
                saida.append({"type": "Feature", "geometry": gj, "properties": pr})
        return saida
    log("Camadas de apoio…")
    for cod in sorted(set(zcod) - ZONAS_FORA):
        u = shapely.union_all(shapely.buffer(zg[zcod == cod], 0.5)).buffer(-0.5).simplify(1.5)
        gravar(f"zonas/{nome_arquivo(cod)}.geojson.gz",
               {"type": "FeatureCollection", "features": feicoes([(u, {"zona": cod})])})
    gravar("tombados.geojson.gz", {"type": "FeatureCollection", "features": feicoes(
        (x.simplify(0.5), {"nome": (p["nm_area_tombada"] or "").strip(),
                           "sit": (p["tx_situacao_tombamento"] or "").strip()})
        for x, p in zip(tg, tp))})
    gravar("restricoes.geojson.gz", {"type": "FeatureCollection", "features": feicoes(
        (x.simplify(1.0), {"t": ROTULO_RESTR[camada]})
        for camada, cg in camadas_restr.items() for x in cg)})
    gravar("alertas.geojson.gz", {"type": "FeatureCollection", "features": feicoes(
        (x.simplify({"g1": 5.0, "mt": 3.0, "mf": 3.0, "fn": 0.0}.get(cod, 1.5)), {"t": cod})
        for cod, cg in camadas_alerta.items() for x in cg)})
    gravar("estacoes.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": wgs(x), "properties": {"nome": n}}
        for x, n in zip(est_g, est_n)]})
    camadas_de_linhas(gravar, feicoes)

    zonas_meta = []
    for cod in sorted(manchas, key=lambda c: (c not in ZONAS_PADRAO, c)):
        fx = collections.Counter(f["properties"]["faixa"] for f in manchas[cod])
        zonas_meta.append({"cod": cod, "arq": nome_arquivo(cod), "nome": znome.get(cod, ""),
                           "manchas": sum(1 for f in manchas[cod]
                                          if f["properties"]["nb"] >= CA_MAX_PREDIO),
                           "celulas": sorted({c for (z, c) in lotes_idx if z == cod})})
    total = sum(len(v) for v in manchas.values())
    gravar("meta.json", {
        "gerado_em": datetime.date.today().isoformat(),
        "fonte": "GeoSampa (lote_cidadao, perímetro de zonas Lei 18.177/24, patrimônio cultural, "
                 "áreas públicas, parcelamento, melhoramento viário, DUP/DIS, carta geotécnica 2024, "
                 "áreas contaminadas)",
        "lotes_analisados": int(n_total), "lotes_elegiveis": int(ok.sum()),
        "manchas": total, "zonas": zonas_meta, "zonas_padrao": ZONAS_PADRAO,
        "regua_predios": dict(maximo=CA_MAX_PREDIO, passo=PASSO_CA, padrao=CA_MAX_PREDIO,
                              altura_pavimento=vol["altura_pavimento"]),
        "descartes": dict(motivo),
        "parametros": dict(ca_max_casa=CA_MAX_CASA, area_min=AREA_MIN, faixas=FAIXAS,
                           divisa_min=DIVISA_MIN, raio_miolo=RAIO_MIOLO, pesos=PESOS,
                           vert_ideal=VERT_IDEAL, vert_ruim=VERT_RUIM,
                           forma_sem_penalidade=FORMA_SEM_PENALIDADE,
                           celula_lotes=CELULA_LOTES)})
    log(f"Pronto: {total} manchas em {len(manchas)} zonas, {n_lotes} lotes em {len(lotes_idx)} arquivos")
    for zm in zonas_meta:
        log(f"   {zm['cod']:10s} {zm['manchas']:6d} manchas")


if __name__ == "__main__":
    main()
