"""Reprocessa os sinais ext de oper_abertas para um periodo passado, dia a dia.

Corre Operacoes_abertas_ext.main para cada data de Stocks_Ind no periodo, sem exportar a cada dia;
no fim exporta oper_abertas uma vez para o Metabase (DuckDB e SQLite).
Os ext do dia sao refeitos todos (travar=False): os travados de rodadas durante o dia nao sao mantidos.
(2026-11-17: os sinais fase2 sairam da atualizacao; Operacoes_abertas.py em backup/antes_remover_fase_20261117.)

Uso: python Reprocessar_oper_abertas.py AAAAMMDD AAAAMMDD [--so-liq]
     --so-liq: so refaz a tabela oper_abertas_liq (ordem so entre tickers liquidos, usada pela etapa13);
               oper_abertas fica como esta.
"""
import contextlib
import io
import sys

import duckdb

import Operacoes_abertas_ext as ext


DB_FILE = r"C:\BD\Stocks.duckdb"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    data_de, data_ate = sys.argv[1], sys.argv[2]
    so_liq = "--so-liq" in sys.argv
    con = duckdb.connect(DB_FILE, read_only=True, config={"memory_limit": "2GB"})
    datas = [r[0] for r in con.execute(
        "SELECT DISTINCT data FROM Stocks_Ind WHERE CAST(data AS VARCHAR) BETWEEN ? AND ? ORDER BY 1",
        [data_de, data_ate]).fetchall()]
    con.close()

    if so_liq:
        total = 0
        for d in datas:
            with contextlib.redirect_stdout(io.StringIO()):
                n = ext.main(d, exportar=False, travar=False, so_liq=True)
            total += n or 0
            print(d, n, flush=True)
        print(f"{ext.TABELA_LIQ}: {total} sinais gravados no periodo.")
        return

    for d in datas:
        with contextlib.redirect_stdout(io.StringIO()):
            ext.main(d, exportar=False, travar=False)
        print(d, flush=True)

    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    resumo = con.execute(
        """
        SELECT estrategia, count(DISTINCT CAST(data_operacao AS VARCHAR) || ticker)
        FROM oper_abertas
        WHERE CAST(data_operacao AS VARCHAR) BETWEEN ? AND ? AND dia_hist = 1
        GROUP BY 1 ORDER BY 1
        """,
        [data_de, data_ate]).fetchall()
    linhas_metabase = ext.exportar_oper_abertas_metabase_seguro(con)
    linhas_sqlite, _ = ext.exportar_oper_abertas_sqlite_seguro(con)
    con.close()
    print(f"Sinais no periodo: {resumo}")
    print(f"Metabase: {linhas_metabase} linhas (DuckDB), {linhas_sqlite} linhas (SQLite) em oper_abertas.")


if __name__ == "__main__":
    main()
