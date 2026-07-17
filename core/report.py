"""Gera relatório HTML standalone e interativo com a distribuição de placares.

Um arquivo único (CSS + JS inline, sem dependências externas) com, por partida:
  - palpite recomendado + probabilidade + pontos esperados;
  - barra 1X2 (probabilidades justas do modelo);
  - heatmap da matriz completa, alternável entre P(placar) e E[pontos do bolão];
  - ranking dos placares (por probabilidade ou por pontos esperados).

Interatividade (vanilla JS sobre os dados embutidos em JSON):
  - regras de pontuação do bolão editáveis → tudo recalcula na hora;
  - switch Probabilidade ↔ Pontos esperados (heatmap + ranking);
  - filtro por dia e ordenação (data / confiança);
  - botão "copiar palpites" formatado pra colar em chat.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    BOLAO_BASE_OUTCOME,
    BOLAO_BLOWOUT_GOALS,
    BOLAO_BONUS_BLOWOUT,
    BOLAO_BONUS_DIFF,
    BOLAO_BONUS_EXACT,
    BOLAO_BONUS_LOSER_SCORE,
    BOLAO_BONUS_WINNER_SCORE,
    OUTPUT_DIR,
    bolao_weight,
)
from core.persistence import RELATORIOS_DIR, format_ts
from core.processor import _lookup_btts, _lookup_h2h, _lookup_totals
from core.schemas import RichMatch

_TOP_SCORES = 6
_STALE_HOURS = 12  # odds mais velhas que isto (vs a mais recente) recebem aviso

_CSS = f"""
:root {{
  color-scheme: dark;
  --bg: #0e1117; --card: #181d27; --ink: #e8ebf1; --muted: #95a0b4;
  --line: #2a3140; --soft: #1f2531; --accent: #25b07f; --hl: #fbbf24;
  --home: #5187f5; --draw: #717c8f; --away: #ef5350;
  --under: #5b9bd5; --over: #f0883e;
  --btts-yes: #25b07f; --btts-no: #8b6fc9;
  --shadow: 0 1px 3px rgba(0, 0, 0, .5), 0 6px 20px rgba(0, 0, 0, .35);
  --shadow-hover: 0 4px 10px rgba(0, 0, 0, .55), 0 12px 30px rgba(0, 0, 0, .4);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 28px 20px 40px; background: var(--bg); color: var(--ink);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  transition: background-color .25s ease, color .25s ease;
}}
header {{ max-width: 1480px; margin: 0 auto 16px; display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }}
header h1 {{ margin: 0; font-size: 27px; letter-spacing: -.02em; }}
header p {{ margin: 0; color: var(--muted); font-size: 13.5px; }}
.toolbar {{
  position: sticky; top: 10px; z-index: 50;
  max-width: 1480px; margin: 0 auto 22px; border-radius: 14px; border: 1px solid var(--line);
  padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center;
  box-shadow: var(--shadow); font-size: 13.5px;
  background: var(--card);
  background: color-mix(in srgb, var(--card) 88%, transparent);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
}}
.tb-label {{ color: var(--muted); margin-right: 6px; }}
.chip {{
  border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 4px 12px;
  font-size: 13px; cursor: pointer; color: var(--ink); margin-right: 4px; transition: all .15s ease;
}}
.chip:hover {{ border-color: var(--accent); color: var(--accent); }}
.chip.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
.seg {{ display: inline-flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
.seg button {{
  border: 0; background: var(--card); font: inherit; font-size: 13px; padding: 6px 12px;
  cursor: pointer; color: var(--ink); transition: all .15s ease;
}}
.seg button.active {{ background: var(--accent); color: #fff; }}
.toolbar input[type=number] {{
  font: inherit; padding: 4px 6px; border: 1px solid var(--line); border-radius: 8px;
  background: var(--card); color: var(--ink);
}}
.toolbar input[type=number] {{ width: 48px; }}
.pts-field {{ margin-right: 8px; white-space: nowrap; font-size: 12.5px; color: var(--muted); }}
#copy-btn {{
  margin-left: auto; border: 0; background: var(--accent); color: #fff; font: inherit;
  font-weight: 600; padding: 8px 16px; border-radius: 10px; cursor: pointer; transition: filter .15s ease;
}}
#copy-btn:hover {{ filter: brightness(1.1); }}
.grid {{
  max-width: 1480px; margin: 0 auto; display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
}}
.card {{
  background: var(--card); border-radius: 16px; padding: 18px 20px 16px;
  border: 1px solid var(--line); box-shadow: var(--shadow);
  transition: box-shadow .2s ease, transform .2s ease, background-color .25s ease;
}}
.card:hover {{ box-shadow: var(--shadow-hover); transform: translateY(-2px); }}
.card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
.card-head h2 {{ margin: 0; font-size: 17px; line-height: 1.3; letter-spacing: -.01em; }}
.card-head .vs {{ color: var(--muted); font-weight: 400; padding: 0 2px; }}
.when {{ color: var(--muted); font-size: 12.5px; margin-top: 3px; }}
.wmult {{
  display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  font-size: 11px; font-weight: 700; vertical-align: 1px;
  background: color-mix(in srgb, var(--hl) 18%, transparent); color: var(--hl);
  border: 1px solid color-mix(in srgb, var(--hl) 45%, transparent);
}}
.stale-odds {{ color: var(--hl); font-size: 11px; margin-top: 2px; opacity: .85; }}
.pick {{ text-align: center; flex-shrink: 0; }}
.pick-score {{
  background: linear-gradient(150deg, var(--accent), color-mix(in srgb, var(--accent) 75%, #003322));
  color: #fff; font-size: 20px; font-weight: 700;
  border-radius: 10px; padding: 5px 14px; letter-spacing: 1px; display: inline-block;
  box-shadow: 0 2px 8px color-mix(in srgb, var(--accent) 35%, transparent);
}}
.pick-prob {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
.pick-ep {{ color: var(--accent); font-size: 12px; font-weight: 600; margin-top: 2px; }}
.bar1x2 {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 14px 0 6px; }}
.bar1x2 .h {{ background: var(--home); }} .bar1x2 .d {{ background: var(--draw); }} .bar1x2 .a {{ background: var(--away); }}
.bar-ou {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 2px 0 6px; }}
.bar-ou .u {{ background: var(--under); }} .bar-ou .o {{ background: var(--over); }}
.bar-btts {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 2px 0 6px; }}
.bar-btts .y {{ background: var(--btts-yes); }} .bar-btts .n {{ background: var(--btts-no); }}
.bar-labels {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 14px; gap: 8px; }}
.bar-labels b {{ color: var(--ink); }}
.dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; vertical-align: 1px; }}
.body-flex {{ display: flex; flex-direction: column; gap: 16px; align-items: stretch; }}
table.heat {{ border-collapse: collapse; align-self: start; }}
table.heat th {{
  font-size: 11px; color: var(--muted); font-weight: 600; padding: 3px;
  text-align: center; min-width: 34px;
}}
table.heat th.axis {{ font-size: 10px; text-align: left; min-width: 0; }}
table.heat td {{
  width: 38px; height: 32px; text-align: center; font-size: 11.5px;
  border: 1px solid var(--card); border-radius: 4px; color: var(--ink); cursor: pointer;
  transition: background-color .25s ease;
}}
table.heat td:hover {{ outline: 2px solid var(--muted); outline-offset: -1px; }}
table.heat td.pick-cell {{ outline: 3px solid var(--hl); outline-offset: -1px; font-weight: 700; }}
.top-scores {{ flex: 1; min-width: 150px; }}
.top-scores h3 {{ margin: 0 0 8px; font-size: 12.5px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .4px; }}
.ts-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }}
.ts-score {{ width: 34px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ts-bar-wrap {{ flex: 1; background: var(--soft); border-radius: 4px; height: 14px; }}
.ts-bar {{ height: 14px; border-radius: 4px; background: var(--accent); opacity: .85; transition: width .3s ease; }}
.ts-row.is-pick .ts-bar {{ opacity: 1; }}
.ts-row.is-pick .ts-score {{ color: var(--accent); }}
.ts-prob {{ width: 50px; text-align: right; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
.lams {{ margin-top: 12px; font-size: 12px; color: var(--muted); }}
.ko-line {{
  margin-top: 12px; padding: 8px 12px; border-radius: 8px; background: var(--soft);
  border-left: 3px solid var(--hl); font-size: 12.5px; line-height: 1.5;
}}
.ko-line[hidden] {{ display: none; }}
.no-data {{ color: var(--muted); font-style: italic; margin: 18px 0; }}
.scoreboard {{
  max-width: 1480px; margin: 0 auto 22px; border-radius: 14px; border: 1px solid var(--line);
  background: var(--card); box-shadow: var(--shadow); padding: 14px 18px;
  display: flex; flex-wrap: wrap; gap: 10px 28px; align-items: center;
}}
.scoreboard[hidden] {{ display: none; }}
.sb-stat {{ display: flex; flex-direction: column; line-height: 1.25; }}
.sb-stat .v {{ font-size: 21px; font-weight: 700; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }}
.sb-stat .v.accent {{ color: var(--accent); }}
.sb-stat .k {{ font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; }}
.result {{
  margin-top: 12px; padding: 8px 12px; border-radius: 8px; background: var(--soft);
  border-left: 3px solid var(--muted); font-size: 13px; line-height: 1.5;
}}
.result[hidden] {{ display: none; }}
.result.hit {{ border-left-color: var(--accent); }}
.result.miss {{ border-left-color: var(--away); }}
.result .score {{ font-weight: 700; }}
.result .pts {{ font-weight: 700; }}
.result.hit .pts {{ color: var(--accent); }}
.result.miss .pts {{ color: var(--away); }}
.result .ko-out {{ color: #8a4bd1; font-weight: 600; }}
table.heat td.actual-cell {{ box-shadow: inset 0 0 0 3px var(--home); font-weight: 700; }}
footer {{ max-width: 1480px; margin: 26px auto 0; color: var(--muted); font-size: 12px; }}
"""

_JS = """
const DATA = JSON.parse(document.getElementById('match-data').textContent);
const byId = Object.fromEntries(DATA.matches.map(m => [m.id, m]));
const pct = p => (p * 100).toFixed(2) + '%';
const fmt = x => x.toFixed(3);
const sgn = Math.sign;
let VIEW = 'ep';
let STATUS = 'unresolved';   // all | resolved | unresolved

function readPts() {
  const v = id => +document.getElementById(id).value || 0;
  return {
    base: v('pts-base'), exact: v('pts-exact'), wscore: v('pts-wscore'),
    diff: v('pts-diff'), lscore: v('pts-lscore'),
    blowout: v('pts-blowout'), blowoutGoals: DATA.blowoutGoals,
  };
}

// No bolao, prorrogacao e penaltis sao palpites INDEPENDENTES do placar, entao
// nao entram no E[pontos] do placar (somariam igual em toda celula). Mas existem
// SEMPRE no mata-mata: a previsao fixa e prorrogacao=empate e penaltis=favorito,
// cada acerto vale a base x peso. Favorito = time mais provavel de vencer (90').
function updateKoLine(card, m) {
  const el = card.querySelector('.ko-line');
  if (!el) return;
  if ((m.weight || 1) < 2) { el.hidden = true; return; }
  const fav = favoriteHome(m.matrix) ? m.home : m.away;
  el.innerHTML = `<b>Mata-mata</b> — palpite: prorrogação <b>empate</b> · pênaltis <b>${fav}</b>`;
  el.hidden = false;
}

// Regras do bolao: errou o resultado -> 0; acertou -> base + no maximo um
// bonus de placar + bonus de goleada (so se o PALPITE e o jogo real tiverem
// um time fazendo 4+, i.e. voce previu a goleada e ela aconteceu).
// `w` e o peso da partida (x1 grupos, x2 mata-mata, x4 final): multiplica TODOS
// os pontos do jogo. Por ser fator constante nao altera o argmax (placar otimo),
// so escala o total — entao o palpite recomendado nao muda, mas a pontuacao sim.
function pointsFor(i, j, a, b, pts, w) {
  const pd = i - j, rd = a - b;
  if (sgn(pd) !== sgn(rd)) return 0;
  let e = pts.base;
  if (i === a && j === b) e += pts.exact;
  else if (rd === 0) e += pts.diff;
  else {
    const wp = rd > 0 ? i : j, lp = rd > 0 ? j : i;
    const wr = rd > 0 ? a : b, lr = rd > 0 ? b : a;
    if (wp === wr) e += pts.wscore;
    else if (pd === rd) e += pts.diff;
    else if (lp === lr) e += pts.lscore;
  }
  if (Math.max(i, j) >= pts.blowoutGoals && Math.max(a, b) >= pts.blowoutGoals) e += pts.blowout;
  return e * (w || 1);
}

// Favorito = lado mais provavel de vencer pela matriz 90'.
function favoriteHome(M) {
  let pH = 0, pA = 0;
  for (let i = 0; i < M.length; i++) for (let j = 0; j < M.length; j++) {
    if (i > j) pH += M[i][j]; else if (i < j) pA += M[i][j];
  }
  return pH >= pA;
}

// Sub-palpites de mata-mata (prorrogacao + penaltis), previsao fixa empate+favorito.
// Independentes do placar de 90', somam `base` por acerto. Retorna {got, max} SEM
// peso (quem multiplica pelo peso e quem chama). Constante por celula -> nao muda
// o palpite otimo do placar, so a pontuacao do jogo resolvido.
function koPoints(ko, M, base) {
  if (!ko) return { got: 0, max: 0 };
  let got = 0, max = base;                 // prorrogacao: sempre da pra cravar o desfecho
  if (ko.stage === 'penaltis') {
    got += base;                            // previmos empate -> foi a penaltis (certo)
    max += base;                            // penaltis tem vencedor cravavel
    if (ko.winnerHome === favoriteHome(M)) got += base;  // favorito venceu os penaltis
  }
  return { got, max };
}

// EP do placar (90'), so do palpite de placar. O bonus de prorrogacao/penaltis
// NAO entra aqui: como e um chute independente do placar, somaria igual em toda
// celula (ate no nao-favorito) e nao mudaria nada — fica indicado a parte.
function epCell(M, i, j, pts, w) {
  let e = 0;
  const n = M.length;
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++)
    e += M[a][b] * pointsFor(i, j, a, b, pts, 1);
  return e * (w || 1);
}

function epMatrix(M, pts, w) {
  const n = M.length;
  return Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => epCell(M, i, j, pts, w)));
}

function bestOf(E) {
  let best = { i: 0, j: 0, ep: -1 };
  for (let i = 0; i < E.length; i++) for (let j = 0; j < E.length; j++)
    if (E[i][j] > best.ep) best = { i, j, ep: E[i][j] };
  return best;
}

// Placar mais provavel (argmax da matriz de probabilidade).
function bestProb(M) {
  let best = { i: 0, j: 0, p: -1 };
  for (let i = 0; i < M.length; i++) for (let j = 0; j < M.length; j++)
    if (M[i][j] > best.p) best = { i, j, p: M[i][j] };
  return best;
}

function recolorHeat(card, m, E) {
  const vals = VIEW === 'ep' ? E : m.matrix;
  const vmax = Math.max(...vals.map(r => Math.max(...r)));
  card.querySelectorAll('td[data-i]').forEach(td => {
    const i = +td.dataset.i, j = +td.dataset.j;
    const v = vals[i][j];
    const t = vmax > 0 ? Math.pow(v / vmax, 0.75) : 0;
    td.style.background = `rgba(14,122,79,${t.toFixed(3)})`;
    td.style.color = t > 0.62 ? '#fff' : 'var(--ink)';
    if (VIEW === 'ep') {
      td.textContent = v >= 0.05 ? v.toFixed(1) : '';
      td.title = `${i}-${j}: ${fmt(v)} pts esperados`;
    } else {
      const p = m.matrix[i][j];
      td.textContent = p >= 0.005 ? Math.round(p * 100) : '';
      td.title = `${i}-${j}: ${pct(p)}`;
    }
  });
}

function rebuildTop(card, m, E, pick) {
  const vals = VIEW === 'ep' ? E : m.matrix;
  const flat = [];
  for (let i = 0; i < vals.length; i++) for (let j = 0; j < vals.length; j++)
    flat.push([vals[i][j], i, j]);
  flat.sort((x, y) => y[0] - x[0]);
  const top = flat.slice(0, 6);
  // Prob: barra = valor absoluto (0..max). EP fica numa faixa estreita longe
  // de zero, entao escala pela amplitude visivel (min..max) pra dar contraste.
  const hi = top.length ? top[0][0] : 1;
  const lo = top.length ? top[top.length - 1][0] : 0;
  const barW = v => VIEW === 'ep'
    ? (hi > lo ? 14 + (v - lo) / (hi - lo) * 86 : 100)
    : (hi > 0 ? v / hi * 100 : 0);
  const box = card.querySelector('.top-scores');
  box.querySelector('h3').textContent =
    VIEW === 'ep' ? 'Maior pontuação esperada' : 'Placares mais prováveis';
  box.querySelectorAll('.ts-row').forEach(r => r.remove());
  for (const [v, i, j] of top) {
    const isPick = `${i}-${j}` === pick;
    const row = document.createElement('div');
    row.className = 'ts-row' + (isPick ? ' is-pick' : '');
    row.dataset.score = `${i}-${j}`;
    row.innerHTML =
      `<span class="ts-score">${i}-${j}</span>` +
      `<div class="ts-bar-wrap"><div class="ts-bar" style="width:${barW(v).toFixed(0)}%"></div></div>` +
      `<span class="ts-prob">${VIEW === 'ep' ? fmt(v) : pct(m.matrix[i][j])}</span>`;
    box.appendChild(row);
  }
}

// Maior pontuacao possivel pra um resultado (palpite com hindsight = placar
// exato). Independe do tamanho do heatmap: o range escala com o placar real,
// entao funciona pra qualquer goleada (7-1, 15-0, ...) sem bugar o "de X possiveis".
function bestPossible(a, b, pts, w) {
  let best = 0;
  const hi = Math.max(a, b) + 1;
  for (let i = 0; i <= hi; i++) for (let j = 0; j <= hi; j++)
    best = Math.max(best, pointsFor(i, j, a, b, pts, w));
  return best;
}

function updateResultCard(card, m, pts) {
  const panel = card.querySelector('.result');
  card.querySelectorAll('td.actual-cell').forEach(td => td.classList.remove('actual-cell'));
  if (!m.result || m.pick == null) { panel.hidden = true; return null; }
  const [a, b] = m.result;
  const [pi, pj] = m.pick.split('-').map(Number);
  const w = m.weight || 1;
  const scoreGot = pointsFor(pi, pj, a, b, pts, w);
  const scoreMax = bestPossible(a, b, pts, w);
  // Bonus de mata-mata (prorrogacao/penaltis) somado ao placar, tudo x peso.
  const koPts = koPoints(m.ko, m.matrix, pts.base);
  const got = scoreGot + koPts.got * w;
  const max = scoreMax + koPts.max * w;
  const correct = sgn(pi - pj) === sgn(a - b);
  const exact = pi === a && pj === b;
  const td = card.querySelector(`td[data-i="${a}"][data-j="${b}"]`);
  if (td) td.classList.add('actual-cell');
  // Placar real fora do grid 6x6 (goleada): nao da pra marcar a celula.
  const offGrid = a >= m.matrix.length || b >= m.matrix.length;
  const verdict = exact ? 'placar exato ✓' : correct ? 'acertou o resultado' : 'errou o resultado';
  // Mata-mata: o placar pontuado é o de 90'; prorrogação/pênaltis (quem avançou)
  // entram só como nota, sem afetar os pontos (são palpites independentes).
  let koNote = '';
  if (m.ko) {
    const k = m.ko;
    const phase = k.stage === 'penaltis'
      ? `pênaltis ${k.pen[0]}-${k.pen[1]} → <b>${k.winner}</b> avançou`
      : `prorrogação ${k.et[0]}-${k.et[1]} → <b>${k.winner}</b> avançou`;
    // palpite fixo: empate na prorrogação + favorito nos pênaltis.
    const koBonus = koPts.got * w;
    koNote = ` <span class="ko-out">· ${phase} (mata-mata: +${koBonus} pts)</span>`;
  }
  panel.className = 'result ' + (got > 0 ? 'hit' : 'miss');
  panel.innerHTML =
    `Resultado real (90') <span class="score">${a}-${b}</span>` +
    (offGrid ? ' <span style="color:var(--muted)">(fora do heatmap)</span>' : '') +
    koNote +
    ` · palpite <b>${m.pick}</b> → ` +
    `<span class="pts">${got} pts</span> <span style="color:var(--muted)">de ${max} possíveis · ${verdict}</span>`;
  panel.hidden = false;
  return { got, max, correct, exact };
}

function updateScoreboard(agg) {
  const sb = document.getElementById('scoreboard');
  if (!sb) return;
  if (agg.n === 0) { sb.hidden = true; sb.innerHTML = ''; return; }
  const avg = agg.got / agg.n;
  const effic = agg.max > 0 ? agg.got / agg.max * 100 : 0;
  const stat = (v, k, accent) =>
    `<div class="sb-stat"><span class="v${accent ? ' accent' : ''}">${v}</span><span class="k">${k}</span></div>`;
  const label = VIEW === 'prob' ? 'pontos (placar provável)' : 'pontos (maior E[pts])';
  sb.innerHTML =
    stat(agg.got, label, true) +
    stat(`${agg.n}`, 'jogos resolvidos') +
    stat(avg.toFixed(2), 'média por jogo') +
    stat(`${agg.correct}/${agg.n}`, 'resultados certos') +
    stat(`${agg.exact}/${agg.n}`, 'placares exatos') +
    stat(`${agg.got}/${agg.max}`, `aproveitamento ${effic.toFixed(0)}%`);
  sb.hidden = false;
}

function refreshAll() {
  const pts = readPts();
  const agg = { n: 0, got: 0, max: 0, correct: 0, exact: 0 };
  document.querySelectorAll('.card[data-id]').forEach(card => {
    const m = byId[card.dataset.id];
    if (!m || !m.matrix) return;
    const E = epMatrix(m.matrix, pts, m.weight);
    // Em "Pontos esperados" o palpite maximiza E[pontos]; em "Probabilidade"
    // o palpite e o placar mais provavel — e o "pontos da estrategia" segue isso.
    const best = VIEW === 'prob' ? bestProb(m.matrix) : bestOf(E);
    m.pick = `${best.i}-${best.j}`;
    card.querySelector('.pick-score').textContent = m.pick;
    card.querySelector('.pick-prob').textContent = pct(m.matrix[best.i][best.j]) + ' do placar';
    card.querySelector('.pick-ep').textContent = fmt(E[best.i][best.j]) + ' pts esperados';
    card.querySelectorAll('td.pick-cell').forEach(td => td.classList.remove('pick-cell'));
    const td = card.querySelector(`td[data-i="${best.i}"][data-j="${best.j}"]`);
    if (td) td.classList.add('pick-cell');
    recolorHeat(card, m, E);
    rebuildTop(card, m, E, m.pick);
    updateKoLine(card, m);
    const res = updateResultCard(card, m, pts);
    if (res) { agg.n++; agg.got += res.got; agg.max += res.max; agg.correct += res.correct ? 1 : 0; agg.exact += res.exact ? 1 : 0; }
  });
  updateScoreboard(agg);
}

function statusOk(m) {
  const resolved = !!(m && m.result);
  return STATUS === 'all' || (STATUS === 'resolved' && resolved) || (STATUS === 'unresolved' && !resolved);
}

function applyFilters() {
  const day = document.querySelector('.chip.active').dataset.day;
  document.querySelectorAll('.grid .card').forEach(c => {
    const m = byId[c.dataset.id];
    const dayOk = !day || c.dataset.day === day;
    c.style.display = (dayOk && statusOk(m)) ? '' : 'none';
  });
}

function copyPicks() {
  const day = document.querySelector('.chip.active').dataset.day;
  const lines = DATA.matches
    .filter(m => m.pick && (!day || m.day === day) && statusOk(m))
    .map(m => `${m.day ? m.day + ' — ' : ''}${m.home} ${m.pick.replace('-', ' x ')} ${m.away}`);
  const text = `Palpites ${DATA.title}\\n` + lines.join('\\n');
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copy-btn');
    const old = btn.textContent;
    btn.textContent = 'Copiado ✓';
    setTimeout(() => { btn.textContent = old; }, 1600);
  });
}

document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  applyFilters();
}));
// Cada .seg controla so a si mesmo (ativa um botao por grupo).
function wireSeg(sel, onPick) {
  document.querySelectorAll(sel + ' button').forEach(b => b.addEventListener('click', () => {
    b.parentElement.querySelectorAll('button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    onPick(b);
  }));
}
wireSeg('#seg-view', b => { VIEW = b.dataset.view; refreshAll(); });
wireSeg('#seg-status', b => { STATUS = b.dataset.status; applyFilters(); });
['pts-base', 'pts-exact', 'pts-wscore', 'pts-diff', 'pts-lscore', 'pts-blowout'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshAll));
document.getElementById('copy-btn').addEventListener('click', copyPicks);

