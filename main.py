"""CLI para o motor de palpites do bolão."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from core.backtest import run_backtest
from core.ingestion import Bet365Scraper
from core.math_engine import bolao_points
from core.persistence import (
    load_results,
    load_snapshot,
    load_store,
    remove_result,
    save_snapshot,
    set_result,
    update_store,
)
from core.processor import enrich, to_dataframe
from core.report import write_html
from core.results_source import (
    dates_window,
    fetch_scoreboard,
    match_event,
    unresolved_past_matches,
)
from core.schemas import RawMatch, RichMatch

app = typer.Typer(help="Motor de palpites para bolão da Copa do Mundo (bet365.bet.br).")
console = Console()


def _generate_reports(matches: list[RawMatch], ts: datetime) -> tuple:
    """Gera o HTML a partir das partidas e retorna (df, html_path, rich).

    O df serve so pra tabela do terminal; toda a saida persistida e HTML.
    Anexa placares reais (resultados.json) aos jogos ja resolvidos.
    """
    rich = enrich(matches)
    results = load_results()
    for r in rich:
        res = results.get(r.raw.match_id)
        if res:
            r.actual_home, r.actual_away = res
    df = to_dataframe(rich)
    html_path = write_html(rich, ts=ts)
    return df, html_path, rich


@app.command()
def extract(
    debug_network: bool = typer.Option(False, "--debug-network", help="Salva todos os JSONs capturados em output/debug/."),
) -> None:
    """Captura odds da bet365, salva snapshot + atualiza o store e gera palpites.

    (Placares de jogos encerrados não vêm daqui — use `buscar-resultados`.)
    """
    scraper = Bet365Scraper(debug=debug_network)
    matches = scraper.capture_round()

    if not matches:
        console.print("[yellow]Nenhuma partida capturada.[/yellow]")
        if not debug_network:
            console.print("[yellow]Tente novamente com --debug-network e inspecione output/debug/.[/yellow]")
        raise typer.Exit(code=1)

    ts = datetime.now()
    snap_path = save_snapshot(matches, ts=ts)
    store_path, novas, atualizadas = update_store(matches)
    console.print(f"[green]Snapshot imutável em: {snap_path}[/green]")
    console.print(
        f"[green]Store atualizado: {store_path} "
        f"({novas} novas, {atualizadas} atualizadas)[/green]"
    )

    # Relatorios sempre do store COMPLETO (nao so do snapshot recem-capturado).
    full = load_store()
    df, html_path, rich = _generate_reports(full, ts)
    console.print(f"[green]Relatório HTML em: {html_path}[/green]\n")
    _render_table(df)
    _render_resolved(rich)


@app.command()
def play(
    file: str | None = typer.Argument(
        None, help="Snapshot/raw específico. Sem argumento: regenera do store (odds_atuais.json)."
    ),
) -> None:
    """Gera palpites sem scrapear. Sem argumento usa o store; com argumento, um snapshot/raw."""
    if file:
        try:
            matches, _ = load_snapshot(file)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)
        console.print(f"[dim]Recarregado: {file} ({len(matches)} partidas)[/dim]\n")
    else:
        matches = load_store()
        if not matches:
            console.print("[red]Store vazio (output/odds_atuais.json). Rode 'python main.py rodada' antes.[/red]")
            raise typer.Exit(code=1)
        console.print(f"[dim]Recarregado do store: {len(matches)} partidas[/dim]\n")

    df, html_path, rich = _generate_reports(matches, datetime.now())
    console.print(f"[green]Relatório HTML em: {html_path}[/green]\n")
    _render_table(df)
    _render_resolved(rich)


def _render_table(df: pl.DataFrame) -> None:
    table = Table(title="Palpites da rodada", show_lines=False)
    table.add_column("Partida", style="cyan")
    table.add_column("Data", style="dim")
    table.add_column("Odds", style="dim")
    table.add_column("Palpite", justify="center", style="bold green")
    table.add_column("P(placar)", justify="right")
    table.add_column("1X2 (H/D/A)", justify="center", style="magenta")
    table.add_column("λ (H/A)", justify="center", style="dim")

    for row in df.iter_rows(named=True):
        match = f"{row['home_team']} x {row['away_team']}"
        when = row["match_date"].strftime("%d/%m %H:%M") if row["match_date"] else "-"
        odds_at = row["odds_captured_at"].strftime("%d/%m %H:%M") if row["odds_captured_at"] else "-"
        prob = f"{row['prob_score']:.2%}" if row["prob_score"] else "-"
        if row["p_home"] is not None:
            h2h = f"{row['p_home']:.0%}/{row['p_draw']:.0%}/{row['p_away']:.0%}"
        else:
            h2h = "-"
        if row["lambda_home"] is not None:
            lams = f"{row['lambda_home']:.2f}/{row['lambda_away']:.2f}"
        else:
            lams = "-"
        table.add_row(
            match,
            when,
            odds_at,
            row["predicted_score"] or "-",
            prob,
            h2h,
            lams,
        )

    console.print(table)


def _render_resolved(rich: list[RichMatch]) -> None:
    """Mostra os jogos resolvidos: palpite (maior E[pontos]) vs placar real e
    o total acumulado da estrategia."""
    resolved = [r for r in rich if r.is_resolved]
    if not resolved:
        return

    table = Table(title="Jogos resolvidos — desempenho da estratégia", show_lines=False)
    table.add_column("Partida", style="cyan")
    table.add_column("Palpite", justify="center", style="bold green")
    table.add_column("Real", justify="center")
    table.add_column("Pontos", justify="right")
    table.add_column("Máx", justify="right", style="dim")

    total = max_total = 0.0
    n_correct = n_exact = 0
    resolved.sort(key=lambda r: (r.raw.match_date is None, r.raw.match_date))
    for r in resolved:
        a, b = r.actual_home, r.actual_away
        pick = r.prediction.score or "-"
        if r.prediction.score:
            pi, pj = (int(x) for x in r.prediction.score.split("-"))
            got = bolao_points(pi, pj, a, b)
            correct = (pi > pj) == (a > b) and (pi < pj) == (a < b)
            exact = (pi, pj) == (a, b)
        else:
            got, correct, exact = 0.0, False, False
        best = max(
            bolao_points(i, j, a, b) for i in range(7) for j in range(7)
        )
        total += got
        max_total += best
        n_correct += int(correct)
        n_exact += int(exact)
        style = "green" if got > 0 else "red"
        table.add_row(
            f"{r.raw.home_team} x {r.raw.away_team}",
            pick,
            f"{a}-{b}",
            f"[{style}]{got:.0f}[/{style}]",
            f"{best:.0f}",
        )

    console.print()
    console.print(table)
    n = len(resolved)
    avg = total / n if n else 0.0
    effic = total / max_total * 100 if max_total else 0.0
    console.print(
        f"[bold]Total: {total:.0f} pts[/bold] em {n} jogos "
        f"([green]{avg:.2f}/jogo[/green]) · resultados certos {n_correct}/{n} · "
        f"placares exatos {n_exact}/{n} · aproveitamento {total:.0f}/{max_total:.0f} ({effic:.0f}%)"
    )


def _normalize(s: str) -> str:
    """Minusculas sem acento, pra casar nomes de time digitados a mao."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _parse_score(placar: str) -> tuple[int, int] | None:
    """Aceita '2-1', '2x1', '2 1'. Retorna (casa, fora) ou None."""
    m = re.fullmatch(r"\s*(\d+)\s*[-x: ]\s*(\d+)\s*", placar, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _find_matches(matches: list[RawMatch], busca: str) -> list[RawMatch]:
    """Casa por match_id exato ou por substring (sem acento) no nome dos times."""
    exact = [m for m in matches if m.match_id == busca]
    if exact:
        return exact
    q = _normalize(busca)
    return [
        m for m in matches
        if q in _normalize(m.home_team) or q in _normalize(m.away_team)
    ]


@app.command()
def resultado(
    busca: str = typer.Argument(..., help="Time (parte do nome) ou match_id da partida."),
    placar: str | None = typer.Argument(None, help="Placar real casa-fora, ex: 2-1. Omita com --remover."),
    remover: bool = typer.Option(False, "--remover", help="Remove o placar salvo da partida."),
) -> None:
    """Registra (ou remove) o placar real de uma partida e regenera o relatório."""
    matches = load_store()
    if not matches:
        console.print("[red]Store vazio (output/odds_atuais.json). Rode 'python main.py extract' antes.[/red]")
        raise typer.Exit(code=1)

    found = _find_matches(matches, busca)
    if not found:
        console.print(f"[red]Nenhuma partida casa com '{busca}'.[/red]")
        raise typer.Exit(code=1)
    if len(found) > 1:
        console.print(f"[yellow]'{busca}' casa com {len(found)} partidas — seja mais específico:[/yellow]")
        for m in found:
            when = m.match_date.strftime("%d/%m %H:%M") if m.match_date else "?"
            console.print(f"  • {m.home_team} x {m.away_team} ({when}) [dim]{m.match_id}[/dim]")
        raise typer.Exit(code=1)

    m = found[0]
    if remover:
        remove_result(m.match_id)
        console.print(f"[green]Resultado removido: {m.home_team} x {m.away_team}[/green]")
    else:
        if not placar:
            console.print("[red]Informe o placar (ex: 2-1) ou use --remover.[/red]")
            raise typer.Exit(code=1)
        parsed = _parse_score(placar)
        if parsed is None:
            console.print(f"[red]Placar inválido: '{placar}'. Use casa-fora, ex: 2-1.[/red]")
            raise typer.Exit(code=1)
        h, a = parsed
        set_result(m.match_id, h, a)
        console.print(f"[green]Resultado salvo: {m.home_team} {h} x {a} {m.away_team}[/green]")

    df, html_path, rich = _generate_reports(load_store(), datetime.now())
    console.print(f"[green]Relatório HTML atualizado: {html_path}[/green]")
    _render_resolved(rich)


@app.command(name="buscar-resultados")
def buscar_resultados() -> None:
    """Busca na ESPN (por data) o placar final dos jogos já encerrados sem placar
    e grava em resultados.json — casa por nome dos times, sem precisar de id nem
    do browser. Não sobrescreve placares já existentes."""
    store = load_store()
    if not store:
        console.print("[red]Store vazio (output/odds_atuais.json). Rode 'python main.py extract' antes.[/red]")
        raise typer.Exit(code=1)

    targets = unresolved_past_matches(store, load_results(), datetime.now())
    if not targets:
        console.print("[green]Nenhum jogo encerrado pendente de placar — tudo já resolvido.[/green]")
        return

    console.print(f"[dim]{len(targets)} jogos encerrados sem placar; consultando a ESPN...[/dim]")
    pool = []
    for date in dates_window(targets):
        try:
            pool.extend(fetch_scoreboard(date))
        except Exception as e:
            console.print(f"[yellow]  ! falha ao consultar {date}: {str(e).splitlines()[0]}[/yellow]")

    if not pool:
        console.print("[red]Não consegui nenhum dado da ESPN (sem rede ou API mudou).[/red]")
        raise typer.Exit(code=1)

    aplicados = 0
    for m in targets:
        score = match_event(m, pool)
        if score is None:
            console.print(f"[yellow]  ? sem placar na ESPN: {m.home_team} x {m.away_team}[/yellow]")
            continue
        set_result(m.match_id, score[0], score[1])
        aplicados += 1
        console.print(f"[green]  + {m.home_team} {score[0]} x {score[1]} {m.away_team}[/green]")

    console.print(f"[green]{aplicados}/{len(targets)} placares gravados.[/green]")
    _, html_path, rich = _generate_reports(load_store(), datetime.now())
    console.print(f"[green]Relatório HTML atualizado: {html_path}[/green]")
    _render_resolved(rich)


@app.command()
def backtest(csv_path: str = typer.Argument(..., help="CSV no formato football-data.co.uk (precisa ter B365H/D/A e B365>2.5/<2.5).")) -> None:
    """Compara o motor antigo (Poisson heuristico) com o consolidado em odds historicas."""
    try:
        result = run_backtest(csv_path)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Backtest sobre {result.n_matches} partidas[/bold]\n")

    metrics = Table(title="Metricas")
    metrics.add_column("Metrica", style="cyan")
    metrics.add_column("Antigo (Poisson argmax)", justify="right", style="green")
    metrics.add_column("Novo (consolidado)", justify="right", style="blue")
    metrics.add_column("Mercado", justify="right", style="yellow")
    metrics.add_row(
        "Pontos bolao (media/jogo)",
        f"{result.antigo.pontos_bolao:.3f}",
        f"{result.novo.pontos_bolao:.3f}",
        "-",
    )
    metrics.add_row(
        "Placar exato",
        f"{result.antigo.placar_exato_pct:.1%}",
        f"{result.novo.placar_exato_pct:.1%}",
        "-",
    )
    metrics.add_row(
        "Vencedor (H/D/A)",
        f"{result.antigo.vencedor_pct:.1%}",
        f"{result.novo.vencedor_pct:.1%}",
        f"{result.market_vencedor_pct:.1%}",
    )
    metrics.add_row(
        "Over/Under 2.5",
        f"{result.antigo.ou25_pct:.1%}",
        f"{result.novo.ou25_pct:.1%}",
        f"{result.market_ou25_pct:.1%}",
    )
    metrics.add_row(
        "Log-loss 1X2 (menor=melhor)",
        f"{result.antigo.log_loss_1x2:.3f}",
        f"{result.novo.log_loss_1x2:.3f}",
        "-",
    )
    metrics.add_row(
        "Brier 1X2 (menor=melhor)",
        f"{result.antigo.brier_1x2:.3f}",
        f"{result.novo.brier_1x2:.3f}",
        "-",
    )
    metrics.add_row(
        "P(placar) media do palpite",
        f"{result.antigo.avg_pick_prob:.1%}",
        f"{result.novo.avg_pick_prob:.1%}",
        "-",
    )
    console.print(metrics)

    if result.sample_predictions:
        console.print(f"\n[dim]Amostra das {len(result.sample_predictions)} primeiras previsoes:[/dim]")
        samples = Table()
        samples.add_column("Partida")
        samples.add_column("Antigo", justify="center", style="green")
        samples.add_column("Novo", justify="center", style="blue")
        samples.add_column("Real", justify="center")
        samples.add_column("Vencedor real")
        for s in result.sample_predictions:
            ok_antigo = "[green]✓[/green]" if s["pred_antigo"] == s["actual_score"] else ""
            ok_novo = "[green]✓[/green]" if s["pred_novo"] == s["actual_score"] else ""
            samples.add_row(
                f"{s['home']} x {s['away']}",
                f"{s['pred_antigo']} {ok_antigo}",
                f"{s['pred_novo']} {ok_novo}",
                s["actual_score"],
                s["actual_winner"],
            )
        console.print(samples)


if __name__ == "__main__":
    app()
