"""Posicao no momento da captura das operacoes da view Evolucao_put_operacoes (mesmas linhas e modelos).

Ativo    close_atual = close da ultima linha do ticker em Stocks_Ind; Var%ativo = close_atual / close_oper - 1 (%).
Opcoes   ultima captura do ticker em Call_Put_dados, mesmo tipo, vencimento e strike da operacao:
           valor = meio entre bid e ask (sem bid/ask: lastPrice); vol = impliedVolatility em %.
         Contrato fora da ultima captura do ticker (vencido ou sem coleta): valor e vol vazios.
Retorno  retorno% no momento, vendendo agora as opcoes compradas na entrada (mesma conta do modelo):
           call_put e vol_call_put = par 1 call + 1 put: (call_atual + put_atual) / (valor_call + valor_put) - 1;
           stock = Var%ativo.
Prazo    dias_vencimento = dias uteis da data da captura ate ao vencimento (como no modelo), minimo 0.
Grava    tabela Posicao_momento na DuckDB e no SQLite do Metabase (substituida a cada execucao).
Historico as operacoes abertas de cada execucao sao acrescentadas a Posicao_momento_hist com data_hora (AAAA-MM-DD HH:MM).
"""
import sqlite3
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA_DESTINO = "Posicao_momento"
TABELA_HIST = "Posicao_momento_hist"

COLUNAS = [
    "modelo", "dataoperacao", "hora", "ticker", "sinal", "close_oper", "close_atual", "dias_vencimento", "Var%ativo", "retorno%",
    "valor_call_atual", "vol_call_atual", "valor_put_atual", "vol_put_atual", "data_captura",
]


def carregar_operacoes(con):
    return con.execute(
        """
        SELECT modelo, dataoperacao, hora, ticker, sinal, status, close_oper, strike_call, valor_call, strike_put, valor_put, venc_put
        FROM Evolucao_put_operacoes
        ORDER BY modelo, dataoperacao DESC, ticker
        """
    ).fetchdf()


def carregar_precos(con):
    return con.execute(
        """
        SELECT trim(ticker) AS ticker, CAST(data AS VARCHAR) AS data_preco, TRY_CAST(close AS DOUBLE) AS close_atual
        FROM Stocks_Ind
        QUALIFY ROW_NUMBER() OVER (PARTITION BY trim(ticker) ORDER BY CAST(data AS VARCHAR) DESC) = 1
        """
    ).fetchdf()


def carregar_opcoes(con, tickers):
    """Contratos da ultima captura de cada ticker."""
    lista = ",".join("'" + t.upper().replace("'", "''") + "'" for t in tickers)
    df = con.execute(
        f"""
        WITH ultima AS (
            SELECT upper(trim(ticker)) AS ticker, MAX(CAST(DataRegistro AS VARCHAR)) AS dr
            FROM Call_Put_dados
            WHERE upper(trim(ticker)) IN ({lista})
            GROUP BY 1
        )
        SELECT upper(trim(c.ticker)) AS ticker, upper(trim(c.tipo)) AS tipo, CAST(c.dataStrike AS DATE) AS venc,
               TRY_CAST(c.strike AS DOUBLE) AS strike, u.dr AS data_captura,
               MAX(TRY_CAST(c.lastPrice AS DOUBLE)) AS lp, MAX(TRY_CAST(c.bid AS DOUBLE)) AS bid,
               MAX(TRY_CAST(c.ask AS DOUBLE)) AS ask, MAX(TRY_CAST(c.impliedVolatility AS DOUBLE)) AS iv
        FROM Call_Put_dados c
        JOIN ultima u ON upper(trim(c.ticker)) = u.ticker AND CAST(c.DataRegistro AS VARCHAR) = u.dr
        GROUP BY ALL
        """
    ).fetchdf()
    df["venc"] = pd.to_datetime(df.venc).dt.date
    df["valor"] = np.where((df.bid > 0) & (df.ask > 0), (df.bid + df.ask) / 2, df.lp)
    df["vol"] = np.where(df.iv > 5, df.iv, df.iv * 100)  # algumas linhas gravam a IV em %
    return df


