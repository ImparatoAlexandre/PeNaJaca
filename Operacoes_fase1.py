# =========================
# OPERACOES FASE 1
# Consulta Stocks_Ind ja atualizada
# =========================
import os
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pandas as pd

with open(r"C:\DataMarket\TESTE_TASK.txt", "a", encoding="utf-8") as f:
    f.write(f"Task executou -> {datetime.now()}\n")

# =========================
# CONFIGURACAO
# =========================
DATA_INICIO_FILTRO = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
DATA_FIM_FILTRO = datetime.now().strftime("%Y%m%d")

OUTPUT_DIR = r"C:\DataMarket"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DB_FILE = r"C:\BD\Stocks.duckdb"
LOG_FILE = r"C:\DataMarket\FINAL_STATUS.txt"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

COLUNAS_FASE1 = [
    "Stock_ID", "ticker", "data", "Open", "Max", "Min", "close", "volume", "dis.vl",
    "And_10", "Z_10d", "ADF10", "KP10", "RL10", "RL15",
    "close-max", "close-min", "max-min", "close-max%", "close-min%", "max-min%",
]

COLUNAS_SQL = ", ".join([f'"{c}"' if any(ch in c for ch in ".-%") else c for c in COLUNAS_FASE1])


def buscar_operacoes(con):
    query = f"""
        WITH base AS (
            SELECT
                *,
                ROUND(TRY_CAST(KP10 AS DOUBLE) * 100) AS kp10_cent,
                LAG(ROUND(TRY_CAST(KP10 AS DOUBLE) * 100), 1) OVER (PARTITION BY ticker ORDER BY data) AS kp10_ontem,
                LAG(ROUND(TRY_CAST(KP10 AS DOUBLE) * 100), 2) OVER (PARTITION BY ticker ORDER BY data) AS kp10_anteontem,
                LAG(ROUND(TRY_CAST(KP10 AS DOUBLE) * 100), 3) OVER (PARTITION BY ticker ORDER BY data) AS kp10_antes_de_anteontem
            FROM Stocks_Ind
        )
        SELECT {COLUNAS_SQL}
        FROM base
        WHERE data BETWEEN ? AND ?
          AND kp10_cent < 10
          AND kp10_ontem = 10
          AND kp10_anteontem = 10
          AND kp10_antes_de_anteontem = 10
        ORDER BY data, ticker
    """
    return con.execute(query, [DATA_INICIO_FILTRO, DATA_FIM_FILTRO]).fetchdf()


def montar_resultado_visual(con, operacoes_df):
    if operacoes_df.empty:
        return pd.DataFrame({"mensagem": ["Nenhum ativo atendeu no periodo"]})

    con.register("operacoes_tmp", operacoes_df[["ticker", "data"]])
    query = f"""
        WITH hist AS (
            SELECT
                s.*,
                ROW_NUMBER() OVER (
                    PARTITION BY o.ticker, o.data
                    ORDER BY s.data DESC
                ) AS rn_desc,
                o.data AS data_operacao
            FROM operacoes_tmp o
            JOIN Stocks_Ind s
              ON s.ticker = o.ticker
             AND s.data <= o.data
        )
        SELECT {COLUNAS_SQL}, data_operacao
        FROM hist
        WHERE rn_desc <= 4
        ORDER BY data_operacao, ticker, data
    """
    historico = con.execute(query).fetchdf()

    resultados_com_espaco = []
    for _, grupo in historico.groupby(["ticker", "data_operacao"], sort=False):
        if resultados_com_espaco:
            resultados_com_espaco.append(pd.DataFrame([[np.nan] * len(grupo.columns)], columns=grupo.columns))
        resultados_com_espaco.append(grupo.drop(columns=["data_operacao"]))

    return pd.concat(resultados_com_espaco, ignore_index=True)


def gravar_oper_fase1(con, operacoes_df):
    if operacoes_df.empty:
        return 0

    con.register("oper_fase1_df", operacoes_df)
    con.execute("CREATE TABLE IF NOT EXISTS oper_fase1 AS SELECT * FROM oper_fase1_df WHERE 1=0")
    con.execute("CREATE TEMP TABLE ids_oper_fase1 AS SELECT Stock_ID FROM oper_fase1_df")
    con.execute("DELETE FROM oper_fase1 WHERE Stock_ID IN (SELECT Stock_ID FROM ids_oper_fase1)")
    con.execute("INSERT INTO oper_fase1 BY NAME SELECT * FROM oper_fase1_df")
    return len(operacoes_df)


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})

    try:
        operacoes_df = buscar_operacoes(con)
        final = montar_resultado_visual(con, operacoes_df)
        linhas_gravadas = gravar_oper_fase1(con, operacoes_df)
    finally:
        con.close()

    print("\n================ RESULTADO FASE 1 ================\n")
    print(f"Periodo: {DATA_INICIO_FILTRO} ate {DATA_FIM_FILTRO}")
    print(f"Operacoes encontradas: {linhas_gravadas}")
    print(final.to_string(index=False))
    print("\n==================================================\n")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now()} -> terminou Operacoes_fase1 via Stocks_Ind "
            f"periodo {DATA_INICIO_FILTRO}-{DATA_FIM_FILTRO}; operacoes {linhas_gravadas}\n"
        )


if __name__ == "__main__":
    main()
