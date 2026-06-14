"""Gera relatório HTML standalone e interativo com a distribuição de placares.

Um arquivo único (CSS + JS inline, sem dependências externas) com, por partida:
  - palpite recomendado + probabilidade + pontos esperados;
  - barra 1X2 (probabilidades justas do modelo);
  - heatmap clicável da matriz completa, alternável entre P(placar) e
    E[pontos do bolão] — clicar num placar simula o palpite;
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
)
from core.persistence import RELATORIOS_DIR, format_ts
from core.processor import _lookup_h2h
from core.schemas import RichMatch

_TOP_SCORES = 6
_STALE_HOURS = 12  # odds mais velhas que isto (vs a mais recente) recebem aviso

_CSS = f"""
:root {{
  color-scheme: dark;
  --bg: #0e1117; --card: #181d27; --ink: #e8ebf1; --muted: #95a0b4;
  --line: #2a3140; --soft: #1f2531; --accent: #25b07f; --hl: #fbbf24;
  --home: #5187f5; --draw: #717c8f; --away: #ef5350;
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
table.heat td.sel-cell {{ outline: 3px dashed var(--muted); outline-offset: -1px; }}
.top-scores {{ flex: 1; min-width: 150px; }}
.top-scores h3 {{ margin: 0 0 8px; font-size: 12.5px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .4px; }}
.ts-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }}
.ts-score {{ width: 34px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ts-bar-wrap {{ flex: 1; background: var(--soft); border-radius: 4px; height: 14px; }}
.ts-bar {{ height: 14px; border-radius: 4px; background: var(--accent); opacity: .85; transition: width .3s ease; }}
.ts-row.is-pick .ts-bar {{ opacity: 1; }}
.ts-row.is-pick .ts-score {{ color: var(--accent); }}
.ts-prob {{ width: 50px; text-align: right; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
.whatif {{
  margin-top: 12px; padding: 10px 12px; background: var(--soft); border-left: 3px solid var(--hl);
  border-radius: 0 8px 8px 0; font-size: 13px; line-height: 1.5;
}}
.whatif .close {{ float: right; cursor: pointer; color: var(--muted); border: 0; background: none; font-size: 15px; }}
.whatif .ok {{ color: var(--accent); font-weight: 600; }}
.whatif .worse {{ color: var(--away); font-weight: 600; }}
.lams {{ margin-top: 12px; font-size: 12px; color: var(--muted); }}
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

function readPts() {
  const v = id => +document.getElementById(id).value || 0;
  return {
    base: v('pts-base'), exact: v('pts-exact'), wscore: v('pts-wscore'),
    diff: v('pts-diff'), lscore: v('pts-lscore'),
    blowout: v('pts-blowout'), blowoutGoals: DATA.blowoutGoals,
  };
}

// Regras do bolao: errou o resultado -> 0; acertou -> base + no maximo um
// bonus de placar + bonus de goleada (so se o PALPITE e o jogo real tiverem
// um time fazendo 4+, i.e. voce previu a goleada e ela aconteceu).
function pointsFor(i, j, a, b, pts) {
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
  return e;
}

function epCell(M, i, j, pts) {
  let e = 0;
  const n = M.length;
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++)
    e += M[a][b] * pointsFor(i, j, a, b, pts);
  return e;
}

function epMatrix(M, pts) {
  const n = M.length;
  return Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => epCell(M, i, j, pts)));
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

function updateWhatif(card, m, E, pts) {
  const panel = card.querySelector('.whatif');
  if (!card._sel) { panel.hidden = true; return; }
  const [i, j] = card._sel;
  const e = E[i][j];
  const best = bestOf(E);
  const isBest = i === best.i && j === best.j;
  const n = m.matrix.length;
  let pOut = 0, pBlow = 0;
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
    if (sgn(a - b) === sgn(i - j)) pOut += m.matrix[a][b];
    if (Math.max(a, b) >= pts.blowoutGoals) pBlow += m.matrix[a][b];
  }
  const verdict = isBest
    ? '<span class="ok">é o palpite ótimo ✓</span>'
    : `<span class="worse">−${fmt(best.ep - e)} pts</span> vs o ótimo (${best.i}-${best.j}, ${fmt(best.ep)})`;
  panel.innerHTML =
    `<button class="close" title="fechar">✕</button>` +
    `Se você jogar <b>${i}-${j}</b>: <b>${fmt(e)}</b> pontos esperados — ${verdict}<br>` +
    `acerta o resultado ${pct(pOut)} · placar exato ${pct(m.matrix[i][j])} · jogo com goleada ${pct(pBlow)}`;
  panel.hidden = false;
  card.querySelectorAll('td.sel-cell').forEach(td => td.classList.remove('sel-cell'));
  const td = card.querySelector(`td[data-i="${i}"][data-j="${j}"]`);
  if (td && !td.classList.contains('pick-cell')) td.classList.add('sel-cell');
}

// Maior pontuacao possivel pra um resultado (palpite com hindsight = placar exato).
function bestPossible(a, b, pts) {
  let best = 0;
  for (let i = 0; i < 7; i++) for (let j = 0; j < 7; j++)
    best = Math.max(best, pointsFor(i, j, a, b, pts));
  return best;
}

function updateResultCard(card, m, pts) {
  const panel = card.querySelector('.result');
  card.querySelectorAll('td.actual-cell').forEach(td => td.classList.remove('actual-cell'));
  if (!m.result || m.pick == null) { panel.hidden = true; return null; }
  const [a, b] = m.result;
  const [pi, pj] = m.pick.split('-').map(Number);
  const got = pointsFor(pi, pj, a, b, pts);
  const max = bestPossible(a, b, pts);
  const correct = sgn(pi - pj) === sgn(a - b);
  const exact = pi === a && pj === b;
  const td = card.querySelector(`td[data-i="${a}"][data-j="${b}"]`);
  if (td) td.classList.add('actual-cell');
  const verdict = exact ? 'placar exato ✓' : correct ? 'acertou o resultado' : 'errou o resultado';
  panel.className = 'result ' + (got > 0 ? 'hit' : 'miss');
  panel.innerHTML =
    `Resultado real <span class="score">${a}-${b}</span> · palpite <b>${m.pick}</b> → ` +
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
    const E = epMatrix(m.matrix, pts);
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
    updateWhatif(card, m, E, pts);
    const res = updateResultCard(card, m, pts);
    if (res) { agg.n++; agg.got += res.got; agg.max += res.max; agg.correct += res.correct ? 1 : 0; agg.exact += res.exact ? 1 : 0; }
  });
  updateScoreboard(agg);
}

