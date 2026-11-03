"""Volatilidade do momento dos sinais ext: tabela Vol (DuckDB e SQLite do Metabase).

Ativos   sinais ext (oper_abertas, dia_hist = 1) com ordem_selecao_dia <= ORDEM_MAX e liquidez em LIQUIDEZ,
         das ultimas DIAS_SINAIS datas de operacao.
Opcoes   cadeia lida no yfinance no momento em que o script corre (nao usa Call_Put_dados), no 1.o vencimento
         (sexta-feira) aceite por Opcoes_base.venc_valido (>= 10 dias uteis), como nos modelos:
           - call ATM: strike mais proximo do preco atual;
           - call delta 0.30 e put delta -0.30: as pernas do Evolucao_call_put. O delta usa a IV do contrato; se for
             invalida (sem bid/ask ou < IV_MINIMA), usa a IV mediana valida da cadeia (strikes a +-10%) ou a vol
             realizada de 20 dias (fora do horario do mercado).
         vol_* = volatilidade implicita (%) do contrato. Fora do horario do mercado o yfinance devolve IV pouco fiavel.
         vol_hist20 = volatilidade historica do ativo (%, inteiro): desvio dos retornos dos ultimos 20 fechamentos da
         Stocks_Ind, anualizado (mesma escala das vol_*; vol_hist20 / vol_call_atm = rv20/IV).
Grava    tabela Vol substituida a cada execucao (corre no fim do Operacoes_atualizacao), na DuckDB e no SQLite;
         cada execucao tambem e acrescentada a Vol_hist (historico por data_hora).
Uso      python Vol_momento.py
"""
import sqlite3
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import yfinance as yf

from Opcoes_base import delta_bs, dias_uteis, venc_valido


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA_DESTINO = "Vol"
TABELA_HIST = "Vol_hist"

ORDEM_MAX = 3
LIQUIDEZ = ("Muito alta", "Alta", "Media")
DIAS_SINAIS = 5      # ultimas N datas de operacao com sinais ext
DELTA_CALL = 0.30
DELTA_PUT = -0.30
IV_MINIMA = 0.10     # IV abaixo disto (ou sem bid/ask) e invalida: o delta usa a IV mediana valida da cadeia (ou a rv20)
JANELA_RV = 20

COLUNAS = [
    "data_hora", "dataoperacao", "ordem", "ticker", "vol_hist20", "liquidez", "venc", "dias_uteis",
    "strike_call_atm", "vol_call_atm", "strike_call_delta30", "vol_call_delta30", "strike_put_delta30", "vol_put_delta30",
]


def carregar_sinais(con):
    lista = ",".join(f"'{x}'" for x in LIQUIDEZ)
    return con.execute(
        f"""
        WITH datas AS (
            SELECT DISTINCT CAST(data_operacao AS VARCHAR) AS d
            FROM oper_abertas
            WHERE estrategia = 'ext'
            ORDER BY 1 DESC
            LIMIT {DIAS_SINAIS}
        )
        SELECT
            CAST(op.data_operacao AS VARCHAR) AS dataoperacao,
            MIN(TRY_CAST(op.ordem_selecao_dia AS INTEGER)) AS ordem,
            trim(op.ticker) AS ticker,
            any_value(l.liquidez_classificacao) AS liquidez
        FROM oper_abertas AS op
        INNER JOIN Stocks_OPC_liquidez AS l
            ON trim(l.ticker) = trim(op.ticker)
        WHERE op.estrategia = 'ext'
          AND TRY_CAST(op.dia_hist AS INTEGER) = 1
          AND CAST(op.data_operacao AS VARCHAR) IN (SELECT d FROM datas)
          AND l.liquidez_classificacao IN ({lista})
        GROUP BY 1, 3
        HAVING MIN(TRY_CAST(op.ordem_selecao_dia AS INTEGER)) <= {ORDEM_MAX}
        ORDER BY 1 DESC, 2, 3
        """
    ).fetchdf()


def preco_momento(tk):
    hist = tk.history(period="1d", interval="5m", auto_adjust=False)
    if hist.empty:
        hist = tk.history(period="5d", auto_adjust=False)
    if hist.empty:
        return None
    return float(hist["Close"].dropna().iloc[-1])


def primeiro_vencimento(tk, hoje):
    for venc in sorted(tk.options):
        d = datetime.strptime(venc, "%Y-%m-%d").date()
        if venc_valido(hoje, d):
            return d
    return None


def vol_realizada(con, ticker):
    closes = [r[0] for r in con.execute(
        "SELECT TRY_CAST(close AS DOUBLE) FROM Stocks_Ind WHERE trim(ticker) = ? ORDER BY CAST(data AS VARCHAR) DESC LIMIT ?",
        [ticker, JANELA_RV + 1]).fetchall()]
    return float(np.log(pd.Series(closes[::-1])).diff().std() * np.sqrt(252)) if len(closes) > JANELA_RV else None


