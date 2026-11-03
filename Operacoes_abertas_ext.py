# =========================
# OPERACOES ABERTAS EXTREMO
# Consulta Stocks_Ind diretamente com a regra extrema e grava em oper_abertas
# =========================
import os
from datetime import datetime

import duckdb
import pandas as pd

DB_FILE = r"C:\BD\Stocks.duckdb"
LOG_FILE = r"C:\DataMarket\FINAL_STATUS.txt"

METABASE_DB_FILE = r"C:\Metabase\data\visualizacao.duckdb"
METABASE_TABLE = "oper_abertas"
SQLITE_DB_FILE = r"C:\Metabase\data\visualizacao.sqlite"
SQLITE_TABLE = "oper_abertas"
ORDEM_VISIVEL = 3  # mesma ORDEM_MAX do Evolucao_vol_call_put (o maior dos modelos): ext com ordem <= isto ficam travados no dia

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

    import sqlite3
    con_sqlite = sqlite3.connect(SQLITE_DB_FILE)
    try:
        df_oper_abertas.to_sql(SQLITE_TABLE, con_sqlite, if_exists="replace", index=False)
    finally:
        con_sqlite.close()

    # Stocks_Ind vai para o SQLite so na sincronizacao final do Operacoes_atualizacao (menos regravacoes com o
    # Metabase a ler o arquivo)
    return len(df_oper_abertas), "-"


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
    if "ranking_liquidez_opcoes" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN ranking_liquidez_opcoes BIGINT")
    if "volume_opcoes" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN volume_opcoes BIGINT")
    if "rank_mm50_dia" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN rank_mm50_dia INTEGER")
    if "mercado_tendencia" not in cols_oper:
        con.execute("ALTER TABLE oper_abertas ADD COLUMN mercado_tendencia VARCHAR")


def tendencia_mercado(con, data_operacao):
    """'alta' se a media igual-ponderada dos tickers com opcoes liquidas tem MM10 > MM50 na data; senao 'baixa'."""
    px = con.execute(
        """
        SELECT trim(s.ticker) AS ticker, CAST(s.data AS VARCHAR) AS d, TRY_CAST(s.close AS DOUBLE) AS c
        FROM Stocks_Ind s
        JOIN Stocks_OPC_liquidez l ON trim(l.ticker) = trim(s.ticker)
        WHERE l.liquidez_classificacao IN ('Alta', 'Muito alta')
          AND CAST(s.data AS VARCHAR) <= CAST(? AS VARCHAR)
          AND CAST(s.data AS VARCHAR) >= strftime(strptime(CAST(? AS VARCHAR), '%Y%m%d') - INTERVAL 120 DAY, '%Y%m%d')
        """,
        [data_operacao, data_operacao],
    ).fetchdf()
    if px.empty:
        return None
    serie = px.pivot(index="d", columns="ticker", values="c").sort_index()
    indice = (1 + serie.pct_change().mean(axis=1).fillna(0)).cumprod()
    if len(indice) < 50:
        return None
    return "alta" if indice.rolling(10).mean().iloc[-1] > indice.rolling(50).mean().iloc[-1] else "baixa"


def marcar_rank_mm50(con, filtrados):
    """1 = sinal do dia mais acima da sua MM50, entre os tickers com opcoes liquidas (os outros ficam sem rank)."""
    liquidos = {r[0] for r in con.execute(
        "SELECT trim(ticker) FROM Stocks_OPC_liquidez WHERE liquidez_classificacao IN ('Alta', 'Muito alta')"
    ).fetchall()}
    dist = pd.to_numeric(filtrados.get("dist_MM50%"), errors="coerce") if "dist_MM50%" in filtrados else pd.Series(index=filtrados.index, dtype=float)
    elegivel = filtrados["ticker"].str.strip().isin(liquidos) & dist.notna()
    rank = dist.where(elegivel).groupby(filtrados["data"]).rank(ascending=False, method="first")
    filtrados["rank_mm50_dia"] = rank.astype("Int64")
    return filtrados


