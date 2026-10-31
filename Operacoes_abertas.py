# =========================
# OPERACOES ABERTAS
# =========================
import os
import duckdb
import pandas as pd
from datetime import datetime

DB_FILE = r"C:\BD\Stocks.duckdb"
LOG_FILE = r"C:\DataMarket\FINAL_STATUS.txt"

METABASE_DB_FILE = r"C:\Metabase\data\visualizacao.duckdb"
METABASE_TABLE = "oper_abertas"
SQLITE_DB_FILE = r"C:\Metabase\data\visualizacao.sqlite"
SQLITE_TABLE = "oper_abertas"
LIQUIDEZ_VISIVEL = ("Alta", "Muito alta")  # liquidez do Evolucao_stock (stock na view): fase2 com esta liquidez ficam travados no dia

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 10000)


def exportar_oper_abertas_metabase(con):
    os.makedirs(os.path.dirname(METABASE_DB_FILE), exist_ok=True)
    df_meta = con.execute("""
        SELECT
            data_operacao,
            "Time",
            hora,
            perspectiva_operacao,
            estrategia,
            ordem_selecao_dia,
            close,
            * EXCLUDE (data_operacao, "Time", hora, perspectiva_operacao, estrategia, ordem_selecao_dia, close)
        FROM oper_abertas
        ORDER BY data_operacao DESC, ticker, data
        """).fetchdf()

    con_meta = duckdb.connect(METABASE_DB_FILE)
    try:
        con_meta.register("df_oper_abertas", df_meta)
        con_meta.execute(f'DROP TABLE IF EXISTS "{METABASE_TABLE}"')
        con_meta.execute(f'CREATE TABLE "{METABASE_TABLE}" AS SELECT * FROM df_oper_abertas')
    finally:
        con_meta.close()

    return len(df_meta)


def exportar_oper_abertas_metabase_seguro(con):
    try:
        return exportar_oper_abertas_metabase(con)
    except Exception as e:
        with open(r"C:\DataMarket\ERROR_METABASE.txt", "a", encoding="utf-8") as f:
            f.write(f"Operacoes_abertas Metabase -> {datetime.now()} -> {str(e)}\n")
        print(f"Erro ao atualizar DuckDB do Metabase: {e}")
        return 0




def exportar_oper_abertas_sqlite(con):
    os.makedirs(os.path.dirname(SQLITE_DB_FILE), exist_ok=True)
    df_oper_abertas = con.execute("""
        SELECT
            data_operacao,
            "Time",
            hora,
            perspectiva_operacao,
            estrategia,
            ordem_selecao_dia,
            close,
            * EXCLUDE (data_operacao, "Time", hora, perspectiva_operacao, estrategia, ordem_selecao_dia, close)
        FROM oper_abertas
        ORDER BY data_operacao DESC, ticker, data
        """).fetchdf()
    df_stocks_ind = con.execute("""
        SELECT *
        FROM Stocks_Ind
        ORDER BY data DESC, ticker
        """).fetchdf()

    import sqlite3
    con_sqlite = sqlite3.connect(SQLITE_DB_FILE)
    try:
        df_oper_abertas.to_sql(SQLITE_TABLE, con_sqlite, if_exists="replace", index=False)
        df_stocks_ind.to_sql("Stocks_Ind", con_sqlite, if_exists="replace", index=False)
    finally:
        con_sqlite.close()

    return len(df_oper_abertas), len(df_stocks_ind)


def exportar_oper_abertas_sqlite_seguro(con):
    try:
        return exportar_oper_abertas_sqlite(con)
    except Exception as e:
        with open(r"C:\DataMarket\ERROR_METABASE.txt", "a", encoding="utf-8") as f:
            f.write(f"Operacoes_abertas SQLite -> {datetime.now()} -> {str(e)}\n")
        print(f"Erro ao atualizar SQLite do Metabase: {e}")
        return 0, 0


