"""Ingestão de odds da bet365.bet.br via Patchright (Playwright hardened).

Estratégia: modo headed + contexto persistente em `browser_data/`. O usuário
faz login na bet365 dentro do browser aberto (vários jogos só servem odds com
a sessão logada) e o comando `auto` percorre os cards da overview, abrindo
cada partida por clique (deep-link de hash não renderiza na SPA nova).

Fontes de odds, em ordem:

- **DOM renderizado** da página da partida (fonte primária — `dom_parser.py`
  lê o que está na tela).
- **WebSocket frames** (fallback — bet365 empurra os mercados por protocolo
  pipe-delimited proprietário sobre WS; usado quando o DOM não renderiza).
- **HTTP JSON responses** (catálogos/metadados; também varridos no diagnóstico).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from patchright.sync_api import Response, WebSocket, sync_playwright

from config import (
    BET365_BASE_URL,
    BET365_WORLD_CUP_KEYWORDS,
    BROWSER_CHANNEL,
    BROWSER_DATA_DIR,
    DEBUG_DIR,
    NAV_TIMEOUT_MS,
)
from core.bet365_protocol import (
    ParsedEvent,
    ParsedMarket,
    classify_market_kind,
    parse_event_score,
    parse_frame,
)
from core.dom_parser import extract_match_from_html
from core.parsers import extract_markets_from_payload
from core.schemas import Market, Outcome, RawMatch


JSON_CT_RE = re.compile(r"application/json", re.IGNORECASE)
TEXT_CT_RE = re.compile(r"text/plain|text/html|application/octet-stream", re.IGNORECASE)
BET365_FRAME_RE = re.compile(r"\|(?:EV|MA|PA|CT|CL|GD);")


class CapturedPayload:
    __slots__ = ("url", "data", "ts")

    def __init__(self, url: str, data: Any) -> None:
        self.url = url
        self.data = data
        self.ts = time.time()


class Bet365Scraper:
    """Encapsula um contexto Patchright persistente apontado pra bet365.bet.br."""

    def __init__(self, debug: bool = False, dump_scores: bool = False) -> None:
        self.debug = debug
        self.dump_scores = dump_scores
        self._captured: list[CapturedPayload] = []
        self._ws_frames: list[str] = []
        self._events_state: dict[str, dict] = {}
        self._announced: set[tuple[str, str]] = set()
        self._debug_idx = 0
        self._ws_recv_idx = 0
        self._ws_sent_idx = 0
        BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        if debug:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (DEBUG_DIR / "ws").mkdir(exist_ok=True)

    def _on_response(self, response: Response) -> None:
        try:
            ctype = response.headers.get("content-type", "")
            url = response.url
        except Exception:
            return

        if JSON_CT_RE.search(ctype):
            try:
                body = response.json()
            except Exception:
                return
            self._captured.append(CapturedPayload(url=url, data=body))
            if self.debug:
                self._dump_debug(url, body)
            return

        if TEXT_CT_RE.search(ctype) or self.debug:
            try:
                text = response.text()
            except Exception:
                return
            if BET365_FRAME_RE.search(text):
                self._ingest_frame(text)
            if self.debug and text:
                self._dump_text_debug(url, text, ctype=ctype)

    def _on_websocket(self, ws: WebSocket) -> None:
        url = ws.url

        def _on_recv(payload: str | bytes) -> None:
            text = payload if isinstance(payload, str) else payload.decode("utf-8", errors="replace")
            self._ws_frames.append(text)
            if self.debug:
                self._dump_ws_frame("recv", url, text)
            self._ingest_frame(text)

        def _on_sent(payload: str | bytes) -> None:
            if not self.debug:
                return
            text = payload if isinstance(payload, str) else payload.decode("utf-8", errors="replace")
            self._dump_ws_frame("sent", url, text)

        ws.on("framereceived", _on_recv)
        ws.on("framesent", _on_sent)

    def _dump_debug(self, url: str, body: Any) -> None:
        self._debug_idx += 1
        path = DEBUG_DIR / f"{self._debug_idx:04d}.json"
        try:
            path.write_text(json.dumps({"url": url, "body": body}, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _dump_text_debug(self, url: str, text: str, ctype: str = "") -> None:
        self._debug_idx += 1
        path = DEBUG_DIR / f"{self._debug_idx:04d}.txt"
        try:
            path.write_text(
                f"# url: {url}\n# content-type: {ctype}\n{text}",
                encoding="utf-8", errors="replace",
            )
        except Exception:
            pass

    def _ingest_frame(self, text: str) -> None:
        """Parsea o frame e atualiza `_events_state` silenciosamente.

        O DOM é a fonte primária das odds; este estado WS é o fallback usado
        quando a página da partida não renderiza. (Boa parte dos frames é
        inplay de outras ligas, irrelevante — só os mercados do evento aberto
        importam.)
        """
        try:
            events = parse_frame(text)
        except Exception:
            return
        for ev in events:
            # Jogos encerrados podem chegar so com o placar (sem mercados): ainda
            # assim atualizamos o estado pra coletar o resultado.
            score = parse_event_score(ev.raw_fields)
            if not ev.markets and score is None and not ev.raw_fields:
                continue
            state = self._events_state.setdefault(
                ev.event_id,
                {"name": ev.name, "markets": {}, "last_seen": time.time(), "score": None, "ev_fields": {}},
            )
            state["last_seen"] = time.time()
            if ev.name and ev.name != "?":
                state["name"] = ev.name
            if ev.raw_fields:
                state.setdefault("ev_fields", {}).update(ev.raw_fields)
            if score is not None:
                state["score"] = score
            for market in ev.markets:
                kind = classify_market_kind(market)
                if kind is None:
                    continue
                state["markets"][kind] = market

    def _dump_ws_frame(self, direction: str, url: str, text: str) -> None:
        if direction == "recv":
            self._ws_recv_idx += 1
            idx = self._ws_recv_idx
        else:
            self._ws_sent_idx += 1
            idx = self._ws_sent_idx
        path = DEBUG_DIR / "ws" / f"{direction}_{idx:05d}.txt"
        try:
            path.write_text(f"# url: {url}\n{text}", encoding="utf-8", errors="replace")
        except Exception:
            pass

    def capture_round(
        self,
        on_ready: Callable[[], None] | None = None,
    ) -> list[RawMatch]:
        """Abre a bet365 (headed) e roda o loop de comandos (`auto`/ENTER/`fim`).

        Retorna a lista de RawMatch capturada, com odds lidas do DOM de cada
        partida e completadas pelo WebSocket quando o DOM não renderiza.
        """
        self._captured.clear()
        self._ws_frames.clear()
        self._events_state.clear()
        self._announced.clear()
        self._debug_idx = 0
        self._ws_recv_idx = 0
        self._ws_sent_idx = 0
        self._dom_matches: list[RawMatch] = []

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                channel=BROWSER_CHANNEL,
                headless=False,
                viewport={"width": 1400, "height": 900},
                no_viewport=True,
                service_workers="block",
            )
            ctx.on("response", self._on_response)
            page = ctx.new_page()
            page.on("websocket", self._on_websocket)

            visited_urls: list[str] = []
            page.on("framenavigated", lambda fr: visited_urls.append(fr.url) if fr == page.main_frame else None)

            page.goto(BET365_BASE_URL, timeout=NAV_TIMEOUT_MS)

            print("\n[captura] Browser aberto. Faca login na bet365 antes de capturar")
            print("          (varios jogos so servem odds com a sessao logada).")
            print("[captura] Comandos:")
            print("          ENTER  - captura a partida aberta na pagina atual")
            print("          auto   - varre os cards da overview e captura todos")
            print("          auto N - igual, mas limita aos N primeiros cards")
            print("          fim    - termina e gera o relatorio")
            print("[captura] Cada partida leva ~10s (abrir o card + ler odds do DOM/WS).\n")
            if on_ready:
                on_ready()
            try:
                while True:
                    cmd = input("[captura] > ").strip().lower()
                    if cmd in {"fim", "f", "q", "quit", "exit"}:
                        break
                    if cmd.startswith("auto"):
                        parts = cmd.split()
                        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                        self._auto_capture_round(page, limit=limit)
                        continue
                    self._capture_current(page)
            except (EOFError, KeyboardInterrupt):
                pass
            try:
                final_url = page.url
            except Exception:
                final_url = "?"
            ctx.close()

        print(
            f"\n[captura] {len(self._dom_matches)} partidas via DOM; "
            f"{len(self._ws_frames)} WS frames; {len(self._captured)} HTTP responses."
        )
        scores = self.collected_scores()
        if scores:
            print(f"[captura] {len(scores)} placares detectados nos frames WS.")
        if self.dump_scores:
            self._dump_event_fields()
        return self._build_matches()

    def _build_matches(self) -> list[RawMatch]:
        """Retorna as partidas capturadas (DOM + fallback por WebSocket)."""
        return list(self._dom_matches)

    def collected_scores(self) -> list[tuple[str, str, tuple[int, int]]]:
        """Placares detectados nos frames WS durante a captura.

        Retorna [(event_id, nome_do_evento, (gols_casa, gols_fora))]. Inclui
        jogos em andamento — quem decide se ja terminou e o chamador (via data).
        """
        out: list[tuple[str, str, tuple[int, int]]] = []
        for ev_id, st in self._events_state.items():
            sc = st.get("score")
            if sc is not None:
                out.append((ev_id, st.get("name") or "", sc))
        return out

    def _dump_event_fields(self) -> None:
        """Grava os campos crus de EV de todos os eventos vistos, pra confirmar
        ao vivo qual campo carrega o placar (uso com --dump-scores)."""
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                ev_id: {
                    "name": st.get("name"),
                    "score_detected": st.get("score"),
                    "ev_fields": st.get("ev_fields", {}),
                }
                for ev_id, st in self._events_state.items()
                if st.get("ev_fields")
            }
            path = DEBUG_DIR / "event_fields.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[captura] campos de EV dumpados em {path} ({len(payload)} eventos) "
                  f"-> procure o placar e ajuste SCORE_FIELD_CANDIDATES.")
        except Exception as e:
            print(f"[captura] (falha ao dumpar campos de EV: {e})")

    def _capture_current(
        self, page, hint_teams: tuple[str | None, str | None] | None = None,
    ) -> RawMatch | None:
        """Captura a partida via DOM da aba ativa.

        Se o usuario abriu a partida numa aba diferente do que o script criou,
        procura entre todas as abas a que tem URL de partida (/E<id>/).
        `hint_teams`: nomes lidos do card da overview, usados como ultimo
        fallback se o DOM da partida nao identificar os times.
        """
        page = self._pick_match_page(page) or page
        try:
            url = page.url
            html = page.content()
        except Exception as e:
            print(f"  ! erro ao ler pagina: {e}")
            return None
        ev_id = _event_id_from_url(url)
        if self.debug:
            try:
                idx = len(self._dom_matches) + 1
                html_path = DEBUG_DIR / f"page_{idx:02d}_{ev_id or 'unknown'}.html"
                html_path.write_text(html, encoding="utf-8", errors="replace")
            except Exception:
                pass
        if hint_teams and not (hint_teams[0] and hint_teams[1]):
            hint_teams = None
        match = extract_match_from_html(html, event_id=ev_id, fallback_teams=hint_teams)

        # Fallback por WebSocket: completa (ou monta) os mercados a partir dos
        # frames ja capturados quando o DOM nao os renderizou.
        ws_markets = self._ws_markets_for_event(ev_id, hint_teams)
        ws_used: list[str] = []
        if match is None and ws_markets:
            home, away = hint_teams or (None, None)
            if not (home and away):
                state = self._events_state.get(ev_id or "", {})
                home, away = _teams_from_event_name(state.get("name", ""))
            if home and away:
                match = RawMatch(
                    match_id=ev_id or f"{home}_vs_{away}".replace(" ", "_"),
                    home_team=home, away_team=away, markets={},
                )
        if match is not None:
            for kind, mkt in ws_markets.items():
                if kind not in match.markets:
                    match.markets[kind] = mkt
                    ws_used.append(kind)

        if match is None:
            print(f"  ! nao consegui identificar a partida desta pagina. URL: {url}")
            return None
        if any(m.match_id == match.match_id for m in self._dom_matches):
            print(f"  ~ {match.home_team} v {match.away_team} ja capturada, ignorando.")
            return None

        if not match.markets:
            print(
                f"  ! {match.home_team} v {match.away_team}: nenhum mercado encontrado "
                f"(DOM vazio e WS sem odds pra evento {ev_id})."
            )
            print(
                f"    Possivel causa: pagina de mata-mata sem h2h/totals na aba atual."
            )
            print(
                f"    Tenta clicar nas abas Goals/Match Markets no bet365 antes de Enter."
            )
            return None

        match.captured_at = datetime.now()
        self._dom_matches.append(match)
        mkts = ", ".join(match.markets.keys())
        suffix = f" [WS: {', '.join(ws_used)}]" if ws_used else ""
        print(f"  + {match.home_team} v {match.away_team} (markets: {mkts}){suffix}")
        return match

    def _try_capture_ws_only(
        self, ev_id: str | None, hint_teams: tuple[str | None, str | None],
    ) -> RawMatch | None:
        """Monta um RawMatch so a partir do estado WS do evento (sem DOM).

        Usado quando a pagina de mercados nao renderiza: os frames WS do evento
        ja chegaram durante o clique. Exige pelo menos o h2h pra valer a pena.
        """
        ws_markets = self._ws_markets_for_event(ev_id, hint_teams)
        if "h2h" not in ws_markets:
            return None
        home, away = hint_teams if (hint_teams and hint_teams[0] and hint_teams[1]) else (None, None)
        if not (home and away):
            _, state = self._ws_state_by_teams(*hint_teams) if hint_teams else (None, None)
            state = state or self._events_state.get(ev_id or "", {})
            home, away = _teams_from_event_name(state.get("name", ""))
        if not (home and away):
            return None
        match = RawMatch(
            match_id=ev_id or f"{home}_vs_{away}".replace(" ", "_"),
            home_team=home, away_team=away, markets=dict(ws_markets),
        )
        if any(m.match_id == match.match_id for m in self._dom_matches):
            return None
        match.captured_at = datetime.now()
        self._dom_matches.append(match)
        print(f"  + {home} v {away} (markets: {', '.join(ws_markets)}) [WS-only]")
        return match

    def _dump_event_ws(
        self, ev_id: str | None,
        hint_teams: tuple[str | None, str | None] | None = None,
    ) -> None:
        """Diagnostico de falha: separa 'a bet365 nao mandou nada' de 'mandou
        sob outro id/nome ou com mercado nao classificado'.

        Sempre grava (independente de --debug-network). Varre o estado WS por
        nome dos times e os frames/HTTP crus por mencao aos times.
        """
        home, away = hint_teams or (None, None)
        state = self._events_state.get(ev_id) if ev_id else None
        n_markets = len(state.get("markets", {})) if state else 0

        # Evento sob OUTRO id, casado por nome?
        alt_id, alt_state = (None, None)
        if home and away:
            alt_id, alt_state = self._ws_state_by_teams(home, away)

        # Frames WS / HTTP crus que mencionam os times.
        ws_hits = [f for f in self._ws_frames if _name_mentions_teams(f, home, away)] if home and away else []
        http_hits = [
            p for p in self._captured
            if _name_mentions_teams(json.dumps(p.data, ensure_ascii=False)[:20000], home, away)
        ] if home and away else []

        print(f"      diagnostico WS: evento {ev_id} -> {n_markets} mercados; "
              f"estado WS total: {len(self._events_state)} eventos")
        if alt_state is not None and alt_id != ev_id:
            print(f"      ATENCAO: '{alt_state.get('name')}' achado sob id {alt_id} "
                  f"(URL dizia {ev_id}) -> id divergente; fallback por nome deve pegar")
        print(f"      frames WS mencionando os times: {len(ws_hits)}; "
              f"respostas HTTP: {len(http_hits)}")

        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            tag = ev_id or _norm(f"{home}_{away}").replace(" ", "_") or "unknown"
            payload = {
                "url_event_id": ev_id,
                "hint_teams": [home, away],
                "state_for_url_id": _state_dump(state),
                "matched_by_name": {"id": alt_id, **(_state_dump(alt_state) or {})}
                if alt_state else None,
                "ws_frames_mentioning_teams": ws_hits[:8],
                "http_responses_mentioning_teams": [
                    {"url": p.url, "data": p.data} for p in http_hits[:4]
                ],
            }
            (DEBUG_DIR / f"fail_{tag}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2)[:400000],
                encoding="utf-8",
            )
            print(f"      dump: {DEBUG_DIR / f'fail_{tag}.json'}")
        except Exception as e:
            print(f"      (falha ao dumpar diagnostico: {e})")

    def _markets_from_state(self, state: dict) -> dict[str, Market]:
        out: dict[str, Market] = {}
        for kind, pm in state.get("markets", {}).items():
            mkt = _ws_h2h_market(pm) if kind == "h2h" else (
                _ws_totals_market(pm) if kind == "totals" else None
            )
            if mkt:
                out[kind] = mkt
        return out

    def _ws_markets_for_event(
        self, ev_id: str | None,
        hint_teams: tuple[str | None, str | None] | None = None,
    ) -> dict[str, Market]:
        """Mercados h2h/totals reconstruidos dos frames WS.

        Tenta primeiro pelo `ev_id` da URL; se nao achar (a bet365 usa ids
        diferentes na URL e no protocolo WS), procura no estado por nome dos
        times — `hint_teams` (nomes do card da overview).
        """
        if ev_id and ev_id in self._events_state:
            mk = self._markets_from_state(self._events_state[ev_id])
            if mk:
                return mk
        if hint_teams and hint_teams[0] and hint_teams[1]:
            _, state = self._ws_state_by_teams(*hint_teams)
            if state is not None:
                return self._markets_from_state(state)
        return {}

    def _ws_state_by_teams(
        self, home: str | None, away: str | None,
    ) -> tuple[str | None, dict | None]:
        """Acha no estado WS o evento cujo nome menciona os dois times."""
        for ev_id, state in self._events_state.items():
            if _name_mentions_teams(state.get("name", ""), home, away):
                return ev_id, state
        return None, None

    FIXTURE_CARD_SELECTOR = ".rcl-ParticipantFixtureDetails-clickable"

    def _count_fixture_cards(self, page) -> int:
        try:
            return page.locator(self.FIXTURE_CARD_SELECTOR).count()
        except Exception:
            return 0

    def _pick_match_page(self, default_page):
        """Encontra a aba aberta na pagina de uma partida (/E<id>/).

        Prefere a aba default se ela ja estiver numa partida. Caso contrario,
        procura entre todas as abas do contexto.
        """
        try:
            if _event_id_from_url(default_page.url):
                return default_page
            for p in default_page.context.pages:
                try:
                    if _event_id_from_url(p.url):
                        return p
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _card_team_names(self, page, index: int) -> tuple[str | None, str | None]:
        try:
            card = page.locator(self.FIXTURE_CARD_SELECTOR).nth(index)
            names = card.locator(".rcl-ParticipantFixtureDetailsTeam_TeamName").all_text_contents()
            names = [n.strip() for n in names if n.strip()]
            if len(names) >= 2:
                return names[0], names[1]
        except Exception:
            pass
        return None, None

    MATCH_ODD_SELECTORS = (
        ".srb-ParticipantResponsiveText_Odds",
        ".gl-ParticipantOddsOnly_Odds",
        ".srb-ParticipantResponsiveText",
    )

    def _wait_for_match_loaded(self, page, timeout_ms: int = 30000) -> bool:
        """Espera qualquer um dos selectors de odds renderizar."""
        import time as _t
        deadline = _t.time() + timeout_ms / 1000.0
        while _t.time() < deadline:
            for sel in self.MATCH_ODD_SELECTORS:
                try:
                    if page.locator(sel).count() > 0:
                        page.wait_for_timeout(700)
                        return True
                except Exception:
                    pass
            _t.sleep(0.3)
        return False

    def _diagnose_match_page(self, page) -> None:
        """Imprime nomes dos grupos de mercado visiveis na pagina (debug)."""
        try:
            groups = page.evaluate(
                "() => Array.from(document.querySelectorAll('.cm-MarketGroupWithIconsButton_Text'))"
                "  .map(el => el.textContent.trim()).filter(Boolean)"
            )
            if groups:
                print(f"      diagnostico: mercados visiveis: {groups[:6]}")
        except Exception:
            pass

    def _auto_capture_round(self, page, limit: int | None = None) -> None:
        """Sequencial: clica card → espera odds → captura → volta pra overview."""
        overview_url = page.url
        total_overview = self._count_fixture_cards(page)
        total_cards = total_overview
        if limit:
            total_cards = min(total_cards, limit)
        if total_cards == 0:
            print("  ! nenhum card de partida encontrado nessa pagina.")
            print("    Esperado: elementos com classe '.rcl-ParticipantFixtureDetails-clickable'.")
            if self.debug:
                try:
                    html = page.content()
                    path = DEBUG_DIR / "overview_page.html"
                    path.write_text(html, encoding="utf-8", errors="replace")
                    print(f"    HTML da overview salvo em {path} ({len(html)} bytes)")
                except Exception as e:
                    print(f"    (falha ao salvar HTML: {e})")
            return

        # Quanto da overview precisa estar renderizado pra considerar restaurada.
        # Metade do total registrado no inicio — i+1 dava falso positivo com
        # cupons pequenos (limit baixo).
        min_cards = max(1, total_overview // 2)

        print(f"  ~ {total_cards} cards na overview; capturando sequencialmente.")
        print(f"    (cada partida: ~10s. Ctrl+C interrompe; o que ja capturou entra no relatorio ao digitar 'fim'.)")
        captured = skipped = failed = 0
        # Deduplicacao SO dentro desta execucao: odds mudam de hora em hora,
        # re-rodar 'auto' deve recapturar tudo (nada de pular "ja salvas hoje").
        seen_ids: set[str] = set()

        for i in range(total_cards):
            if self._count_fixture_cards(page) <= i and not \
                    self._recover_overview(page, min_cards):
                print(f"    ~ overview nao restaurou os cards (precisava de >= {min_cards}).")
                print(f"      Auto interrompido em {i} partidas; rode 'auto' de novo pra continuar.")
                break

            label_a, label_b = self._card_team_names(page, i)
            label = f"{label_a} v {label_b}" if label_a and label_b else f"card #{i+1}"
            print(f"    [{i+1}/{total_cards}] {label}")

            # Nada de reescrever a URL pra /G40/: deep-link de hash na SPA nova
            # da bet365 renderiza painel vazio. Se as odds nao vierem, volta pra
            # overview e clica no card de novo (navegacao interna renderiza).
            loaded, ev_id = False, None
            for attempt, wait_ms in ((1, 12000), (2, 25000)):
                loaded, ev_id = self._open_card_and_wait(page, i, overview_url, min_cards, wait_ms)
                if loaded:
                    break
                self._recover_overview(page, min_cards)
                if attempt == 1:
                    print(f"      ~ odds nao vieram; segunda tentativa via overview")

            if not loaded:
                # O DOM nao renderizou, mas o clique ja assinou o evento por WS
                # e os frames podem ter chegado. Tenta montar so do WS.
                ws_match = self._try_capture_ws_only(ev_id, (label_a, label_b))
                if ws_match is not None:
                    captured += 1
                    if ev_id:
                        seen_ids.add(ev_id)
                    self._recover_overview(page, min_cards)
                    continue
                print(f"      ! odds nao vieram (DOM vazio, WS sem odds pra evento {ev_id})")
                self._dump_event_ws(ev_id, (label_a, label_b))
                self._diagnose_match_page(page)
                failed += 1
                self._recover_overview(page, min_cards)
                continue

            if ev_id and ev_id in seen_ids:
                print(f"      ~ ja capturado antes, pulando")
                skipped += 1
            else:
                match = self._capture_current(page, hint_teams=(label_a, label_b))
                if match is None:
                    self._diagnose_match_page(page)
                    failed += 1
                else:
                    captured += 1
                    if ev_id:
                        seen_ids.add(ev_id)

            self._recover_overview(page, min_cards)

        print(f"  ~ auto concluida: {captured} novas, {skipped} repetidas/puladas, {failed} falharam.")

    def _open_card_and_wait(
        self, page, i: int, overview_url: str, min_cards: int, wait_ms: int,
    ) -> tuple[bool, str | None]:
        """Garante a overview, clica o card i e espera as odds renderizarem."""
        if self._count_fixture_cards(page) <= i and not \
                self._recover_overview(page, min_cards):
            return False, None
        try:
            card = page.locator(self.FIXTURE_CARD_SELECTOR).nth(i)
            card.scroll_into_view_if_needed(timeout=5000)
            card.click(timeout=8000)
        except Exception as e:
            print(f"      ! clique falhou: {str(e).splitlines()[0]}")
            return False, None
        # A URL da overview tambem tem /E<id>/ (o id do campeonato) — espera
        # ate o event id ser OUTRO, senao um clique sem efeito passa batido.
        overview_ev = _event_id_from_url(overview_url)
        try:
            page.wait_for_url(
                lambda url: _event_id_from_url(url) not in (None, overview_ev),
                timeout=NAV_TIMEOUT_MS,
            )
        except Exception:
            print(f"      ! clique nao navegou pra pagina de partida")
            return False, None
        ev_id = _event_id_from_url(page.url)
        loaded = self._wait_for_match_loaded(page, timeout_ms=wait_ms)
        return loaded, ev_id

    ROOT_URL_MARKERS = ("about:blank", "", "/#/HO/", "/#/HO")

    def _is_root_or_home(self, url: str) -> bool:
        """True se a URL e a raiz/home (nao a overview com cards)."""
        if not url or url in ("about:blank", ""):
            return True
        # raiz exata (sem hash de navegacao, ou hash de Home)
        base = BET365_BASE_URL.rstrip("/")
        stripped = url.rstrip("/")
        if stripped == base:
            return True
        if "/#/HO" in url:
            return True
        return False

    def _wait_for_cards(self, page, min_cards: int, timeout_s: float) -> bool:
        """Espera ate `min_cards` cards aparecerem na overview, ou estoura timeout."""
        import time as _t
        deadline = _t.time() + timeout_s
        while _t.time() < deadline:
            if self._count_fixture_cards(page) >= min_cards:
                page.wait_for_timeout(400)
                return True
            _t.sleep(0.25)
        return False

    def _recover_overview(self, page, min_cards: int) -> bool:
        """Restaura a overview da Copa com >= min_cards cards renderizados.

        Estrategia (a SPA nova nao renderiza deep-link de hash, so navegacao
        por clique):
          1) go_back (ate 2x): se cair na raiz/home/about:blank, aborta o
             go_back; senao espera ~4s por cards >= min_cards.
          2) goto na RAIZ + aceitar cookies + clicar entradas da sidebar que
             batem com as keywords da Copa (ate ~6 candidatos), esperando ~8s
             por cards apos cada clique.
          3) Falhou tudo -> False.
        """
        import time as _t

        # Ja estamos na overview?
        if self._count_fixture_cards(page) >= min_cards:
            return True

        # 1) history back (ate 2 tentativas)
        for _ in range(2):
            try:
                page.go_back(timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception:
                break
            if self._is_root_or_home(page.url):
                # go_back saltou pra raiz/home — inutil, parte pro plano 2
                break
            if self._wait_for_cards(page, min_cards, timeout_s=4.0):
                return True

        # 2) goto raiz + clicar sidebar da Copa
        print(f"      ~ recuperando overview pela raiz (cards atual: "
              f"{self._count_fixture_cards(page)}, alvo: >= {min_cards})")
        try:
            page.goto(BET365_BASE_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as e:
            print(f"      ! goto(raiz) falhou: {str(e).splitlines()[0]}")
            return False
        page.wait_for_timeout(5000)
        self._accept_cookies(page)

        for cand in self._world_cup_sidebar_candidates(page, max_candidates=6):
            try:
                cand.scroll_into_view_if_needed(timeout=4000)
                cand.click(timeout=6000)
            except Exception:
                continue
            if self._wait_for_cards(page, min_cards, timeout_s=8.0):
                return True

        print(f"      ! overview nao restaurou (cards: {self._count_fixture_cards(page)}, "
              f"alvo: >= {min_cards})")
        return False

    def _accept_cookies(self, page) -> None:
        """Aceita o banner de cookies se estiver presente."""
        for text in ("Aceitar todos", "Aceitar Todos", "Aceitar"):
            try:
                btn = page.get_by_text(text, exact=False)
                if btn.count() > 0:
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _world_cup_sidebar_candidates(self, page, max_candidates: int = 6) -> list:
        """Locators da sidebar cujo texto bate com as keywords da Copa.

        Ordem dos candidatos preservada (na home a entrada certa costuma ser a
        2a; tentamos varias ate uma renderizar cards).
        """
        candidates: list = []
        seen_texts: set[str] = set()
        for kw in BET365_WORLD_CUP_KEYWORDS:
            try:
                loc = page.get_by_text(re.compile(re.escape(kw), re.IGNORECASE))
                n = min(loc.count(), max_candidates)
            except Exception:
                continue
            for idx in range(n):
                try:
                    item = loc.nth(idx)
                    txt = (item.text_content() or "").strip().lower()
                except Exception:
                    continue
                key = f"{kw}|{idx}|{txt}"
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                candidates.append(item)
                if len(candidates) >= max_candidates:
                    return candidates
        return candidates


EVENT_ID_URL_RE = re.compile(r"/E(\d+)/")


def _event_id_from_url(url: str) -> str | None:
    m = EVENT_ID_URL_RE.search(url or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Fallback por WebSocket: a SPA da bet365 empurra os mercados de cada evento
# por WS quando o card e clicado, mesmo quando a pagina de mercados nao
# renderiza no DOM. Esses frames ja sao parseados em _events_state; aqui os
# convertemos pros schemas do projeto pra recuperar os jogos que o DOM perde.
# ---------------------------------------------------------------------------

_DRAW_KEYS = ("draw", "empate", "tie")


def _teams_from_event_name(name: str) -> tuple[str | None, str | None]:
    """`Brasil v Marrocos` -> ('Brasil', 'Marrocos'); aceita ' v ' e ' @ '."""
    if not name:
        return None, None
    if " v " in name:
        h, a = name.split(" v ", 1)
        return h.strip(), a.strip()
    if " @ " in name:
        a, h = name.split(" @ ", 1)
        return h.strip(), a.strip()
    return None, None


def _state_dump(state: dict | None) -> dict | None:
    """Serializa um estado de evento WS (nome + mercados crus) pra JSON."""
    if not state:
        return None
    return {
        "name": state.get("name"),
        "markets": {
            kind: [
                {"name": o.name, "odd": o.odd, "handicap": o.handicap, "order": o.order}
                for o in pm.outcomes
            ]
            for kind, pm in state.get("markets", {}).items()
        },
    }


def _norm(s: str | None) -> str:
    """Minuscula sem acentos, pra casar nomes de times entre idiomas/builds."""
    if not s:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _team_tokens(name: str | None) -> list[str]:
    """Tokens significativos (>=3 chars) de um nome de time, normalizados."""
    return [t for t in re.split(r"[^a-z0-9]+", _norm(name)) if len(t) >= 3]


def _name_mentions_teams(text: str, home: str | None, away: str | None) -> bool:
    """True se `text` menciona ao menos um token de cada time."""
    n = _norm(text)
    h_toks, a_toks = _team_tokens(home), _team_tokens(away)
    if not h_toks or not a_toks:
        return False
    return any(t in n for t in h_toks) and any(t in n for t in a_toks)


def _ws_h2h_market(pm: ParsedMarket) -> Market | None:
    """Constroi um Market h2h a partir de um ParsedMarket de WS (Resultado Final).

    Espera 3 outcomes com odds; ordena por OR (0=casa, 1=empate, 2=fora) e
    rotula posicionalmente como Home/Draw/Away (compativel com _lookup_h2h).
    """
    outs = [o for o in pm.outcomes if o.odd and o.odd > 1.0]
    if len(outs) < 3:
        return None
    ordered = sorted(outs, key=lambda o: o.order if o.order is not None else 99)[:3]
    labels = ("Home", "Draw", "Away")
    return Market(
        kind="h2h",
        outcomes=[Outcome(name=labels[i], odd=o.odd) for i, o in enumerate(ordered)],
    )


def _ws_totals_market(pm: ParsedMarket, line: float = 2.5) -> Market | None:
    """Constroi um Market totals (over/under na `line`) de um ParsedMarket de WS.

    O mercado de gols no WS costuma trazer varias linhas; seleciona a `line`
    pelo handicap (HA) e, em ultimo caso, pelo nome do outcome.
    """
    line_str = f"{line:g}"
    over = under = None
    for o in pm.outcomes:
        if not (o.odd and o.odd > 1.0):
            continue
        ha = (o.handicap or "").strip()
        nm = o.name.lower()
        on_line = ha in (line_str, str(line)) or line_str in nm
        if not on_line:
            continue
        if "mais" in nm or "over" in nm:
            over = over or o.odd
        elif "menos" in nm or "under" in nm:
            under = under or o.odd
    if over and under:
        return Market(
            kind="totals",
            outcomes=[
                Outcome(name=f"Mais de {line_str}", odd=over),
                Outcome(name=f"Menos de {line_str}", odd=under),
            ],
        )
    return None