def iv_valida(chain):
    """IV so conta com cotacao real (bid e ask > 0); fora do horario o yfinance devolve IV ~0 ou fixa (12.5%, 25%)."""
    return (chain.bid.fillna(0) > 0) & (chain.ask.fillna(0) > 0) & (chain.impliedVolatility >= IV_MINIMA)


def vol_referencia(calls, puts, spot, rv20):
    """IV para o delta quando a do contrato e invalida: mediana das IV validas a +-10% do preco, ou a rv20."""
    c = pd.concat([calls[iv_valida(calls)], puts[iv_valida(puts)]])
    c = c[(c.strike / spot - 1).abs() <= 0.10]
    return float(c.impliedVolatility.median()) if not c.empty else rv20


def por_delta(chain, spot, du, alvo, tipo, vol_ref):
    c = chain.copy()
    c["vol_delta"] = c.impliedVolatility.where(iv_valida(c), vol_ref)
    c = c.dropna(subset=["vol_delta"])
    if c.empty:
        return None
    c["delta"] = [delta_bs(spot, k, du, v, tipo) for k, v in zip(c.strike, c.vol_delta)]
    c = c.dropna(subset=["delta"])
    if c.empty:
        return None
    return c.loc[(c.delta - alvo).abs().idxmin()]


def vol_ticker(ticker, hoje, rv20):
    """Leitura do momento para um ticker (independente da data do sinal)."""
    tk = yf.Ticker(ticker)
    spot = preco_momento(tk)
    venc = primeiro_vencimento(tk, hoje) if spot is not None else None
    if venc is None:
        return {}
    du = dias_uteis(hoje, venc)
    ch = tk.option_chain(venc.isoformat())
    atm = ch.calls.loc[(ch.calls.strike - spot).abs().idxmin()]
    vol_ref = vol_referencia(ch.calls, ch.puts, spot, rv20)
    c30 = por_delta(ch.calls, spot, du, DELTA_CALL, "CALL", vol_ref)
    p30 = por_delta(ch.puts, spot, du, DELTA_PUT, "PUT", vol_ref)
    pct = lambda v: round(float(v) * 100, 1)
    return {
        "venc": venc.isoformat(), "dias_uteis": du,
        "strike_call_atm": float(atm.strike), "vol_call_atm": pct(atm.impliedVolatility),
        "strike_call_delta30": float(c30.strike) if c30 is not None else None,
        "vol_call_delta30": pct(c30.impliedVolatility) if c30 is not None else None,
        "strike_put_delta30": float(p30.strike) if p30 is not None else None,
        "vol_put_delta30": pct(p30.impliedVolatility) if p30 is not None else None,
    }


def construir(con):
    agora = datetime.now()
    sinais = carregar_sinais(con)
    leituras = {}
    for i, t in enumerate(sinais.ticker.unique(), start=1):
        rv20 = vol_realizada(con, t)
        try:
            leituras[t] = vol_ticker(t, agora.date(), rv20)
        except Exception as exc:
            leituras[t] = {}
            print(f"{t}: erro {exc}")
        leituras[t]["vol_hist20"] = int(round(rv20 * 100)) if rv20 is not None and rv20 == rv20 else None
        print(f"[{i}/{sinais.ticker.nunique()}] {t}: vol_call_atm={leituras[t].get('vol_call_atm')}", flush=True)
    linhas = [{"data_hora": agora.strftime("%Y-%m-%d %H:%M"), "dataoperacao": s.dataoperacao, "ordem": s.ordem,
               "ticker": s.ticker, "liquidez": s.liquidez, **leituras[s.ticker]} for s in sinais.itertuples()]
    df = pd.DataFrame(linhas).reindex(columns=COLUNAS)
    con.register("df_vol", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_vol")
    con.execute(f"CREATE TABLE IF NOT EXISTS {TABELA_HIST} AS SELECT * FROM df_vol WHERE 1=0")
    con.execute(f"ALTER TABLE {TABELA_HIST} ADD COLUMN IF NOT EXISTS vol_hist20 INTEGER")
    con.execute(f"INSERT INTO {TABELA_HIST} BY NAME SELECT * FROM df_vol")
    con.unregister("df_vol")
    df_hist = con.execute(f"SELECT {', '.join(COLUNAS)} FROM {TABELA_HIST} ORDER BY data_hora DESC, dataoperacao DESC, ordem, ticker").fetchdf()
    return df, df_hist


def sincronizar_sqlite(df, df_hist):
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
        df_hist.to_sql(TABELA_HIST, sq, if_exists="replace", index=False)
    finally:
        sq.close()


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    df, df_hist = construir(con)
    con.close()
    sincronizar_sqlite(df, df_hist)
    print(f"Tabela {TABELA_DESTINO} criada com {len(df)} linhas ({df.ticker.nunique()} tickers).")


if __name__ == "__main__":
    main()