def limpar_ext_dia(con, data_operacao, travar):
    """Apaga os ext do dia, menos os ja visiveis em Evolucao_put_operacoes (travados); devolve os tickers travados.

    Visivel = ordem_selecao_dia <= ORDEM_VISIVEL e opcoes com liquidez Alta/Muito alta (selecao do Evolucao_stock e dos call_put).
    Assim uma rodada durante o dia nao tira o que ja foi selecionado; nas rodadas seguintes so entram tickers novos.
    travar=False: apaga todos os ext do dia (reprocessamento).
    """
    travados = set()
    if travar:
        travados = {r[0] for r in con.execute(
            """
            SELECT DISTINCT trim(op.ticker)
            FROM oper_abertas op
            JOIN Stocks_OPC_liquidez l ON trim(l.ticker) = trim(op.ticker)
            WHERE op.estrategia = 'ext'
              AND CAST(op.data_operacao AS VARCHAR) = CAST(? AS VARCHAR)
              AND TRY_CAST(op.ordem_selecao_dia AS INTEGER) <= ?
              AND l.liquidez_classificacao IN ('Alta', 'Muito alta')
            """,
            [data_operacao, ORDEM_VISIVEL],
        ).fetchall()}
    con.execute(
        f"""
        DELETE FROM oper_abertas
        WHERE estrategia = 'ext'
          AND CAST(data_operacao AS VARCHAR) = CAST(? AS VARCHAR)
          {"AND trim(ticker) NOT IN (SELECT unnest(?::VARCHAR[]))" if travados else ""}
        """,
        [data_operacao, sorted(travados)] if travados else [data_operacao],
    )
    return travados