def perna(ops, opc, tipo, col_strike):
    chave = pd.DataFrame({
        "ticker": ops.ticker.str.strip().str.upper(), "tipo": tipo,
        "venc": pd.to_datetime(ops.venc_put).dt.date, "strike": pd.to_numeric(ops[col_strike], errors="coerce"),
    })
    r = chave.merge(opc[["ticker", "tipo", "venc", "strike", "valor", "vol"]], how="left",
                    on=["ticker", "tipo", "venc", "strike"])
    return r.valor.round(2).values, r.vol.round(1).values


def construir(con):
    ops = carregar_operacoes(con)
    px = carregar_precos(con).set_index("ticker")
    ops["close_atual"] = ops.ticker.str.strip().map(px.close_atual)
    ops["data_preco"] = ops.ticker.str.strip().map(px.data_preco)
    opc = carregar_opcoes(con, ops.ticker.str.strip().unique())
    capturas = opc.groupby("ticker").data_captura.max()

    ops["valor_call_atual"], ops["vol_call_atual"] = perna(ops, opc, "CALL", "strike_call")
    ops["valor_put_atual"], ops["vol_put_atual"] = perna(ops, opc, "PUT", "strike_put")
    ops["data_captura"] = ops.ticker.str.strip().str.upper().map(capturas).fillna(ops.data_preco)
    ops["Var%ativo"] = ((ops.close_atual / ops.close_oper - 1) * 100).round(2)
    ret_call_put = (ops.valor_call_atual + ops.valor_put_atual) / (ops.valor_call + ops.valor_put) - 1
    ops["retorno%"] = np.where(ops.modelo.isin(["call_put", "vol_call_put"]), ret_call_put * 100, ops["Var%ativo"]).round(2)

    venc = pd.to_datetime(ops.venc_put, errors="coerce")
    captura = pd.to_datetime(ops.data_captura, format="%Y%m%d", errors="coerce")
    ok = venc.notna() & captura.notna()
    du = np.busday_count(captura[ok].values.astype("datetime64[D]") + 1, venc[ok].values.astype("datetime64[D]") + 1)
    ops["dias_vencimento"] = pd.Series(np.nan, index=ops.index)
    ops.loc[ok, "dias_vencimento"] = np.maximum(du, 0)
    ops["dias_vencimento"] = ops.dias_vencimento.astype("Int64")

    df = ops.reindex(columns=COLUNAS)
    con.register("df_posicao", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_posicao")
    con.unregister("df_posicao")

    hist = ops[ops.status == "aberta"].reindex(columns=COLUNAS)
    hist.insert(0, "data_hora", datetime.now().strftime("%Y-%m-%d %H:%M"))
    con.register("df_hist", hist)
    con.execute(f"CREATE TABLE IF NOT EXISTS {TABELA_HIST} AS SELECT * FROM df_hist WHERE 1=0")
    con.execute(f"INSERT INTO {TABELA_HIST} BY NAME SELECT * FROM df_hist")
    con.unregister("df_hist")
    df_hist = con.execute(f"SELECT * FROM {TABELA_HIST} ORDER BY data_hora DESC, modelo, ticker").fetchdf()
    return df, df_hist


def sincronizar_sqlite(df, df_hist):
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
        df_hist.to_sql(TABELA_HIST, sq, if_exists="replace", index=False)
    finally:
        sq.close()
    return len(df)


def main():
    con = duckdb.connect(DB_FILE)
    try:
        df, df_hist = construir(con)
    finally:
        con.close()
    qtd = sincronizar_sqlite(df, df_hist)
    com_opcao = df.valor_call_atual.notna().sum() + df.valor_put_atual.notna().sum()
    print(f"{datetime.now()} Tabela {TABELA_DESTINO} criada com {qtd} linhas ({com_opcao} pernas com preco atual).")


if __name__ == "__main__":
    main()
