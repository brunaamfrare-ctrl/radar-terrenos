# Radar de Terrenos — São Paulo

Mapa para prospecção de terrenos: encontra **manchas de lotes de casas contíguos**
em zonas **ZEU** e **ZC** que, unificados, somam pelo menos **2.500 m²**, e classifica
cada mancha por área e por formato.

O site é estático e fica na pasta `docs/` (publicado pelo GitHub Pages).

## Critérios

| Etapa | Regra |
|---|---|
| Lote elegível | Lote fiscal do GeoSampa, fora de condomínio ("CD"), com área construída ÷ área do terreno ≤ 4, com ≥ 50% da área em ZEU/ZEUa/ZEUP/ZEUPa/ZC/ZCa/ZC-ZEIS (Lei 18.177/24) e não tombado |
| Mancha | Lotes elegíveis com divisa comum ≥ 3 m, somando ≥ 2.500 m² |
| Nota de forma (0–100) | 30% retangularidade · 30% miolo aproveitável (largura ≥ 20 m) · 20% perímetro × área · 20% nº de vértices (ideal ≤ 7) |
| Área equivalente | área × min(1, nota ÷ 80) |
| Cor | vermelho ≥ 2.500 m² · amarelo ≥ 4.000 m² · verde ≥ 6.000 m² (de área equivalente); cinza quando a forma derruba a área equivalente abaixo de 2.500 m² |

Todos os parâmetros estão no topo de `scripts/processar.py`.

## Atualizar os dados

```bash
pip install -r requirements.txt
python3 scripts/baixar_dados.py   # ~20 min: baixa lotes, zoneamento e patrimônio do GeoSampa
python3 scripts/processar.py      # ~5 min: gera docs/dados/
```

Depois é só fazer commit da pasta `docs/dados/`.

## Fontes

GeoSampa / Prefeitura de São Paulo (WFS): `lote_cidadao`, `perimetro_zona_lei_18177_24`,
`patrimonio_cultural_bem_tombado`, áreas envoltórias CONPRESP/CONDEPHAAT/IPHAN,
distritos e estações de metrô/trem.

É uma ferramenta de triagem. Confirme matrícula, zoneamento e restrições antes de avançar em qualquer negociação.