def main(data_operacao=None, exportar=True, travar=True):
    """data_operacao: data a processar (None = ultima). exportar=False: nao exporta para o Metabase nem escreve o log (reprocessamento).
    travar=False: refaz todos os ext do dia (sem manter os ja visiveis)."""
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    agora = datetime.now()
    hora_execucao = agora.strftime("%H%M")
    hora_minuto = agora.strftime("%H:%M")

    if data_operacao is None:
        data_operacao = con.execute("SELECT MAX(data) FROM Stocks_Ind").fetchone()[0]
    if data_operacao is None:
        print("Nenhuma data encontrada em Stocks_Ind.")
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
    garantir_colunas_oper_abertas(con)

    filtrados = con.execute(
        """
        WITH base AS (
            SELECT
                s.*,
                TRY_CAST(s.close AS DOUBLE) AS close_hoje,
                LAG(TRY_CAST(s.close AS DOUBLE)) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                ) AS close_ontem,
                100.0 * (TRY_CAST(s.close AS DOUBLE) - LAG(TRY_CAST(s.close AS DOUBLE)) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                )) / NULLIF(LAG(TRY_CAST(s.close AS DOUBLE)) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                ), 0) AS variacao_close,
                100.0 * (LAG(TRY_CAST(s.close AS DOUBLE)) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                ) - LAG(TRY_CAST(s.close AS DOUBLE), 2) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                )) / NULLIF(LAG(TRY_CAST(s.close AS DOUBLE), 2) OVER (
                    PARTITION BY s.ticker
                    ORDER BY STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d')
                ), 0) AS variacao_close_d2_d1,
                100.0 * (TRY_CAST(s.Max AS DOUBLE) - TRY_CAST(s.Min AS DOUBLE)) / NULLIF(TRY_CAST(s.close AS DOUBLE), 0) AS amplitude_candle,
                100.0 * ABS(TRY_CAST(s.close AS DOUBLE) - TRY_CAST(s.Open AS DOUBLE)) / NULLIF(TRY_CAST(s.close AS DOUBLE), 0) AS corpo_candle,
                100.0 * (TRY_CAST(s.close AS DOUBLE) - TRY_CAST(s.y_estimado AS DOUBLE)) / NULLIF(TRY_CAST(s.close AS DOUBLE), 0) AS close_y_pct
            FROM Stocks_Ind s
            WHERE STRPTIME(CAST(s.data AS VARCHAR), '%Y%m%d') IS NOT NULL
        ), extremos AS (
            SELECT
                base.*,
                CASE
                    WHEN TRY_CAST(Z_10d AS DOUBLE) >= 1.2
                     AND variacao_close >= 0
                     AND amplitude_candle >= 5
                        THEN 'ALTA_FORTE'
                    WHEN close_hoje < close_ontem
                     AND variacao_close <= -5
                     AND amplitude_candle >= 7
                     AND TRY_CAST(KP10 AS DOUBLE) <= 0.07
                     AND TRY_CAST(And_10 AS DOUBLE) <= 0.60
                     AND TRY_CAST(ADF10 AS DOUBLE) <= 0.98
                     AND TRY_CAST(Z_10d AS DOUBLE) <= -1.2
                     AND close_y_pct <= -4
                     AND TRY_CAST(volume AS DOUBLE) > (
                            SELECT AVG(TRY_CAST(v.volume AS DOUBLE)) * 1.20
                            FROM (
                                SELECT volume
                                FROM Stocks_Ind v
                                WHERE v.ticker = base.ticker
                                  AND STRPTIME(CAST(v.data AS VARCHAR), '%Y%m%d') < STRPTIME(CAST(base.data AS VARCHAR), '%Y%m%d')
                                ORDER BY STRPTIME(CAST(v.data AS VARCHAR), '%Y%m%d') DESC
                                LIMIT 5
                            ) v
                         )
                        THEN 'BAIXA_FORTE'
                    ELSE NULL
                END AS movimento,
                CASE
                    WHEN close_hoje > close_ontem THEN 'Alta'
                    WHEN close_hoje < close_ontem THEN 'Baixa'
                    ELSE NULL
                END AS tendencia
            FROM base
            WHERE close_ontem IS NOT NULL
              AND close_hoje IS NOT NULL
              AND data = ?
        ), com_perspectiva AS (
            SELECT
                *,
                CASE
                    WHEN TRY_CAST(close AS DOUBLE) > TRY_CAST(Open AS DOUBLE)
                     AND close_hoje > close_ontem
                     AND TRY_CAST("close-min%" AS DOUBLE) >= 2.0
                     AND TRY_CAST("close-max%" AS DOUBLE) > -1.5
                        THEN 'alta'
                    WHEN TRY_CAST(close AS DOUBLE) < TRY_CAST(Open AS DOUBLE)
                     AND close_hoje < close_ontem
                     AND TRY_CAST("close-max%" AS DOUBLE) <= -2.5
                     AND TRY_CAST("close-min%" AS DOUBLE) < 4.0
                        THEN 'baixa'
                    WHEN tendencia = 'Alta'
                     AND close_y_pct > 4
                     AND TRY_CAST(RL15 AS DOUBLE) < 0
                        THEN 'baixa'
                    WHEN tendencia = 'Baixa'
                     AND close_y_pct < -4
                     AND TRY_CAST(RL15 AS DOUBLE) > 0
                        THEN 'alta'
                    WHEN tendencia = 'Alta'
                     AND TRY_CAST("close-max%" AS DOUBLE) > -1.5
                     AND TRY_CAST("close-min%" AS DOUBLE) < 2.0
                        THEN 'baixa'
                    WHEN tendencia = 'Alta'
                        THEN 'alta'
                    WHEN tendencia = 'Baixa'
                     AND TRY_CAST("close-min%" AS DOUBLE) < 1.0
                        THEN 'alta'
                    WHEN tendencia = 'Baixa'
                        THEN 'baixa'
                    ELSE NULL
                END AS perspectiva
            FROM extremos
            WHERE movimento IS NOT NULL
              AND NOT (
                  movimento = 'ALTA_FORTE'
                  AND TRY_CAST("close-max%" AS DOUBLE) > -0.5
                  AND TRY_CAST("close-min%" AS DOUBLE) >= 5.0
              )
        ), candidatos_com_liquidez AS (
            SELECT
                c.*,
                CASE
                    WHEN c.perspectiva = 'alta' THEN c.variacao_close_d2_d1
                    WHEN c.perspectiva = 'baixa' THEN -c.variacao_close_d2_d1
                    ELSE NULL
                END AS variacao_favoravel_d2_d1,
                CASE
                    WHEN c.perspectiva = 'alta'
                     AND c.close_hoje > c.close_ontem
                     AND TRY_CAST(c.close AS DOUBLE) > TRY_CAST(c.Open AS DOUBLE)
                        THEN 1
                    WHEN c.perspectiva = 'baixa'
                     AND c.close_hoje < c.close_ontem
                     AND TRY_CAST(c.close AS DOUBLE) < TRY_CAST(c.Open AS DOUBLE)
                        THEN 1
                    ELSE 0
                END AS candle_confirma,
                l.ranking_liquidez AS ranking_liquidez_opcoes,
                l.volume_total_opcoes AS volume_opcoes
            FROM com_perspectiva c
            LEFT JOIN Stocks_OPC_liquidez l
              ON l.ticker = c.ticker
        ), uma_operacao_por_dia AS (
            -- perspectiva e so coluna informativa: nao filtra nem entra na ordem do dia
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY data
                        ORDER BY
                            amplitude_candle DESC,
                            ticker,
                            Stock_ID
                    ) AS rn_dia
                FROM candidatos_com_liquidez
            )
            WHERE rn_dia <= 3
        )
        SELECT *
        FROM uma_operacao_por_dia
        ORDER BY rn_dia, ticker
        """,
        [data_operacao],
    ).fetchdf()

    travados = limpar_ext_dia(con, data_operacao, travar)
    mantidos_txt = ",".join(sorted(travados)) or "-"

    if filtrados.empty:
        print(f"Data operacao: {data_operacao}")
        print(f"Nenhum ativo extremo passou no filtro. Mantidos do dia: {mantidos_txt}")
        linhas_metabase = exportar_oper_abertas_metabase_seguro(con) if exportar else 0
        linhas_sqlite, linhas_stocks_sqlite = exportar_oper_abertas_sqlite_seguro(con) if exportar else (0, 0)
        print(f"DuckDB Metabase atualizado com {linhas_metabase} linhas em oper_abertas.")
        print(f"SQLite Metabase atualizado com {linhas_sqlite} linhas em oper_abertas e {linhas_stocks_sqlite} linhas em Stocks_Ind.")
        with open(LOG_FILE if exportar else os.devnull, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now()} -> Operacoes_abertas_ext data {data_operacao}; "
                f"nenhum ativo extremo passou no filtro; mantidos {mantidos_txt}; metabase {linhas_metabase}; sqlite {linhas_sqlite}; stocks_ind_sqlite {linhas_stocks_sqlite}\n"
            )
        con.close()
        return

    filtrados = marcar_rank_mm50(con, filtrados)
    filtrados["mercado_tendencia"] = tendencia_mercado(con, data_operacao)
    con.register("filtrados", filtrados)

    historico = con.execute(
        """
        WITH hist AS (
            SELECT
                f.data AS data_operacao,
                f.perspectiva AS perspectiva_operacao,
                f.variacao_close,
                COALESCE(TRY_CAST(f.rn_dia AS INTEGER), 1) AS ordem_selecao_dia,
                f.ranking_liquidez_opcoes,
                f.volume_opcoes,
                f.rank_mm50_dia,
                f.mercado_tendencia,
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
            'ext' AS estrategia,
            close,
            ROUND(variacao_close, 2) AS variacao_close,
            rn_desc AS dia_hist,
            ordem_selecao_dia,
            ranking_liquidez_opcoes,
            volume_opcoes,
            CASE WHEN rn_desc = 1 THEN rank_mm50_dia END AS rank_mm50_dia,
            mercado_tendencia,
            * EXCLUDE (data_operacao, perspectiva_operacao, variacao_close, ordem_selecao_dia, ranking_liquidez_opcoes, volume_opcoes, rank_mm50_dia, mercado_tendencia, rn_desc, close),
            'EXT_' || CAST(data_operacao AS VARCHAR) || '_' || CAST(Stock_ID AS VARCHAR) || '_' || CAST(rn_desc AS VARCHAR) AS oper_aberta_id
        FROM hist
        WHERE rn_desc <= 5
        ORDER BY ticker, data
        """,
        [hora_execucao, hora_minuto],
    ).fetchdf()

    # ticker ja visivel no dia nao entra de novo: fica a linha da 1.a selecao (hora e close dessa rodada)
    historico = historico[~historico["ticker"].str.strip().isin(travados)]
    novos = filtrados[~filtrados["ticker"].str.strip().isin(travados)]
    novos_txt = ",".join(f"{t.strip()}({int(o)})" for t, o in zip(novos["ticker"], novos["rn_dia"])) or "-"

    con.register("historico", historico)
    con.execute("CREATE TABLE IF NOT EXISTS oper_abertas AS SELECT * FROM historico WHERE 1=0")
    garantir_colunas_oper_abertas(con)
    con.execute("INSERT INTO oper_abertas BY NAME SELECT * FROM historico")
    linhas_metabase = exportar_oper_abertas_metabase_seguro(con) if exportar else 0
    linhas_sqlite, linhas_stocks_sqlite = exportar_oper_abertas_sqlite_seguro(con) if exportar else (0, 0)

    print("\n================ OPERACOES ABERTAS EXTREMO ================\n")
    print(f"Data operacao: {data_operacao}")
    print(f"Ativos extremos filtrados: {filtrados['ticker'].nunique()}")
    print(f"Novos (ordem): {novos_txt}; mantidos do dia: {mantidos_txt}")
    print(f"Linhas gravadas em oper_abertas: {len(historico)}")
    print(f"DuckDB Metabase atualizado com {linhas_metabase} linhas em oper_abertas.")
    print(f"SQLite Metabase atualizado com {linhas_sqlite} linhas em oper_abertas e {linhas_stocks_sqlite} linhas em Stocks_Ind.")
    print("\nHISTORICO ULTIMOS 5 DIAS\n")
    print(historico.to_string(index=False))
    print("\n============================================================\n")

    con.close()

    with open(LOG_FILE if exportar else os.devnull, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now()} -> Operacoes_abertas_ext data {data_operacao}; "
            f"ativos {filtrados['ticker'].nunique()}; novos {novos_txt}; mantidos {mantidos_txt}; linhas {len(historico)}; metabase {linhas_metabase}; sqlite {linhas_sqlite}; stocks_ind_sqlite {linhas_stocks_sqlite}\n"
        )



if __name__ == "__main__":
    main()
















