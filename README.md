# predict-bolao

Motor de palpites para bolão da Copa do Mundo 2026 a partir de odds da **bet365.bet.br**.

Para cada partida, gera **um palpite único consolidado** de placar exato:

1. Ajusta numericamente (λ_casa, λ_fora, ρ) para que uma matriz Poisson com
   correção Dixon-Coles reproduza simultaneamente as probabilidades justas de
   `1X2` (vitória/empate/derrota) e de `mais/menos 2.5 gols` do mercado. Com o
   ρ (correlação Dixon-Coles) livre por partida, o ajuste zera o erro nos quatro
   alvos (p_H, p_D, p_A, p_under) — antes, com ρ fixo, sobrava até ~3.6pp no empate.
2. Escolhe o placar que **maximiza os pontos esperados do bolão** sob as regras
   reais (errou o resultado = 0; acertou = base 3 + bônus de placar exato /
   placar do vencedor / saldo / placar do perdedor + bônus de goleada —
   constantes `BOLAO_*` em `config.py`), não apenas o placar mais provável.

Saída em dois formatos: tabela no terminal e **relatório HTML interativo**
(arquivo único, sem dependências) com, por partida, o heatmap da matriz completa de placares,
o ranking dos placares mais prováveis e a barra de probabilidades 1X2. Cada card
também mostra as odds cruas 1X2 e o momento da captura ("Odds 1X2: 1.80 / 3.60 /
4.50 · capturadas dd/mm HH:MM") e os λ/ρ ajustados. No relatório
dá pra alternar entre probabilidade e pontos esperados, clicar em qualquer placar
pra simular os pontos esperados, editar as regras de pontuação do bolão (palpites
recalculam na hora), filtrar por dia e copiar os palpites formatados pra colar em
chat. O relatório é dark; o título mostra quando as odds mais recentes foram
capturadas e cada card cujas odds estejam mais de 12h atrás dessa marca recebe
um aviso âmbar discreto ("odds de dd/mm HH:MM").

Os arquivos ficam organizados assim (timestamp `YYYY-MM-DD_HHhMM` por execução):

```
output/
├── capturas/                      # snapshots imutáveis, um por execução de captura
│   └── 2026-06-12_15h30.json      #   {saved_at, matches[]} — nunca sobrescreve
├── odds_atuais.json               # store: odds mais recentes POR PARTIDA
├── relatorios/
│   └── palpites_2026-06-12_15h30.html
└── palpites_atual.html            # cópia estável do relatório mais recente
```

Cada captura grava um snapshot imutável em `capturas/` e atualiza o store
`odds_atuais.json` por `match_id` (a captura nova sempre ganha, preservando o
`captured_at` de cada partida). Os relatórios são gerados do store completo,
então odds antigas que não foram recapturadas continuam aparecendo — com o aviso.

No backtest com a Premier League 2022/23 (380 jogos), sob as regras reais do
bolão, o motor consolidado rende **3.04 pontos por jogo vs 2.50 do Poisson
heurístico antigo** (+21%), acertando mais vencedores (56.6% vs 45.8%):

```bash
python main.py backtest data/backtest/PL_2022_23.csv
```

## Resultado do predictor na Copa 2026

📊 **[Ver o relatório HTML interativo completo](https://viniaraujo68.github.io/predict-bolao/)** (heatmaps, ranking de placares, simulador de pontos).

Desempenho da estratégia (palpite de maior pontos esperados) contra os placares reais, nas regras do bolão (mata-mata ×2, final ×4, com sub-palpites de prorrogação/pênaltis):

| Fase | Jogos | Pontos | Máx. possível | Aproveitamento |
|---|---:|---:|---:|---:|
| Grupos | 72 | 241 | 591 | 41% |
| Mata-mata | 32 | 232 | 616 | 38% |
| **Total** | **104** | **473** | **1207** | **39%** |

473 pts em 104 jogos (4.55/jogo) · resultados certos 67/104 · placares exatos 13/104 · aproveitamento 473/1207 (39%)

<details>
<summary>Todos os jogos (palpite vs real)</summary>

| Partida | Palpite | Real | Pontos | Máx |
|---|:---:|:---:|---:|---:|
| México x África do Sul | 2-0 | 2-0 | 8 | 8 |
| Coreia do Sul x República Tcheca | 2-1 | 2-1 | 8 | 8 |
| Canadá x Bósnia e Herzegovina | 2-1 | 1-1 | 0 | 8 |
| Estados Unidos x Paraguai | 2-1 | 4-1 | 4 | 9 |
| Catar x Suíça | 0-2 | 1-1 | 0 | 8 |
| Brasil x Marrocos | 2-0 | 1-1 | 0 | 8 |
| Haiti x Escócia | 0-2 | 0-1 | 4 | 8 |
| Austrália x Turquia | 1-2 | 2-0 | 0 | 8 |
| Alemanha x Curaçao | 4-0 | 7-1 | 4 | 9 |
| Holanda x Japão | 2-1 | 2-2 | 0 | 8 |
| Costa do Marfim x Equador | 0-0 | 1-0 | 0 | 8 |
| Suécia x Tunísia | 2-1 | 5-1 | 4 | 9 |
| Espanha x Cabo Verde | 4-0 | 0-0 | 0 | 8 |
| Bélgica x Egito | 2-0 | 1-1 | 0 | 8 |
| Arábia Saudita x Uruguai | 0-2 | 1-1 | 0 | 8 |
| Irã x Nova Zelândia | 2-1 | 2-2 | 0 | 8 |
| França x Senegal | 2-0 | 3-1 | 5 | 8 |
| Iraque x Noruega | 0-2 | 1-4 | 3 | 9 |
| Argentina x Argélia | 2-0 | 3-0 | 4 | 8 |
| Áustria x Jordânia | 2-0 | 3-1 | 5 | 8 |
| Portugal x República Democrática do Congo | 2-0 | 1-1 | 0 | 8 |
| Inglaterra x Croácia | 2-1 | 4-2 | 3 | 9 |
| Gana x Panamá | 2-1 | 1-0 | 5 | 8 |
| Uzbequistão x Colômbia | 0-2 | 1-3 | 5 | 8 |
| República Tcheca x África do Sul | 2-1 | 1-1 | 0 | 8 |
| Suíça x Bósnia e Herzegovina | 2-0 | 4-1 | 3 | 9 |
| Canadá x Catar | 2-0 | 6-0 | 4 | 9 |
| México x Coreia do Sul | 2-1 | 1-0 | 5 | 8 |
| Estados Unidos x Austrália | 2-1 | 2-0 | 6 | 8 |
| Escócia x Marrocos | 0-2 | 0-1 | 4 | 8 |
| Brasil x Haiti | 4-0 | 3-0 | 4 | 8 |
| Turquia x Paraguai | 2-1 | 0-1 | 0 | 8 |
| Holanda x Suécia | 2-1 | 5-1 | 4 | 9 |
| Alemanha x Costa do Marfim | 2-0 | 2-1 | 6 | 8 |
| Equador x Curaçao | 2-0 | 0-0 | 0 | 8 |
| Tunísia x Japão | 0-2 | 0-4 | 4 | 9 |
| Espanha x Arábia Saudita | 4-0 | 4-0 | 9 | 9 |
| Bélgica x Irã | 2-0 | 0-0 | 0 | 8 |
| Uruguai x Cabo Verde | 2-0 | 2-2 | 0 | 8 |
| Nova Zelândia x Egito | 0-2 | 1-3 | 5 | 8 |
| Argentina x Áustria | 2-0 | 2-0 | 8 | 8 |
| França x Iraque | 4-0 | 3-0 | 4 | 8 |
| Noruega x Senegal | 2-1 | 3-2 | 5 | 8 |
| Jordânia x Argélia | 0-2 | 1-2 | 6 | 8 |
| Portugal x Uzbequistão | 2-0 | 5-0 | 4 | 9 |
| Inglaterra x Gana | 2-0 | 0-0 | 0 | 8 |
| Panamá x Croácia | 0-2 | 0-1 | 4 | 8 |
| Colômbia x República Democrática do Congo | 2-0 | 1-0 | 4 | 8 |
| Bósnia e Herzegovina x Catar | 2-0 | 3-1 | 5 | 8 |
| Suíça x Canadá | 2-1 | 2-1 | 8 | 8 |
| Marrocos x Haiti | 2-0 | 4-2 | 5 | 9 |
| Escócia x Brasil | 0-2 | 0-3 | 4 | 8 |
| República Tcheca x México | 1-2 | 0-3 | 3 | 8 |
| África do Sul x Coreia do Sul | 0-2 | 1-0 | 0 | 8 |
| Curaçao x Costa do Marfim | 0-2 | 0-2 | 8 | 8 |
| Equador x Alemanha | 1-2 | 2-1 | 0 | 8 |
| Japão x Suécia | 2-1 | 1-1 | 0 | 8 |
| Tunísia x Holanda | 0-4 | 1-3 | 3 | 8 |
| Paraguai x Austrália | 0-0 | 0-0 | 8 | 8 |
| Turquia x Estados Unidos | 1-2 | 3-2 | 0 | 8 |
| Noruega x França | 0-2 | 1-4 | 3 | 9 |
| Senegal x Iraque | 2-0 | 5-0 | 4 | 9 |
| Cabo Verde x Arábia Saudita | 2-1 | 0-0 | 0 | 8 |
| Uruguai x Espanha | 0-2 | 0-1 | 4 | 8 |
| Egito x Irã | 0-0 | 1-1 | 5 | 8 |
| Nova Zelândia x Bélgica | 0-4 | 1-5 | 6 | 9 |
| Croácia x Gana | 2-0 | 2-1 | 6 | 8 |
| Panamá x Inglaterra | 0-4 | 0-2 | 4 | 8 |
| Colômbia x Portugal | 1-2 | 0-0 | 0 | 8 |
| República Democrática do Congo x Uzbequistão | 2-1 | 3-1 | 4 | 8 |
| Argélia x Áustria | 0-0 | 3-3 | 5 | 8 |
| Jordânia x Argentina | 0-2 | 1-3 | 5 | 8 |
| África do Sul x Canadá | 0-2 | 0-1 | 8 | 16 |
| Brasil x Japão | 2-1 | 2-1 | 16 | 16 |
| Alemanha x Paraguai | 2-0 | 1-1 · pên. 3-4 (Paraguai) | 6 | 28 |
| Holanda x Marrocos | 2-1 | 1-1 · pên. 2-3 (Marrocos) | 6 | 28 |
| Costa do Marfim x Noruega | 1-2 | 1-2 | 16 | 16 |
| França x Suécia | 2-0 | 3-0 | 8 | 16 |
| México x Equador | 1-0 | 2-0 | 8 | 16 |
| Inglaterra x República Democrática do Congo | 2-0 | 2-1 | 12 | 16 |
| Bélgica x Senegal | 2-1 | 2-2 · pror. 3-2 (Bélgica) | 0 | 22 |
| Estados Unidos x Bósnia e Herzegovina | 2-0 | 2-0 | 16 | 16 |
| Espanha x Áustria | 2-0 | 3-0 | 8 | 16 |
| Portugal x Croácia | 2-1 | 2-1 | 16 | 16 |
| Suíça x Argélia | 2-1 | 2-0 | 12 | 16 |
| Austrália x Egito | 1-2 | 1-1 · pên. 2-4 (Egito) | 12 | 28 |
| Argentina x Cabo Verde | 2-0 | 1-1 · pror. 3-2 (Argentina) | 0 | 22 |
| Colômbia x Gana | 2-0 | 1-0 | 8 | 16 |
| Canadá x Marrocos | 0-2 | 0-3 | 8 | 16 |
| Paraguai x França | 0-2 | 0-1 | 8 | 16 |
| Brasil x Noruega | 2-1 | 1-2 | 0 | 16 |
| México x Inglaterra | 1-2 | 2-3 | 10 | 16 |
| Portugal x Espanha | 1-2 | 0-1 | 10 | 16 |
| Estados Unidos x Bélgica | 2-1 | 1-4 | 0 | 18 |
| Argentina x Egito | 2-0 | 3-2 | 6 | 16 |
| Suíça x Colômbia | 1-2 | 0-0 · pên. 4-3 (Suíça) | 6 | 28 |
| França x Marrocos | 2-0 | 2-0 | 16 | 16 |
| Espanha x Bélgica | 2-1 | 2-1 | 16 | 16 |
| Noruega x Inglaterra | 1-2 | 1-1 · pror. 1-2 (Inglaterra) | 0 | 22 |
| Argentina x Suíça | 2-0 | 1-1 · pror. 3-1 (Argentina) | 0 | 22 |
| França x Espanha | 1-1 | 0-2 | 0 | 16 |
| Inglaterra x Argentina | 1-1 | 1-2 | 0 | 16 |
| França x Inglaterra | 2-1 | 4-6 | 0 | 18 |
| Espanha x Argentina | 2-1 | 0-0 · pror. 1-0 (Espanha) | 0 | 44 |

</details>


## Setup

```bash
cd predict-bolao
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
patchright install chromium
```

## Uso

**1. Login inicial** (uma única vez — sessão fica salva em `browser_data/`):

```bash
python main.py setup
```

Faça login na bet365.bet.br no browser que abrir e pressione Enter no terminal.

**2. Capturar odds:**

```bash
python main.py extract
```

O browser abre na bet365. Navegue até a overview da Copa do Mundo (sidebar → segunda
entrada "Copa do Mundo 2026", a da competição) e digite `auto` no terminal pra capturar
todas as partidas listadas — ou `auto 5` pra limitar às 5 primeiras. Alternativa manual:
abra uma partida no browser e pressione Enter pra capturar só ela. Como as odds mudam
de hora em hora, re-rodar `rodada` recaptura tudo e atualiza o store (nada de pular
partidas "já capturadas hoje").

**3. Gerar palpites sem scrapear:**

```bash
python main.py play                                            # do store (odds_atuais.json)
python main.py play output/capturas/2026-06-12_15h30.json     # de um snapshot específico
```

`play` também aceita os `raw_*.json` antigos (usa o `saved_at` do arquivo como
`captured_at` quando a partida não traz o seu).

**4. Preencher os placares dos jogos encerrados (ESPN):**

```bash
python main.py results
```

Pega do store os jogos **já encerrados e ainda sem placar**, consulta o
scoreboard público da ESPN (`fifa.world`, em português) nas datas deles e grava
o placar final em `output/resultados.json` — **casando por nome dos times + data,
sem precisar de id nem do browser**, e orientando o placar pela identidade dos
times (bet365 e ESPN podem discordar de mando em sede neutra). Jogos que já têm
placar são ignorados (não reconsulta a ESPN). Tanto o store (bet365) quanto a
ESPN passam o nome de cada time por `canonical_team` (`core/teams.py`), que tem o
nome canônico (português) e os apelidos de cada seleção — a bet365 grava ora em
inglês, ora em português, e tudo converge pra mesma grafia, então o casamento é
**igualdade exata**, sem heurística de similaridade. Para incluir um time novo ou
uma grafia nova, basta editar `core/teams.py`.

Os placares ficam em `output/resultados.json`, separados das odds (uma nova
captura nunca apaga um resultado). Com eles, o relatório (terminal e HTML) passa
a mostrar, por jogo resolvido, o palpite de **maior pontos esperados** vs o placar
real e os pontos que rendeu, mais um placar acumulado com o total da estratégia ao
longo da Copa. No HTML o total recalcula ao vivo conforme você edita as regras de
pontuação, e a célula do placar real fica marcada no heatmap.

> O `extract` (bet365) cuida só das **odds** — não coleta placar. A fonte de
> resultados é a ESPN (`results`); tentamos puxar pelo bet365 e ficou
> comprovado que a SPA não serve jogos encerrados por id.

**Flags úteis:**

- `--debug-network` — salva todos os JSONs capturados em `output/debug/` (útil pra ajustar `core/parsers.py` se a estrutura da bet365 mudar).

## Estrutura do projeto

```
predict-bolao/
├── main.py               # CLI (Typer)
├── config.py             # URLs, paths, timeouts
├── core/
│   ├── schemas.py        # Modelos Pydantic
│   ├── math_engine.py    # Calibração DC + mistura + pontos esperados
│   ├── parsers.py        # Heurísticas pra extrair mercados de JSONs
│   ├── ingestion.py      # Patchright + captura de responses
│   ├── persistence.py    # Snapshots imutáveis + store odds_atuais.json + resultados.json
│   ├── results_source.py # Busca placares finais na ESPN (por data) e casa por nome
│   ├── processor.py      # Pipeline Polars (DataFrame da tabela do terminal)
│   └── report.py         # Relatório HTML interativo (heatmap + simulador de palpite)
├── browser_data/         # Perfil persistente do Patchright (gitignored)
└── output/               # snapshots, store e relatórios gerados (gitignored)
```

## Quando as coisas falham

- **Browser bloqueado por Cloudflare** — resolva o Turnstile manualmente; a sessão persistente reduz a frequência.
- **Nenhuma partida reconhecida** — rode com `--debug-network` e inspecione `output/debug/`. Os JSONs internos da bet365 podem ter campos diferentes dos que `core/parsers.py` reconhece; ajuste `MARKET_NAME_PATTERNS`, `ODD_FIELDS`, etc.
- **Smoke test do motor matemático isoladamente:**
  ```bash
  python -m core.math_engine
  ```