def garantir_colunas_oper_abertas(con):
    cols_oper = {r[1]: r[2] for r in con.execute("PRAGMA table_info('oper_abertas')").fetchall()}
    cols_stocks = {r[1]: r[2] for r in con.execute("PRAGMA table_info('Stocks_Ind')").fetchall()}
    for nome, tipo in cols_stocks.items():
        if nome not in cols_oper:
            con.execute(f'ALTER TABLE oper_abertas ADD COLUMN "{nome}" {tipo}')
            cols_oper[nome] = tipo
    if "Time" not in cols_oper:
        con.execute('ALTER TABLE oper_abertas ADD COLUMN "Time" VARCHAR')
    if "hora" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN hora VARCHAR")
        con.execute("""
            UPDATE oper_abertas SET hora = substr("Time", 1, 2) || ':' || substr("Time", 3, 2)
            WHERE regexp_matches(CAST("Time" AS VARCHAR), '^[0-9]{4}$')
        """)
    if "estrategia" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN estrategia VARCHAR")
        cols_oper["estrategia"] = "VARCHAR"
    if "oper_aberta_id" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN oper_aberta_id VARCHAR")
    if "ordem_selecao_dia" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN ordem_selecao_dia INTEGER")

def tickers_travados(con, data_operacao):
    """fase2 do dia com liquidez em LIQUIDEZ_VISIVEL (podem estar na view): ficam com close e hora da 1.a selecao."""
    lista = ",".join(f"'{x}'" for x in LIQUIDEZ_VISIVEL)
    return {r[0] for r in con.execute(
        f"""
        SELECT DISTINCT trim(op.ticker)
        FROM oper_abertas op
        JOIN Stocks_OPC_liquidez l ON trim(l.ticker) = trim(op.ticker)
        WHERE op.estrategia = 'fase2'
          AND CAST(op.data_operacao AS VARCHAR) = CAST(? AS VARCHAR)
          AND l.liquidez_classificacao IN ({lista})
        """,
        [data_operacao],
    ).fetchall()}


