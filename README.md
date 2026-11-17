# Radar de Terrenos — São Paulo

Mapa para prospecção de terrenos: encontra **manchas de lotes de casas contíguos**, do mesmo
zoneamento, que unificados somam pelo menos **2.500 m²**, e classifica cada mancha por área e
por formato. Todos os zoneamentos da cidade são processados; no mapa você marca quais quer ver
(padrão: ZEU, ZC, ZEIS-5 e ZEM).

O site é estático e fica na pasta `docs/` (publicado pelo GitHub Pages).

## Critérios

| Etapa | Regra |
|---|---|
| Lote elegível | Lote fiscal do GeoSampa, fora de condomínio ("CD"), com área construída ÷ área do terreno ≤ 4 |
| Exclusões | Tombado · em área envoltória (CONPRESP/CONDEPHAAT/IPHAN) · atingido por área cedida, cadastro de área pública (CAP), diretriz (DZ), desmembramento de gleba (DG) ou conjunto (CJ) · hospital, escola estadual, polícia civil/militar e guarda civil (cadastros de equipamentos do GeoSampa) · dentro de operação urbana (perímetro principal de Faria Lima, Água Branca, Água Espraiada e Centro; os perímetros expandidos não excluem) |
| Metrô e trem | Sai o lote que uma linha (atual ou projetada) atravessa ou que contém uma estação. O lote a até 20 m de uma linha ou estação continua no mapa, com alerta ("Metrô/trem a até 20 m" ou "Linha projetada a até 20 m"). O GeoSampa não informa se o trecho é túnel ou superfície, e as linhas projetadas são traçados de referência |
| Lançamentos e prédios novos | Sai o lote com empreendimento licenciado: outorga onerosa (2000–2026, exceto indeferidos e desistências), alvará de HIS/HMP (2019–2026), cota de solidariedade, fruição pública, EIV deferido e habitação pública em projeto ou obra. Também sai o terreno sem construção, sem número, com 1.000 m² ou mais |
| Mancha | Lotes elegíveis do mesmo zoneamento (Lei 18.177/24) com divisa comum ≥ 3 m, somando ≥ 2.500 m² |
| Prédios | Lote em condomínio ("CD") entra na mancha quando a área construída estimada ÷ terreno é ≤ a régua "CA máximo dos prédios" do mapa (0 a 4, de 0,5 em 0,5; padrão 4). A área é estimada pela volumetria da camada "Edificações": projeção de cada volume × pavimentos (altura ÷ 2,9 m). Não conta subsolos e o levantamento é de 2014 |
| Vilas | A passagem de vila (lote "V" — via de acesso) entra como elo: une as casas dos dois lados e soma área, sem contar como lote a negociar. A vila é "completa" quando toda casa que encosta na passagem está na mancha |
| Alertas (não excluem) | Metrô/trem a até 20 m · lote atingido por melhoramento viário, desapropriação (DUP/DIS), unidade geotécnica I da carta de 2024, área contaminada ou com potencial de contaminação, ou parcelamento aprovado como Passagem – PS |
| Nota de forma (0–100) | 30% retangularidade · 30% miolo aproveitável (largura ≥ 20 m) · 20% perímetro × área · 20% nº de vértices (ideal ≤ 7) |
| Área equivalente | área × min(1, nota ÷ 80) |
| Cor | vermelho ≥ 2.500 m² · amarelo ≥ 4.000 m² · verde ≥ 6.000 m² (de área equivalente); cinza quando a forma derruba a área equivalente abaixo de 2.500 m² |

Um lote é considerado "atingido" por uma camada quando a sobreposição tem pelo menos 10 m² (ou 5%
do lote, o que for menor) e mais de 1,5 m de largura — filetes de desenho na divisa são ignorados.

**Ainda sem fonte de dados:** igrejas e fundações não têm cadastro no GeoSampa, e a lista de
hospitais do GeoSampa cobre principalmente a rede pública.

Todos os parâmetros estão no topo de `scripts/processar.py`.

## Atualizar os dados

```bash
pip install -r requirements.txt
python3 scripts/baixar_dados.py   # ~40 min: baixa todos os lotes, zoneamento e restrições do GeoSampa
python3 scripts/estimar_predios.py  # ~20 min: estima a área construída dos condomínios pela volumetria
python3 scripts/processar.py      # ~15 min: gera docs/dados/
```

Depois é só fazer commit da pasta `docs/dados/`.

## Fontes

GeoSampa / Prefeitura de São Paulo (WFS): `lote_cidadao`, `perimetro_zona_lei_18177_24`,
bens tombados e áreas envoltórias, área cedida, CAP, passagem, diretriz, desmembramento de gleba,
conjunto, operações urbanas, outorga onerosa, alvarás de HIS/HMP, cota de solidariedade, fruição pública,
EIV, empreendimentos habitacionais, lei de melhoramento viário vigente, plantas DUP/DIS, carta geotécnica 2024, áreas
contaminadas (SVMA e SIGAC), equipamentos de saúde, educação e segurança, distritos e estações
de metrô/trem.

É uma ferramenta de triagem. Confirme matrícula, zoneamento e restrições antes de avançar em qualquer negociação.