// visao padrao = pontos esperados: re-renderiza heatmap/ranking sobre o HTML inicial
refreshAll();
// filtro padrao = nao resolvidos: aplica ja na carga
applyFilters();
"""


def _heat_cell_style(p: float, p_max: float) -> str:
    """Fundo verde com intensidade proporcional à probabilidade da célula."""
    t = (p / p_max) ** 0.75 if p_max > 0 else 0.0
    color = "#fff" if t > 0.62 else "var(--ink)"
    return f"background: rgba(14, 122, 79, {t:.3f}); color: {color};"


def _heatmap(r: RichMatch) -> str:
    p = r.prediction
    matrix = p.matrix
    n = len(matrix)
    p_max = max(max(row) for row in matrix)
    pick = tuple(int(x) for x in p.score.split("-")) if p.score else (-1, -1)

    head = "".join(f"<th>{j}</th>" for j in range(n))
    rows = []
    for i in range(n):
        cells = []
        for j in range(n):
            prob = matrix[i][j]
            label = f"{prob * 100:.0f}" if prob >= 0.005 else ""
            cls = ' class="pick-cell"' if (i, j) == pick else ""
            cells.append(
                f'<td{cls} data-i="{i}" data-j="{j}" style="{_heat_cell_style(prob, p_max)}" '
                f'title="{i}-{j}: {prob * 100:.1f}%">{label}</td>'
            )
        rows.append(f"<tr><th>{i}</th>{''.join(cells)}</tr>")

    home = html.escape(r.raw.home_team)
    away = html.escape(r.raw.away_team)
    return (
        '<div><table class="heat">'
        f'<tr><th class="axis">↓ {home}<br>→ {away}</th>{head}</tr>'
        f"{''.join(rows)}</table></div>"
    )


def _top_scores(r: RichMatch) -> str:
    matrix = r.prediction.matrix
    n = len(matrix)
    flat = sorted(
        ((matrix[i][j], i, j) for i in range(n) for j in range(n)), reverse=True
    )[:_TOP_SCORES]
    top_p = flat[0][0] if flat and flat[0][0] > 0 else 1.0

    rows = []
    for prob, i, j in flat:
        is_pick = f"{i}-{j}" == r.prediction.score
        rows.append(
            f'<div class="ts-row{" is-pick" if is_pick else ""}" data-score="{i}-{j}">'
            f'<span class="ts-score">{i}-{j}</span>'
            f'<div class="ts-bar-wrap"><div class="ts-bar" style="width:{prob / top_p * 100:.0f}%"></div></div>'
            f'<span class="ts-prob">{prob * 100:.1f}%</span></div>'
        )
    return (
        '<div class="top-scores"><h3>Placares mais prováveis</h3>'
        f"{''.join(rows)}</div>"
    )


def _under_over_bar(odd_over: float, odd_under: float) -> str:
    """Barra Under/Over 2.5 a partir das odds capturadas (desvigorizadas).

    Mesma quantidade que o modelo calibra (P(under 2.5)); aqui mostramos
    direto o que veio do mercado de Gols Mais/Menos da bet365.
    """
    if not (odd_over and odd_under):
        return ""
    p_o, p_u = 1.0 / odd_over, 1.0 / odd_under
    total = p_o + p_u
    if total <= 0:
        return ""
    p_u, p_o = p_u / total, p_o / total
    return (
        '<div class="bar-ou">'
        f'<div class="u" style="width:{p_u * 100:.1f}%"></div>'
        f'<div class="o" style="width:{p_o * 100:.1f}%"></div></div>'
        '<div class="bar-labels">'
        f'<span><span class="dot" style="background:var(--under)"></span>Under 2.5 <b>{p_u * 100:.1f}%</b></span>'
        f'<span><span class="dot" style="background:var(--over)"></span>Over 2.5 <b>{p_o * 100:.1f}%</b></span></div>'
    )


def _btts_bar(odd_yes: float, odd_no: float) -> str:
    """Barra Ambos Marcam (Sim/Não) a partir das odds capturadas (desvigorizadas).

    Mostra direto o que veio do mercado 'Ambos Marcam' da bet365 — a mesma
    quantidade que agora entra como alvo extra na calibracao do modelo."""
    if not (odd_yes and odd_no):
        return ""
    p_y, p_n = 1.0 / odd_yes, 1.0 / odd_no
    total = p_y + p_n
    if total <= 0:
        return ""
    p_y, p_n = p_y / total, p_n / total
    return (
        '<div class="bar-btts">'
        f'<div class="y" style="width:{p_y * 100:.1f}%"></div>'
        f'<div class="n" style="width:{p_n * 100:.1f}%"></div></div>'
        '<div class="bar-labels">'
        f'<span><span class="dot" style="background:var(--btts-yes)"></span>Ambos marcam <b>{p_y * 100:.1f}%</b></span>'
        f'<span><span class="dot" style="background:var(--btts-no)"></span>Não <b>{p_n * 100:.1f}%</b></span></div>'
    )


def _card(r: RichMatch, newest_capture: datetime | None = None) -> str:
    p = r.prediction
    home = html.escape(r.raw.home_team)
    away = html.escape(r.raw.away_team)
    when = r.raw.match_date.strftime("%d/%m %H:%M") if r.raw.match_date else "data a definir"
    day = r.raw.match_date.strftime("%d/%m") if r.raw.match_date else ""
    weight = bolao_weight(r.raw.match_date)
    wlabel = {2: "mata-mata", 4: "final"}.get(weight)
    wbadge = f'<span class="wmult" title="{wlabel}">×{weight}</span>' if wlabel else ""
    attrs = f' data-id="{html.escape(r.raw.match_id)}" data-day="{day}" data-weight="{weight}"'

    stale = ""
    cap = r.raw.captured_at
    if cap and newest_capture and (newest_capture - cap) > timedelta(hours=_STALE_HOURS):
        stale = f'<div class="stale-odds">odds de {cap.strftime("%d/%m %H:%M")}</div>'

    head = (
        '<div class="card-head"><div>'
        f'<h2>{home} <span class="vs">×</span> {away}</h2>'
        f'<div class="when">{when}{wbadge}</div>{stale}</div>'
    )
    if p.score:
        head += (
            '<div class="pick">'
            f'<div class="pick-score">{p.score}</div>'
            f'<div class="pick-prob">{p.prob_score * 100:.2f}% do placar</div>'
            f'<div class="pick-ep">{p.expected_points * weight:.3f} pts esperados</div></div>'
        )
    head += "</div>"

    if not p.matrix:
        return (
            f'<article class="card"{attrs}>{head}'
            '<p class="no-data">Odds insuficientes para gerar a previsão.</p></article>'
        )

    bar = (
        '<div class="bar1x2">'
        f'<div class="h" style="width:{p.p_home * 100:.1f}%"></div>'
        f'<div class="d" style="width:{p.p_draw * 100:.1f}%"></div>'
        f'<div class="a" style="width:{p.p_away * 100:.1f}%"></div></div>'
        '<div class="bar-labels">'
        f'<span><span class="dot" style="background:var(--home)"></span>{home} <b>{p.p_home * 100:.1f}%</b></span>'
        f'<span><span class="dot" style="background:var(--draw)"></span>Empate <b>{p.p_draw * 100:.1f}%</b></span>'
        f'<span><span class="dot" style="background:var(--away)"></span>{away} <b>{p.p_away * 100:.1f}%</b></span></div>'
    )
    totals = r.raw.market("totals")
    odd_over, odd_under = _lookup_totals(totals.outcomes) if totals else (0.0, 0.0)
    bar += _under_over_bar(odd_over, odd_under)
    btts = r.raw.market("btts")
    odd_btts_yes, odd_btts_no = _lookup_btts(btts.outcomes) if btts else (0.0, 0.0)
    bar += _btts_bar(odd_btts_yes, odd_btts_no)
    rho_str = f" · ρ <b>{p.rho:+.2f}</b>" if p.rho is not None else ""
    lams = (
        f'<div class="lams">Gols esperados (λ): {home} <b>{p.lambda_home:.2f}</b> · '
        f"{away} <b>{p.lambda_away:.2f}</b>{rho_str}</div>"
    )

    # Odds cruas 1X2 + momento da captura (info que vivia no CSV).
    h2h = r.raw.market("h2h")
    odd_h, odd_d, odd_a = _lookup_h2h(h2h.outcomes) if h2h else (0.0, 0.0, 0.0)
    if odd_h and odd_d and odd_a:
        raw_odds = f"Odds 1X2: {odd_h:.2f} / {odd_d:.2f} / {odd_a:.2f}"
    else:
        raw_odds = "Odds 1X2: -"
    if odd_over and odd_under:
        raw_odds += f" · O/U 2.5: {odd_over:.2f} / {odd_under:.2f}"
    if odd_btts_yes and odd_btts_no:
        raw_odds += f" · Ambos: {odd_btts_yes:.2f} / {odd_btts_no:.2f}"
    captured = f" · capturadas {cap.strftime('%d/%m %H:%M')}" if cap else ""
    odds_line = f'<div class="lams">{raw_odds}{captured}</div>'
    return (
        f'<article class="card"{attrs}>{head}{bar}'
        f'<div class="body-flex">{_heatmap(r)}{_top_scores(r)}</div>'
        '<div class="result" hidden></div>'
        '<div class="ko-line" hidden></div>'
        f"{lams}{odds_line}</article>"
    )


def _embedded_json(rich: list[RichMatch], title: str) -> str:
    matches = []
    for r in rich:
        p = r.prediction
        ko = None
        if r.decision:
            d = r.decision
            ko = {
                "stage": d.stage,
                "winner": r.raw.home_team if d.winner == "home" else r.raw.away_team,
                "winnerHome": d.winner == "home",
                "et": [d.et_home, d.et_away],
                "pen": [d.pen_home, d.pen_away],
            }
        matches.append({
            "id": r.raw.match_id,
            "home": r.raw.home_team,
            "away": r.raw.away_team,
            "day": r.raw.match_date.strftime("%d/%m") if r.raw.match_date else "",
            "pick": p.score,
            "matrix": [[round(v, 6) for v in row] for row in p.matrix] if p.matrix else None,
            "result": [r.actual_home, r.actual_away] if r.is_resolved else None,
            "ko": ko,
            "weight": bolao_weight(r.raw.match_date),
        })
    payload = json.dumps(
        {
            "title": title,
            "blowoutGoals": BOLAO_BLOWOUT_GOALS,
            "matches": matches,
        },
        ensure_ascii=False,
    )
    # "</" dentro de <script> encerraria a tag — escapa por segurança
    return payload.replace("</", "<\\/")


def write_html(rich: list[RichMatch], ts: datetime | None = None) -> Path:
    """Grava relatorios/palpites_<ts>.html e copia pra palpites_atual.html.

    O titulo usa o captured_at mais recente do conjunto; cards com odds >12h
    mais velhas que esse recebem um aviso ambar discreto.
    """
    RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)

    captures = [r.raw.captured_at for r in rich if r.raw.captured_at]
    newest_capture = max(captures) if captures else None
    if newest_capture:
        title = f"odds atualizadas em {newest_capture.strftime('%d/%m %H:%M')}"
    else:
        title = "odds atualizadas"

    ordered = sorted(rich, key=lambda r: (r.raw.match_date is None, r.raw.match_date))
    cards = "".join(_card(r, newest_capture) for r in ordered)
    generated = datetime.now().strftime("%d/%m/%Y %H:%M")

    days = sorted(
        {r.raw.match_date.strftime("%d/%m") for r in rich if r.raw.match_date},
        key=lambda d: (d[3:5], d[0:2]),
    )
    chips = '<button class="chip active" data-day="">Todos</button>' + "".join(
        f'<button class="chip" data-day="{d}">{d}</button>' for d in days
    )

    doc = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Palpites — {title}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
<h1>Palpites — {title}</h1>
<p>{len(rich)} partidas · Dixon-Coles calibrado nas odds 1X2 + over/under 2.5 da bet365</p>
</header>
<div class="scoreboard" id="scoreboard" hidden></div>
<div class="toolbar">
<span><span class="tb-label">Dia:</span>{chips}</span>
<span><span class="tb-label">Jogos:</span><span class="seg" id="seg-status">
<button class="active" data-status="unresolved">Não resolvidos</button>
<button data-status="resolved">Resolvidos</button>
<button data-status="all">Todos</button></span></span>
<span><span class="tb-label">Ver:</span><span class="seg" id="seg-view">
<button class="active" data-view="ep">Pontos esperados</button>
<button data-view="prob">Probabilidade</button></span></span>
<span><span class="tb-label">Pontuação:</span>
<span class="pts-field">base <input type="number" id="pts-base" min="0" value="{BOLAO_BASE_OUTCOME}"></span>
<span class="pts-field">exato <input type="number" id="pts-exact" min="0" value="{BOLAO_BONUS_EXACT}"></span>
<span class="pts-field">placar venc. <input type="number" id="pts-wscore" min="0" value="{BOLAO_BONUS_WINNER_SCORE}"></span>
<span class="pts-field">saldo <input type="number" id="pts-diff" min="0" value="{BOLAO_BONUS_DIFF}"></span>
<span class="pts-field">placar perd. <input type="number" id="pts-lscore" min="0" value="{BOLAO_BONUS_LOSER_SCORE}"></span>
<span class="pts-field">goleada <input type="number" id="pts-blowout" min="0" value="{BOLAO_BONUS_BLOWOUT}"></span></span>
<button id="copy-btn">Copiar palpites</button>
</div>
<main class="grid">{cards}</main>
<footer>Gerado em {generated}. O palpite destacado maximiza os pontos esperados do placar (errou o resultado = 0; acertou = base + bônus de placar + goleada). Mata-mata vale ×2 e a final ×4 (pelo calendário da Copa); nesses jogos chutamos empate na prorrogação e o favorito nos pênaltis — cada acerto soma a base (×peso), à parte do placar de 90'.</footer>
<script type="application/json" id="match-data">{_embedded_json(ordered, title)}</script>
<script>{_JS}</script>
</body>
</html>"""

    path = RELATORIOS_DIR / f"palpites_{format_ts(ts)}.html"
    path.write_text(doc, encoding="utf-8")
    shutil.copyfile(path, OUTPUT_DIR / "palpites_atual.html")
    return path
