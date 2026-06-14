"""CLI para o motor de palpites do bolão."""

from __future__ import annotations

from datetime import datetime

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from core.backtest import run_backtest
from core.ingestion import Bet365Scraper
from core.persistence import load_snapshot, load_store, save_snapshot, update_store
from core.processor import enrich, to_dataframe
from core.report import write_html
from core.schemas import RawMatch

app = typer.Typer(help="Motor de palpites para bolão da Copa do Mundo (bet365.bet.br).")
console = Console()


def _generate_reports(matches: list[RawMatch], ts: datetime) -> tuple:
    """Gera o HTML a partir das partidas e retorna (df, html_path).

    O df serve so pra tabela do terminal; toda a saida persistida e HTML.
    """
    rich = enrich(matches)
    df = to_dataframe(rich)
    html_path = write_html(rich, ts=ts)
    return df, html_path


@app.command()
def extract(
    debug_network: bool = typer.Option(False, "--debug-network", help="Salva todos os JSONs capturados em output/debug/."),
) -> None:
    """Captura odds da bet365, salva snapshot + atualiza o store e gera palpites."""
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
    df, html_path = _generate_reports(full, ts)
    console.print(f"[green]Relatório HTML em: {html_path}[/green]\n")
    _render_table(df)


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

    df, html_path = _generate_reports(matches, datetime.now())
    console.print(f"[green]Relatório HTML em: {html_path}[/green]\n")
    _render_table(df)


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
