"""Apaga da DuckDB tabelas antigas que nenhum script, view ou grafico do Metabase usa (verificado em 03/11/2026).

Uso    python Apagar_tabelas_antigas.py
       Correr uma vez em cada maquina, fora do horario das rodadas (para se a trava do Operacoes_atualizacao existir).
       O espaco libertado e reaproveitado pelos dados novos (o arquivo nao encolhe logo).
"""
import os
import sys
from pathlib import Path

import duckdb

DB_FILE = r"C:\BD\Stocks.duckdb"
LOCK_FILE = Path(__file__).resolve().parent / "Operacoes_atualizacao.lock"
TABELAS = ["Stocks_extremos_var2", "Stocks_Ind_backup_y_estimado", "stocks_ind_backup", "Stocks_Ind1", "Stocks_Ind2"]


def main():
    if LOCK_FILE.exists():
        sys.exit(f"Rodada em curso ({LOCK_FILE.name} existe) - nada apagado")
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    try:
        existentes = {r[0].lower() for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        for t in TABELAS:
            if t.lower() in existentes:
                n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                con.execute(f'DROP TABLE "{t}"')
                print(f"{t}: apagada ({n} linhas)")
            else:
                print(f"{t}: nao existe")
        con.execute("CHECKPOINT")
        livres = con.execute("PRAGMA database_size").fetchone()
    finally:
        con.close()
    print(f"Arquivo: {os.path.getsize(DB_FILE) / 1024**2:.0f} MB; {livres}")


if __name__ == "__main__":
    main()
