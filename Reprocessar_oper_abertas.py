"""Reprocessa oper_abertas (ext e fase2) para um periodo passado, dia a dia.

Corre Operacoes_abertas_ext.main e Operacoes_abertas.main para cada data de Stocks_Ind no periodo,
sem exportar a cada dia; no fim exporta oper_abertas uma vez para o Metabase (DuckDB e SQLite).
Datas que ja tem fase2 sincronizado (Time = 'SYNC') sao saltadas na fase2 para nao duplicar.
Os ext e fase2 do dia sao refeitos todos (travar=False): os travados de rodadas durante o dia nao sao mantidos.

Uso: python Reprocessar_oper_abertas.py AAAAMMDD AAAAMMDD
"""
import contextlib
import io
import sys

import duckdb

import Operacoes_abertas as fase2
import Operacoes_abertas_ext as ext


DB_FILE = r"C:\BD\Stocks.duckdb"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    data_de, data_ate = sys.argv[1], sys.argv[2]
    con = duckdb.connect(DB_FILE, read_only=True, config={"memory_limit": "2GB"})
    datas = [r[0] for r in con.execute(
        "SELECT DISTINCT data FROM Stocks_Ind WHERE CAST(data AS VARCHAR) BETWEEN ? AND ? ORDER BY 1",
        [data_de, data_ate]).fetchall()]
    sync = {r[0] for r in con.execute(
        "SELECT DISTINCT CAST(data_operacao AS VARCHAR) FROM oper_abertas WHERE estrategia = 'fase2' AND \"Time\" = 'SYNC'"
    ).fetchall()}
    con.close()

    for d in datas:
        with contextlib.redirect_stdout(io.StringIO()):
            ext.main(d, exportar=False, travar=False)
            if str(d) not in sync:
                fase2.main(d, exportar=False, travar=False)
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
