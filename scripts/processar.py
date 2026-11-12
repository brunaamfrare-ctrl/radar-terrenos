#!/usr/bin/env python3
"""Processa os dados brutos do GeoSampa e gera os arquivos do mapa (docs/dados/).

Regras (premissas de novos negócios):
  1. Lote elegível = lote fiscal, fora de condomínio ("CD"), com
     área construída / área do terreno <= CA_MAX_CASA, em zona-alvo (ZEU/ZC),
     não tombado.
  2. Lotes elegíveis contíguos (divisa comum >= DIVISA_MIN m) formam uma "mancha".
     Passagens de vila (lotes "V" — via de acesso) entram como elo: unem as casas
     dos dois lados e somam área, mas não contam como lote a negociar.
  3. Mancha entra no mapa se área somada >= AREA_MIN.
  4. Nota de forma (0-100) combina retangularidade, compacidade
     (perímetro x área), nº de vértices e miolo aproveitável.
  5. Área equivalente = área x fator de forma -> faixa de cor.

Uso:  python3 scripts/processar.py
"""
import collections
import datetime
import gzip
import json
import math
import os
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
FRACAO_ZONA_MIN = 0.5    # fração mínima do lote dentro de zona-alvo
FRACAO_TOMBADO = 0.3     # sobreposição com bem tombado que descarta o lote
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

FAMILIA = {"ZEU": "ZEU", "ZEUa": "ZEU", "ZEUP": "ZEU", "ZEUPa": "ZEU",
           "ZC": "ZC", "ZCa": "ZC", "ZC-ZEIS": "ZC"}

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
    gj = json.loads(shapely.to_geojson(g))

    def arred(c):
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
    comp = min(1.0, 4.0 * math.sqrt(a) / s.length)      # 1 = quadrado
    sv = u.simplify(TOL_VERTICES)
    nv = len(sv.exterior.coords) - 1 + sum(len(i.coords) - 1 for i in sv.interiors)
    vert = min(1.0, max(0.0, 1.0 - (nv - VERT_IDEAL) / (VERT_RUIM - VERT_IDEAL)))
    miolo_g = u.buffer(-RAIO_MIOLO, join_style="mitre").buffer(RAIO_MIOLO, join_style="mitre")
    miolo = min(1.0, miolo_g.intersection(u).area / a)
    nota = (PESOS["ret"] * ret + PESOS["comp"] * comp
            + PESOS["vert"] * vert + PESOS["miolo"] * miolo)
    return nota, dict(ret=ret, comp=comp, nv=nv, miolo=miolo)


def faixa(area):
    return 3 if area >= FAIXAS[2] else 2 if area >= FAIXAS[1] else 1 if area >= FAIXAS[0] else 0


