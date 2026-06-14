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

**4. Registrar placares reais (jogos resolvidos):**

```bash
python main.py resultado "México" 2-1     # busca por nome do time
python main.py resultado 185942569 2-1    # ou pelo match_id (sem ambiguidade)
python main.py resultado "México" --remover
```

Os placares ficam em `output/resultados.json`, separados das odds (uma nova
captura nunca apaga um resultado). Aceita `2-1`, `2x1` ou `2 1` (casa-fora). Se a
busca casar com mais de uma partida, ele lista as opções com o `match_id`.

Com placares registrados, o relatório (terminal e HTML) passa a mostrar, por
jogo resolvido, o palpite de **maior pontos esperados** vs o placar real e os
pontos que rendeu, mais um placar acumulado com o total da estratégia ao longo
da Copa. No HTML o total recalcula ao vivo conforme você edita as regras de
pontuação, e a célula do placar real fica marcada no heatmap.

**Coleta automática de placares (bet365):** durante o `extract`, no prompt de
captura há o comando **`results`** (igual ao `auto`, mas pra resultados): ele
pega do store os jogos **já encerrados (data passada) e ainda sem placar** e
busca cada um **pelo `match_id`** — você não precisa estar em página nenhuma,
já temos os ids. `results N` limita aos N mais antigos. Os placares vão pra
`resultados.json` na hora, **sem sobrescrever** o que você pôs à mão. Além
disso, qualquer jogo encerrado que você abrir manualmente também tem o placar
coletado dos frames WebSocket.

Dois pontos dependem de uma confirmação ao vivo (a SPA nova da bet365 é chata):

1. **Campo do placar.** Rode `python main.py extract --dump-scores` com um jogo
   encerrado aberto, abra `output/debug/event_fields.json`, ache o placar e
   ajuste `SCORE_FIELD_CANDIDATES` em `core/bet365_protocol.py` (hoje tenta
   `SS`/`SC`/…).
2. **Abrir o evento pelo id.** A SPA pode não renderizar deep-link de hash; o
   template `BET365_EVENT_URL_TEMPLATE` (em `config.py`) e o método
   `_open_event_by_id` ficam isolados pra ajustar a navegação se o `results`
   não trouxer os placares na primeira corrida.

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
