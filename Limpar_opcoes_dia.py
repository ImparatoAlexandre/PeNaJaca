"""Apaga as leituras de opcoes de um dia (ex.: rodada manual de fim de semana, cotacoes paradas).

Apaga  Call_Put_dados (DataRegistro = dia) e Vol_hist (data_hora do dia), na DuckDB e no SQLite do Metabase;
       depois refaz Volume_OPC_strike (termometro sem esse dia).
Uso    python Limpar_opcoes_dia.py 20261101
       Correr fora do horario das rodadas (para se a trava do Operacoes_atualizacao existir).
"""
import sqlite3
import sys
from pathlib import Path

import duckdb

import Volume_OPC_strike

DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
LOCK_FILE = Path(__file__).resolve().parent / "Operacoes_atualizacao.lock"


def main():
    if len(sys.argv) != 2 or not (sys.argv[1].isdigit() and len(sys.argv[1]) == 8):
        sys.exit("Uso: python Limpar_opcoes_dia.py AAAAMMDD")
    if LOCK_FILE.exists():
        sys.exit(f"Rodada em curso ({LOCK_FILE.name} existe) - nada apagado")
    dia = sys.argv[1]
    dia_hora = f"{dia[:4]}-{dia[4:6]}-{dia[6:]}%"

    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    try:
        n_cp = con.execute("SELECT count(*) FROM Call_Put_dados WHERE DataRegistro = ?", [dia]).fetchone()[0]
        con.execute("DELETE FROM Call_Put_dados WHERE DataRegistro = ?", [dia])
        n_vh = con.execute("SELECT count(*) FROM Vol_hist WHERE CAST(data_hora AS VARCHAR) LIKE ?", [dia_hora]).fetchone()[0]
        con.execute("DELETE FROM Vol_hist WHERE CAST(data_hora AS VARCHAR) LIKE ?", [dia_hora])
    finally:
        con.close()
    print(f"DuckDB: Call_Put_dados -{n_cp}, Vol_hist -{n_vh}")

    sq = sqlite3.connect(SQLITE_FILE)
    try:
        s_cp = sq.execute("DELETE FROM Call_Put_dados WHERE DataRegistro = ?", [dia]).rowcount
        s_vh = sq.execute("DELETE FROM Vol_hist WHERE data_hora LIKE ?", [dia_hora]).rowcount
        sq.commit()
    finally:
        sq.close()
    print(f"SQLite: Call_Put_dados -{s_cp}, Vol_hist -{s_vh}")

    Volume_OPC_strike.main()


if __name__ == "__main__":
    main()