def main():
    os.makedirs(os.path.join(SAIDA, "lotes"), exist_ok=True)

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
    for i, p in enumerate(props):
        if via[i]:
            ok[i] = area_geo[i] >= 20
        elif p["cd_tipo_lote"] != "F":
            motivo["não fiscal (municipal)"] += 1
        elif p["cd_condominio"] != "00" or p["dc_tipo_uso_imovel"] == "Condomínio":
            motivo["condomínio (CD)"] += 1
        elif area_geo[i] < 20:
            motivo["geometria ínfima"] += 1
        else:
            at = p["qt_area_terreno"] or area_geo[i]
            ac = p["qt_area_construida"] or 0
            if ac / max(at, 1) > CA_MAX_CASA or ac / max(area_geo[i], 1) > CA_MAX_CASA * 1.5:
                motivo["prédio (CA > %g)" % CA_MAX_CASA] += 1
            else:
                ok[i] = True
    log("  descartados no cadastro:", dict(motivo))

    # ------------------------------------------------------------- zonas
    log("Cruzando com zoneamento…")
    zg, zp = carregar("zonas.json")
    zcod = np.array([p["cd_zoneamento_perimetro"] for p in zp])
    idx = np.flatnonzero(ok)
    li, zi = STRtree(zg).query(geoms[idx], predicate="intersects")
    inter = shapely.area(shapely.intersection(geoms[idx][li], zg[zi]))
    por_lote = collections.defaultdict(lambda: collections.Counter())
    for a, b, c in zip(li, zi, inter):
        por_lote[idx[a]][zcod[b]] += c
    zona = np.full(n_total, "", dtype=object)
    for i in idx:
        cz = por_lote.get(i)
        if not cz or sum(cz.values()) / area_geo[i] < FRACAO_ZONA_MIN:
            ok[i] = False
            motivo["fora de ZEU/ZC"] += 1
        else:
            zona[i] = cz.most_common(1)[0][0]

    # ---------------------------------------------------------- tombados
    log("Cruzando com tombamento…")
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
        # mantém o ativo; em empate, o de maior área
        chave = lambda i: (props[i]["tx_situ_lote"] == "ATIVO", area_geo[i])
        ok[ix if chave(ix) < chave(iy) else iy] = False
        motivo["duplicado/sobreposto"] += 1
    log("  descartes:", dict(motivo))
    log(f"  lotes elegíveis: {ok.sum()}")

    # ------------------------------------------------------- contiguidade
    log("Calculando contiguidade…")
    idx = np.flatnonzero(ok)
    g = geoms[idx]
    buf = shapely.buffer(g, FOLGA, join_style="mitre")
    a, b = STRtree(buf).query(buf, predicate="intersects")
    m = a < b
    a, b = a[m], b[m]
    divisa = shapely.area(shapely.intersection(buf[a], buf[b])) / (2 * FOLGA)
    m = divisa >= DIVISA_MIN
    a, b = a[m], b[m]
    pai = np.arange(len(idx))

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    for x, y in zip(a, b):
        rx, ry = raiz(x), raiz(y)
        if rx != ry:
            pai[ry] = rx
    grupos = collections.defaultdict(list)
    for k in range(len(idx)):
        grupos[raiz(k)].append(k)
    grupos = [v for v in grupos.values()
              if area_geo[idx[v]].sum() >= AREA_MIN and not via[idx[v]].all()]
    log(f"  {len(grupos)} manchas com >= {AREA_MIN:.0f} m²")

    # ------------------------------------------------- vilas (passagens "V")
    # Vila "completa": toda casa que encosta na passagem está na mesma mancha
    # (se sobra alguma de fora — tombada, prédio, outra zona — a vila é "parcial").
    grupo_de = {}
    for n_g, membros in enumerate(grupos):
        for k in membros:
            grupo_de[idx[k]] = n_g
    vias_ok = np.array([i for i in np.flatnonzero(via) if i in grupo_de], dtype=int)
    via_completa = {}
    if len(vias_ok):
        bv = shapely.buffer(geoms[vias_ok], FOLGA, join_style="mitre")
        va, vb = STRtree(geoms).query(bv, predicate="intersects")
        toque = shapely.area(shapely.intersection(
            bv[va], shapely.buffer(geoms[vb], FOLGA, join_style="mitre"))) / (2 * FOLGA)
        for x, y, t in zip(va, vb, toque):
            i = vias_ok[x]
            via_completa.setdefault(i, True)
            # condomínios encostados na passagem têm frente própria para a rua: não contam
            if y == i or t < 1.5 or props[y]["cd_tipo_lote"] != "F" \
                    or props[y]["cd_condominio"] != "00":
                continue
            if grupo_de.get(y) != grupo_de[i]:
                via_completa[i] = False
    log(f"  {len(vias_ok)} passagens de vila dentro de manchas")

    # ------------------------------------------------------ apoio espacial
    eg_all, env_nome = [], []
    for org in ("CONPRESP", "CONDEPHAAT", "IPHAN"):
        eg, _ = carregar(f"envoltoria_{org}.json")
        eg_all.extend(eg)
        env_nome.extend([org] * len(eg))
    eg_all = np.array(eg_all)
    arv_env = STRtree(eg_all)
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
    manchas, lotes_por_celula = [], collections.defaultdict(list)
    for membros in grupos:
        u = shapely.union_all(buf[membros]).buffer(-FOLGA, join_style="mitre")
        u = maior_poligono(u)
        if u is None:
            continue
        u = shapely.Polygon(u.exterior, [h for h in u.interiors if shapely.Polygon(h).area >= 20])
        area = float(area_geo[idx[membros]].sum())
        nota, det = nota_forma(u)
        fator = min(1.0, nota / FORMA_SEM_PENALIDADE)
        area_eq = area * fator

        ms = sorted(membros, key=lambda k: (via[idx[k]],
                                            props[idx[k]]["cd_setor_fiscal"],
                                            props[idx[k]]["cd_quadra_fiscal"],
                                            props[idx[k]]["cd_lote"]))
        p0 = props[idx[ms[0]]]          # primeiro lote fiscal (as vias ficam no fim)
        vias_m = [idx[k] for k in ms if via[idx[k]]]
        vila = "" if not vias_m else \
            "completa" if all(via_completa.get(i, True) for i in vias_m) else "parcial"
        mid = f"{p0['cd_setor_fiscal']}-{p0['cd_quadra_fiscal']}-{p0['cd_lote']}"
        zonas = collections.Counter()
        usos = collections.Counter()
        ruas = collections.Counter()
        at_iptu = ac_tot = a_nres = 0.0
        for k in ms:
            i = idx[k]
            p = props[i]
            zonas[zona[i]] += area_geo[i]
            if via[i]:
                usos["Passagem de vila"] += 1
                at_iptu += area_geo[i]
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
        ek = arv_env.query(u, predicate="intersects")
        orgaos = sorted({env_nome[k] for k in ek
                         if eg_all[k].intersection(u).area >= 0.05 * u.area})
        ne = arv_est.nearest(rp)
        dist_est = float(est_g[ne].distance(rp))
        zord = zonas.most_common()
        fam = sorted({FAMILIA[z] for z in zonas})

        manchas.append({"type": "Feature", "geometry": wgs(u.simplify(0.4)), "properties": {
            "id": mid, "area": round(area), "area_iptu": round(at_iptu),
            "n": len(ms) - len(vias_m), "vila": vila, "forma": round(nota * 100),
            "ret": round(det["ret"] * 100), "comp": round(det["comp"] * 100),
            "nv": det["nv"], "miolo": round(det["miolo"] * 100),
            "area_eq": round(area_eq), "faixa": faixa(area_eq), "faixa_area": faixa(area),
            "zona": zord[0][0], "fam": "+".join(fam),
            "zonas": " · ".join(f"{z} {round(100 * v / area)}%" for z, v in zord),
            "distrito": distrito,
            "ruas": " · ".join(r for r, _ in ruas.most_common(4)),
            "pnr": round(100 * a_nres / area),
            "ac": round(ac_tot), "ca": round(ac_tot / max(at_iptu, 1), 2),
            "usos": " · ".join(f"{v} {k.lower()}" for k, v in usos.most_common()),
            "env": "/".join(orgaos), "est": est_n[ne], "est_m": round(dist_est),
            "lon": round(lon, 6), "lat": round(lat, 6)}})

        cel = (math.floor(lon / CELULA_LOTES), math.floor(lat / CELULA_LOTES))
        manchas[-1]["properties"]["cel"] = f"{cel[0]}_{cel[1]}"
        for k in ms:
            i = idx[k]
            p = props[i]
            at = p["qt_area_terreno"] or 0
            ac = p["qt_area_construida"] or 0
            num = (p["cd_numero_porta"] or "").strip()
            lotes_por_celula[cel].append({"type": "Feature", "geometry": wgs(g[k]), "properties": {
                "m": mid,
                "sql": (f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.V{p['cd_lote']}" if via[i] else
                        f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.{p['cd_lote']}-{p['cd_digito_sql'] or ''}"),
                "end": "Passagem de vila" if via[i] else
                       ((p["nm_logradouro_completo"] or "").strip() + (", " + num if num else "")).strip(", "),
                "at": at, "ag": round(float(area_geo[i])), "ac": ac,
                "uso": "Via de acesso" if via[i] else (p["dc_tipo_uso_imovel"] or ""),
                "zona": zona[i], "v": 1 if via[i] else 0,
                "sit": p["tx_situ_lote"] or ""}})

    manchas.sort(key=lambda f: (-f["properties"]["area_eq"]))
    def gravar(nome, obj):
        """Grava JSON compacto; nomes terminados em .gz saem comprimidos
        (o navegador descomprime com DecompressionStream)."""
        txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        caminho = os.path.join(SAIDA, nome)
        if nome.endswith(".gz"):
            with open(caminho, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
                gz.write(txt)
        else:
            with open(caminho, "wb") as fh:
                fh.write(txt)

    for antigo in os.listdir(SAIDA):
        if os.path.isfile(os.path.join(SAIDA, antigo)):
            os.remove(os.path.join(SAIDA, antigo))
    for antigo in os.listdir(os.path.join(SAIDA, "lotes")):
        os.remove(os.path.join(SAIDA, "lotes", antigo))
    gravar("manchas.geojson.gz", {"type": "FeatureCollection", "features": manchas})
    for (cx, cy), feats in lotes_por_celula.items():
        gravar(f"lotes/{cx}_{cy}.geojson.gz", {"type": "FeatureCollection", "features": feats})

    # ---------------------------------------------------- camadas de apoio
    log("Camadas de apoio…")
    zfeats = []
    for cod in sorted(set(zcod)):
        u = shapely.union_all(shapely.buffer(zg[zcod == cod], 0.5)).buffer(-0.5).simplify(1.5)
        zfeats.append({"type": "Feature", "geometry": wgs(u, 5),
                       "properties": {"zona": cod, "fam": FAMILIA[cod]}})
    gravar("zonas.geojson.gz", {"type": "FeatureCollection", "features": zfeats})
    gravar("tombados.geojson.gz", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": wgs(x.simplify(0.5), 5),
         "properties": {"nome": (p["nm_area_tombada"] or "").strip(),
                        "sit": (p["tx_situacao_tombamento"] or "").strip()}}
        for x, p in zip(tg, tp)]})
    gravar("envoltorias.geojson.gz", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": wgs(x.simplify(1.0), 5), "properties": {"orgao": o}}
        for x, o in zip(eg_all, env_nome)]})
    gravar("estacoes.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": wgs(x), "properties": {"nome": n}}
        for x, n in zip(est_g, est_n)]})

    resumo = collections.Counter(f["properties"]["faixa"] for f in manchas)
    gravar("meta.json", {
        "gerado_em": datetime.date.today().isoformat(),
        "fonte": "GeoSampa (lote_cidadao, perímetro de zonas Lei 18.177/24, patrimônio cultural)",
        "lotes_analisados": int(n_total), "lotes_elegiveis": int(ok.sum()),
        "manchas": len(manchas),
        "manchas_com_vila": sum(1 for f in manchas if f["properties"]["vila"]),
        "por_faixa": {str(k): v for k, v in sorted(resumo.items())},
        "descartes": dict(motivo),
        "parametros": dict(ca_max_casa=CA_MAX_CASA, area_min=AREA_MIN, faixas=FAIXAS,
                           divisa_min=DIVISA_MIN, raio_miolo=RAIO_MIOLO, pesos=PESOS,
                           vert_ideal=VERT_IDEAL, vert_ruim=VERT_RUIM,
                           forma_sem_penalidade=FORMA_SEM_PENALIDADE,
                           celula_lotes=CELULA_LOTES)})
    log(f"Pronto: {len(manchas)} manchas, por faixa {dict(resumo)}, "
        f"{sum(len(v) for v in lotes_por_celula.values())} lotes em {len(lotes_por_celula)} arquivos")


if __name__ == "__main__":
    main()
