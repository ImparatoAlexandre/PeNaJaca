# =========================
# EVOLUCAO
# =========================
# Materializa a visao de evolucao das operacoes EXT em tabela DuckDB
# e sincroniza a mesma tabela para o SQLite usado pelo Metabase.

from pathlib import Path
from datetime import datetime

import duckdb
import sqlite3

DB_FILE = Path(r"C:\BD\Stocks.duckdb")
SQLITE_FILE = Path(r"C:\Metabase\data\visualizacao.sqlite")
TABELA_SAIDA = "Evolucao"
DATA_INICIO = "20260102"

QUERY_EVOLUCAO = f"""
CREATE OR REPLACE TABLE {TABELA_SAIDA} AS
WITH operacoes AS (
    SELECT
        a.data_operacao,
        a.ordem_selecao_dia,
        a.ticker,
        a.Stock_ID,
        a.perspectiva_operacao AS perspectiva,
        CAST(a.close AS DOUBLE) AS close_operacao
    FROM oper_abertas a
    WHERE lower(trim(a.estrategia)) = 'ext'
      AND CAST(a.dia_hist AS INTEGER) = 1
      AND CAST(a.data_operacao AS VARCHAR) >= '{DATA_INICIO}'
),

futuros AS (
    SELECT
        o.Stock_ID,
        s.data,
        CAST(s.Open AS DOUBLE) AS open_dia,
        CAST(s.close AS DOUBLE) AS close_dia,
        CAST(s.Max AS DOUBLE) AS max_dia,
        CAST(s.Min AS DOUBLE) AS min_dia,
        ROW_NUMBER() OVER (
            PARTITION BY o.Stock_ID
            ORDER BY CAST(s.data AS INTEGER)
        ) AS dia_pos
    FROM operacoes o
    JOIN Stocks_Ind s
      ON s.ticker = o.ticker
     AND CAST(s.data AS INTEGER) > CAST(o.data_operacao AS INTEGER)
),

calc_futuros AS (
    SELECT
        o.Stock_ID,

        ROUND(MAX(CASE
            WHEN f.dia_pos = 1
            THEN ((f.open_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS Abertura,

        ROUND(MIN(CASE
            WHEN f.dia_pos <= 7
            THEN ((f.min_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS "MIN",

        ROUND(MAX(CASE
            WHEN f.dia_pos <= 7
            THEN ((f.max_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS "MAX",
        ROUND(MAX(CASE
            WHEN f.dia_pos <= 2
            THEN ((f.max_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS maxD2,

        ROUND(MIN(CASE
            WHEN f.dia_pos <= 2
            THEN ((f.min_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS minD2,

        ROUND(MAX(CASE
            WHEN f.dia_pos <= 5
            THEN ((f.max_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS maxD5,

        ROUND(MIN(CASE
            WHEN f.dia_pos <= 5
            THEN ((f.min_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS minD5,

        ROUND(MAX(CASE
            WHEN f.dia_pos = 5
            THEN ((f.close_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS D5,

        ROUND(MAX(CASE
            WHEN f.dia_pos <= 7
            THEN ((f.max_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS maxD7,

        ROUND(MIN(CASE
            WHEN f.dia_pos <= 7
            THEN ((f.min_dia - o.close_operacao) / o.close_operacao) * 100
        END), 2) AS minD7

        ,ROUND(MAX(CASE
            WHEN f.dia_pos = 8 AND lower(trim(o.perspectiva)) = 'alta'
            THEN ((f.close_dia - o.close_operacao) / o.close_operacao) * 100
            WHEN f.dia_pos = 8 AND lower(trim(o.perspectiva)) = 'baixa'
            THEN ((o.close_operacao - f.close_dia) / o.close_operacao) * 100
        END), 2) AS D8

    FROM operacoes o
    LEFT JOIN futuros f
      ON trim(f.Stock_ID) = trim(o.Stock_ID)
    GROUP BY
        o.Stock_ID,
        o.close_operacao,
        o.perspectiva
)

SELECT
    o.data_operacao AS dataoper,
    o.ordem_selecao_dia AS ordem,
    o.ticker,
    l.liquidez_classificacao AS LiqOPC,
    l.ranking_liquidez AS ranking_liquidez,
    o.perspectiva AS perspec,
    o.close_operacao AS closeoperacao,
    c.Abertura,
    c.minD2,
    c.maxD2,
    c.minD5,
    c.maxD5,
    c.minD7,
    c.maxD7,
    c.D8,
    c."MIN",
    c."MAX"
FROM operacoes o
LEFT JOIN Stocks_OPC_liquidez l
  ON trim(l.ticker) = trim(o.ticker)
LEFT JOIN calc_futuros c
  ON trim(c.Stock_ID) = trim(o.Stock_ID)
ORDER BY
    CAST(o.data_operacao AS INTEGER) DESC,
    CAST(o.ordem_selecao_dia AS INTEGER) ASC,
    o.ticker ASC
"""


def sincronizar_sqlite(df):
    SQLITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    con_sqlite = sqlite3.connect(str(SQLITE_FILE))
    try:
        df.to_sql(TABELA_SAIDA, con_sqlite, if_exists="replace", index=False)
    finally:
        con_sqlite.close()


def main():
    print(f"{datetime.now()} -> INICIO Evolucao.py")
    con = duckdb.connect(str(DB_FILE))
    try:
        con.execute(QUERY_EVOLUCAO)
        df = con.execute(f"SELECT * FROM {TABELA_SAIDA}").fetchdf()
    finally:
        con.close()

    sincronizar_sqlite(df)

    print(f"Tabela {TABELA_SAIDA} criada no DuckDB com {len(df)} linhas desde {DATA_INICIO}.")
    print(f"Tabela {TABELA_SAIDA} sincronizada no SQLite do Metabase.")
    if not df.empty:
        print(df.head(20).to_string(index=False))
    print(f"{datetime.now()} -> FIM Evolucao.py")


if __name__ == "__main__":
    main()