def main(data_operacao=None, exportar=True, travar=True):
    """data_operacao: data a processar (None = ultima). exportar=False: nao exporta para o Metabase nem escreve o log (reprocessamento).
    travar=False: regrava tambem os fase2 do dia ja visiveis (sem manter close e hora da 1.a selecao)."""
    con = duckdb.connect(DB_FILE)
    agora = datetime.now()
    hora_execucao = agora.strftime("%H%M")
    hora_minuto = agora.strftime("%H:%M")

    if data_operacao is None:
        data_operacao = con.execute(
            """
            SELECT MAX(data)
            FROM oper_fase2
            WHERE lower(trim(status)) = 'operacao'
            """
        ).fetchone()[0]

    if data_operacao is None:
        print("Nenhuma operacao encontrada em oper_fase2.")
        con.close()
        return

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS oper_abertas AS
        SELECT
            CAST(NULL AS VARCHAR) AS data_operacao,
            CAST(NULL AS VARCHAR) AS "Time",
            CAST(NULL AS VARCHAR) AS perspectiva_operacao,
            CAST(NULL AS VARCHAR) AS estrategia,
            s.close,
            CAST(NULL AS DOUBLE) AS variacao_close,
            CAST(NULL AS INTEGER) AS dia_hist,
            CAST(NULL AS INTEGER) AS ordem_selecao_dia,
            s.* EXCLUDE (close),
            CAST(NULL AS VARCHAR) AS oper_aberta_id
        FROM Stocks_Ind s
        WHERE 1=0
        """
    )
    cols_oper_abertas = [r[1] for r in con.execute("PRAGMA table_info('oper_abertas')").fetchall()]
    if "Time" not in cols_oper_abertas:
        con.execute('ALTER TABLE oper_abertas ADD COLUMN "Time" VARCHAR')
        con.execute('UPDATE oper_abertas SET "Time" = ''1600'' WHERE "Time" IS NULL')
    if "estrategia" not in cols_oper_abertas:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN estrategia VARCHAR")
        con.execute("UPDATE oper_abertas SET estrategia = CASE WHEN starts_with(CAST(oper_aberta_id AS VARCHAR), 'EXT_') THEN 'ext' ELSE 'fase2' END WHERE estrategia IS NULL")
    if "oper_aberta_id" not in cols_oper_abertas:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN oper_aberta_id VARCHAR")
    if "ordem_selecao_dia" not in cols_oper_abertas:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN ordem_selecao_dia INTEGER")
        con.execute("""
            UPDATE oper_abertas
            SET oper_aberta_id = CAST(data_operacao AS VARCHAR) || '_' || CAST(Stock_ID AS VARCHAR) || '_' || CAST(dia_hist AS VARCHAR)
        """)
    garantir_colunas_oper_abertas(con)  # hora tem de existir antes da exportacao (tambem sem ativos no filtro)

    filtrados = con.execute(
        """
        WITH ops AS (
            SELECT
                f2.*,
                lower(trim(f2.perspectiva)) AS tendencia,
                TRY_CAST(f2.close AS DOUBLE) AS close_op,
                TRY_CAST(f2.ADF10 AS DOUBLE) AS adf10_op,
                TRY_CAST(f2.KP10 AS DOUBLE) AS kp10_op,
                TRY_CAST(f2.Z_10d AS DOUBLE) AS z10_op,
                TRY_CAST(f2.volume AS DOUBLE) AS volume_op,
                TRY_CAST(f2."close-min%" AS DOUBLE) AS close_min_pct,
                TRY_CAST(f2."close-max%" AS DOUBLE) AS close_max_pct,
                TRY_CAST(f2.y_estimado AS DOUBLE) AS y_estimado_op,
                ROUND(
                    100.0 * (TRY_CAST(f2.close AS DOUBLE) - TRY_CAST(f2.y_estimado AS DOUBLE))
                    / NULLIF(TRY_CAST(f2.close AS DOUBLE), 0),
                    2
                ) AS close_y_pct
            FROM oper_fase2 f2
            WHERE lower(trim(f2.status)) = 'operacao'
              AND f2.data = ?
        ), prev AS (
            SELECT
                ops.*,
                p.close_ontem,
                v.volume_avg5_antes,
                100.0 * (ops.close_op - p.close_ontem) / NULLIF(p.close_ontem, 0) AS variacao_close
            FROM ops
            LEFT JOIN LATERAL (
                SELECT TRY_CAST(s.close AS DOUBLE) AS close_ontem
                FROM Stocks_Ind s
                WHERE s.ticker = ops.ticker
                  AND s.data < ops.data
                ORDER BY s.data DESC
                LIMIT 1
            ) p ON TRUE
            LEFT JOIN LATERAL (
                SELECT AVG(volume_5) AS volume_avg5_antes
                FROM (
                    SELECT TRY_CAST(s.volume AS DOUBLE) AS volume_5
                    FROM Stocks_Ind s
                    WHERE s.ticker = ops.ticker
                      AND s.data < ops.data
                    ORDER BY s.data DESC
                    LIMIT 5
                ) ultimos_5
            ) v ON TRUE
        )
        SELECT
            *,
            ROW_NUMBER() OVER (ORDER BY ticker, Stock_ID) AS rn_dia
        FROM prev
        WHERE
            CASE
                WHEN tendencia = 'alta' THEN (
                    ROUND(kp10_op * 100) < 9 AND
                    adf10_op != 0 AND
                    adf10_op != 1 AND
                    ABS(z10_op) < 1.2 AND
                    close_min_pct >= 2.0 AND
                    volume_avg5_antes IS NOT NULL AND
                    volume_op > volume_avg5_antes * 0.90
                )
                WHEN tendencia = 'baixa' THEN (
                    ROUND(kp10_op * 100) < 9 AND
                    adf10_op != 0 AND
                    adf10_op != 1 AND
                    ABS(z10_op) < 1.2 AND
                    close_max_pct <= -2.5 AND
                    close_y_pct >= -6.0
                )
                ELSE FALSE
            END
        ORDER BY ticker
        """,
        [data_operacao],
    ).fetchdf()

    if filtrados.empty:
        print(f"Data operacao: {data_operacao}")
        print("Nenhum ativo passou no filtro forte.")
        linhas_metabase = exportar_oper_abertas_metabase_seguro(con) if exportar else 0
        linhas_sqlite, linhas_stocks_sqlite = exportar_oper_abertas_sqlite_seguro(con) if exportar else (0, 0)
        print(f"DuckDB Metabase atualizado com {linhas_metabase} linhas em oper_abertas.")
        print(f"SQLite Metabase atualizado com {linhas_sqlite} linhas em oper_abertas e {linhas_stocks_sqlite} linhas em Stocks_Ind.")
        with open(LOG_FILE if exportar else os.devnull, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now()} -> Operacoes_abertas data {data_operacao}; "
                f"nenhum ativo passou no filtro; metabase {linhas_metabase}; sqlite {linhas_sqlite}; stocks_ind_sqlite {linhas_stocks_sqlite}\n"
            )
        con.close()
        return

    con.register("filtrados", filtrados)

    historico = con.execute(
        """
        WITH hist AS (
            SELECT
                f.data AS data_operacao,
                f.tendencia AS perspectiva_operacao,
                f.variacao_close,
                COALESCE(TRY_CAST(f.rn_dia AS INTEGER), 1) AS ordem_selecao_dia,
                s.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.ticker, f.data
                    ORDER BY s.data DESC
                ) AS rn_desc
            FROM filtrados f
            JOIN Stocks_Ind s
              ON s.ticker = f.ticker
             AND s.data <= f.data
        )
        SELECT
            data_operacao,
            ? AS "Time",
            ? AS hora,
            perspectiva_operacao,
            'fase2' AS estrategia,
            close,
            ROUND(variacao_close, 2) AS variacao_close,
            rn_desc AS dia_hist,
            ordem_selecao_dia,
            * EXCLUDE (data_operacao, perspectiva_operacao, variacao_close, ordem_selecao_dia, rn_desc, close),
            CAST(data_operacao AS VARCHAR) || '_' || CAST(Stock_ID AS VARCHAR) || '_' || CAST(rn_desc AS VARCHAR) AS oper_aberta_id
        FROM hist
        WHERE rn_desc <= 5
        ORDER BY ticker, data
        """,
        [hora_execucao, hora_minuto],
    ).fetchdf()

    # ticker ja visivel no dia nao e regravado: fica a linha da 1.a selecao (hora e close dessa rodada)
    travados = tickers_travados(con, data_operacao) if travar else set()
    historico = historico[~historico["ticker"].str.strip().isin(travados)]
    mantidos_txt = ",".join(sorted(travados & set(filtrados["ticker"].str.strip()))) or "-"

    con.register("historico", historico)
    con.execute("CREATE TABLE IF NOT EXISTS oper_abertas AS SELECT * FROM historico WHERE 1=0")
    garantir_colunas_oper_abertas(con)
    con.execute("CREATE TEMP TABLE ids_oper_abertas AS SELECT oper_aberta_id FROM historico")
    con.execute("DELETE FROM oper_abertas WHERE oper_aberta_id IN (SELECT oper_aberta_id FROM ids_oper_abertas)")
    con.execute("INSERT INTO oper_abertas BY NAME SELECT * FROM historico")
    linhas_metabase = exportar_oper_abertas_metabase_seguro(con) if exportar else 0
    linhas_sqlite, linhas_stocks_sqlite = exportar_oper_abertas_sqlite_seguro(con) if exportar else (0, 0)

    print("\n================ OPERACOES ABERTAS ================\n")
    print(f"Data operacao: {data_operacao}")
    print(f"Ativos filtrados: {filtrados['ticker'].nunique()}")
    print(f"Linhas gravadas em oper_abertas: {len(historico)}")
    print(f"DuckDB Metabase atualizado com {linhas_metabase} linhas em oper_abertas.")
    print(f"SQLite Metabase atualizado com {linhas_sqlite} linhas em oper_abertas e {linhas_stocks_sqlite} linhas em Stocks_Ind.")
    print("\nHISTORICO ULTIMOS 5 DIAS\n")
    print(historico.to_string(index=False))
    print("\n====================================================\n")

    with open(LOG_FILE if exportar else os.devnull, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now()} -> Operacoes_abertas data {data_operacao}; "
            f"ativos {filtrados['ticker'].nunique()}; mantidos {mantidos_txt}; linhas {len(historico)}; metabase {linhas_metabase}; sqlite {linhas_sqlite}; stocks_ind_sqlite {linhas_stocks_sqlite}\n"
        )

    con.close()


if __name__ == "__main__":
    main()