function applyDayFilter() {
  const day = document.querySelector('.chip.active').dataset.day;
  document.querySelectorAll('.grid .card').forEach(c => {
    c.style.display = (!day || c.dataset.day === day) ? '' : 'none';
  });
}

function copyPicks() {
  const day = document.querySelector('.chip.active').dataset.day;
  const lines = DATA.matches
    .filter(m => m.pick && (!day || m.day === day))
    .map(m => `${m.day ? m.day + ' — ' : ''}${m.home} ${m.pick.replace('-', ' x ')} ${m.away}`);
  const text = `Palpites ${DATA.title}\\n` + lines.join('\\n');
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copy-btn');
    const old = btn.textContent;
    btn.textContent = 'Copiado ✓';
    setTimeout(() => { btn.textContent = old; }, 1600);
  });
}

document.querySelector('.grid').addEventListener('click', e => {
  const close = e.target.closest('.whatif .close');
  if (close) {
    const card = close.closest('.card');
    card._sel = null;
    card.querySelectorAll('td.sel-cell').forEach(td => td.classList.remove('sel-cell'));
    card.querySelector('.whatif').hidden = true;
    return;
  }
  const td = e.target.closest('td[data-i]');
  if (!td) return;
  const card = td.closest('.card');
  const m = byId[card.dataset.id];
  if (!m || !m.matrix) return;
  card._sel = [+td.dataset.i, +td.dataset.j];
  const pts = readPts();
  updateWhatif(card, m, epMatrix(m.matrix, pts), pts);
});

