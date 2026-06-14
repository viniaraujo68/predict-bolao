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

# Maximo de gols por time na matriz Dixon-Coles (inclusivo). 5 -> grid 6x6.
# Truncar aqui nao muda as decisoes: a massa de probabilidade acima de 5 gols
# por time e desprezivel (auditado em 12/06 contra forca bruta).
MAX_GOALS = 5

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
