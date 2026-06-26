from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
BROWSER_DATA_DIR = PROJECT_ROOT / "browser_data"
OUTPUT_DIR = PROJECT_ROOT / "output"
DEBUG_DIR = OUTPUT_DIR / "debug"

BET365_BASE_URL = "https://www.bet365.bet.br/"
BET365_FOOTBALL_URL = "https://www.bet365.bet.br/#/AC/B1/C1/"
BET365_WORLD_CUP_KEYWORDS = ("world cup", "copa do mundo", "fifa world cup", "mundial")

# Canal do browser: "chrome" usa o Google Chrome instalado no sistema
# (recomendado contra anti-bot da bet365). Use "chromium" para o bundled do Patchright.
BROWSER_CHANNEL = "chrome"

NAV_TIMEOUT_MS = 60_000
RESPONSE_WAIT_MS = 30_000
DEFAULT_WINDOW_HOURS = 48

# Maximo de gols por time na matriz Dixon-Coles (inclusivo). 7 -> grid 8x8.
# Nao muda as decisoes (a massa acima de ~5 gols e desprezivel, auditado em
# 12/06 contra forca bruta) — 7 e so pra o heatmap do relatorio mostrar
# goleadas reais ate 7-x (ex: Alemanha 7-1).
MAX_GOALS = 7

POISSON_LAMBDA_BOUNDS = (0.1, 8.0)

# Chute inicial da correlacao Dixon-Coles. O rho hoje e ajustado por partida
# (junto de lambda_H e lambda_A em fit_lambdas), entao este valor serve so como
# x0 do otimizador. Negativo eleva 0-0 e 1-1, reduz 1-0 e 0-1. -0.15 vem de
# calibracoes em ligas europeias modernas (Boshnakov et al.).
DIXON_COLES_RHO = -0.15

# --- Predicao consolidada ---

# Pontuacao do bolao (regras reais). Errou o resultado (vencedor/empate) -> 0,
# sem nenhum bonus. Acertou -> base + no maximo um bonus de placar (acertar
# placar do vencedor E saldo implicaria placar exato) + bonus de goleada.
BOLAO_BASE_OUTCOME = 3        # acertou vencedor ou empate
BOLAO_BONUS_EXACT = 5         # placar exato
BOLAO_BONUS_WINNER_SCORE = 3  # so o placar do vencedor
BOLAO_BONUS_DIFF = 2          # saldo de gols (empate certo nao-exato sempre ganha)
BOLAO_BONUS_LOSER_SCORE = 1   # so o placar do perdedor
BOLAO_BONUS_BLOWOUT = 1       # o jogo real teve goleada (time com >= BLOWOUT_GOALS)
BOLAO_BLOWOUT_GOALS = 4

# Peso da partida no bolao (multiplica TODOS os pontos do jogo). Definido por
# data, seguindo o calendario da Copa 2026: fase de grupos ate 27/06; o mata-mata
# (Round of 32) comeca em 28/06 e vale x2; a final, em 19/07, vale x4. Como o peso
# e um fator constante por jogo, ele nao muda qual placar maximiza os pontos
# esperados (so escala o total) — serve pra ponderar o placar e o ranking real.
BOLAO_KNOCKOUT_START = date(2026, 6, 28)  # mata-mata (Round of 32)
BOLAO_FINAL_DATE = date(2026, 7, 19)      # final
BOLAO_MULT_KNOCKOUT = 2
BOLAO_MULT_FINAL = 4


def bolao_weight(match_date: datetime | date | None) -> int:
    """Multiplicador de pontos da partida conforme a fase (pela data do jogo)."""
    if match_date is None:
        return 1
    d = match_date.date() if isinstance(match_date, datetime) else match_date
    if d >= BOLAO_FINAL_DATE:
        return BOLAO_MULT_FINAL
    if d >= BOLAO_KNOCKOUT_START:
        return BOLAO_MULT_KNOCKOUT
    return 1