document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  applyDayFilter();
}));
document.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.seg button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  VIEW = b.dataset.view;
  refreshAll();
}));
['pts-base', 'pts-exact', 'pts-wscore', 'pts-diff', 'pts-lscore', 'pts-blowout'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshAll));
document.getElementById('copy-btn').addEventListener('click', copyPicks);

// visao padrao = pontos esperados: re-renderiza heatmap/ranking sobre o HTML inicial
refreshAll();
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


def _card(r: RichMatch, newest_capture: datetime | None = None) -> str:
    p = r.prediction
    home = html.escape(r.raw.home_team)
    away = html.escape(r.raw.away_team)
    when = r.raw.match_date.strftime("%d/%m %H:%M") if r.raw.match_date else "data a definir"
    day = r.raw.match_date.strftime("%d/%m") if r.raw.match_date else ""
    attrs = f' data-id="{html.escape(r.raw.match_id)}" data-day="{day}"'

    stale = ""
    cap = r.raw.captured_at
    if cap and newest_capture and (newest_capture - cap) > timedelta(hours=_STALE_HOURS):
        stale = f'<div class="stale-odds">odds de {cap.strftime("%d/%m %H:%M")}</div>'

    head = (
        '<div class="card-head"><div>'
        f'<h2>{home} <span class="vs">×</span> {away}</h2>'
        f'<div class="when">{when}</div>{stale}</div>'
    )
    if p.score:
        head += (
            '<div class="pick">'
            f'<div class="pick-score">{p.score}</div>'
            f'<div class="pick-prob">{p.prob_score * 100:.2f}% do placar</div>'
            f'<div class="pick-ep">{p.expected_points:.3f} pts esperados</div></div>'
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
        f'<span><span class="dot" style="background:var(--home)"></span>{home} <b>{p.p_home * 100:.0f}%</b></span>'
        f'<span><span class="dot" style="background:var(--draw)"></span>Empate <b>{p.p_draw * 100:.0f}%</b></span>'
        f'<span><span class="dot" style="background:var(--away)"></span>{away} <b>{p.p_away * 100:.0f}%</b></span></div>'
    )
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
    captured = f" · capturadas {cap.strftime('%d/%m %H:%M')}" if cap else ""
    odds_line = f'<div class="lams">{raw_odds}{captured}</div>'
    return (
        f'<article class="card"{attrs}>{head}{bar}'
        f'<div class="body-flex">{_heatmap(r)}{_top_scores(r)}</div>'
        '<div class="result" hidden></div>'
        '<div class="whatif" hidden></div>'
        f"{lams}{odds_line}</article>"
    )


def _embedded_json(rich: list[RichMatch], title: str) -> str:
    matches = []
    for r in rich:
        p = r.prediction
        matches.append({
            "id": r.raw.match_id,
            "home": r.raw.home_team,
            "away": r.raw.away_team,
            "day": r.raw.match_date.strftime("%d/%m") if r.raw.match_date else "",
            "pick": p.score,
            "matrix": [[round(v, 6) for v in row] for row in p.matrix] if p.matrix else None,
            "result": [r.actual_home, r.actual_away] if r.is_resolved else None,
        })
    payload = json.dumps(
        {"title": title, "blowoutGoals": BOLAO_BLOWOUT_GOALS, "matches": matches},
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
<span><span class="tb-label">Ver:</span><span class="seg">
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
<footer>Gerado em {generated}. O palpite destacado maximiza os pontos esperados do bolão sob a pontuação configurada acima
(errou o resultado = 0; acertou = base + bônus de placar + goleada). Clique em qualquer célula do heatmap pra simular outro palpite.</footer>
<script type="application/json" id="match-data">{_embedded_json(ordered, title)}</script>
<script>{_JS}</script>
</body>
</html>"""

    path = RELATORIOS_DIR / f"palpites_{format_ts(ts)}.html"
    path.write_text(doc, encoding="utf-8")
    shutil.copyfile(path, OUTPUT_DIR / "palpites_atual.html")
    return path
