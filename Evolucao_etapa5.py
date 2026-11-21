"""Etapa 5 (na view: modelo etapa5). Compra de call e put (delta 0.30 / -0.30) como a Etapa 1, nos tickers da view Variacoes_liq.

Sinais   view Variacoes_liq (Variacoes_liq.sql): liquidez Alta / Muito alta, amp_med20 >= 8, amp_med5 >= amp_med20, |dist_MM50%| > 25
         (historico mai/25-nov/26: ~87% tocam +-11% intradiario nos 5 pregoes seguintes).
Trava    o sinal fica gravado em TABELA_SINAIS na 1.a rodada em que aparece (com o close desse momento e a hora): se a Stocks_Ind
         do dia mudar e o ticker sair da view, o sinal continua. A entrada das opcoes (strikes, IV, valores) fica em
         Entrada_opc_travada (modelo etapa5), como nas outras etapas.
Regra    um ticker com operacao aberta nao entra de novo: os sinais seguintes do mesmo ticker so contam a partir do dia de saida
         (alvo, corte ou vencimento); no proprio dia da saida pode entrar outro sinal.
Opcoes   sem limite de IV (a IV e validada a mao na tabela Vol, que tambem mostra os tickers da etapa5).
Saida    alvo +-ALVO (11%) no ativo; sem alvo sai ao close do CORTE.o dia de pregao (D5). Retorno e lucro como no Evolucao_call_put.
ordem    ordem do sinal no dia pela amp_med20 (1 = maior amplitude); define a entrada no capital unico.
Grava    tabela Evolucao_etapa5 na DuckDB e no SQLite do Metabase (e TABELA_SINAIS / Entrada_opc_travada).
"""
import sqlite3
from datetime import date, datetime

import duckdb
import pandas as pd

import Opcoes_base as base
import Evolucao_call_put as cp


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE
MODELO = "etapa5"
TABELA_DESTINO = "Evolucao_etapa5"
TABELA_SINAIS = "Sinais_etapa5"
ALVO = 0.11
CORTE = 5

COLUNAS = cp.COLUNAS + base.INDICADORES + ["amp_med20", "amp_med5"]


def sinais_view(con):
    """Sinais da view Variacoes_liq com o close, a amplitude e a ordem do dia (pela amp_med20)."""
    return con.execute(
        """
        WITH s AS (
            SELECT trim(ticker) AS ticker, CAST(data AS VARCHAR) AS d, TRY_CAST(close AS DOUBLE) AS close,
                   (Max - Min) / LAG(close) OVER (PARTITION BY trim(ticker) ORDER BY data) * 100 AS amp
            FROM Stocks_Ind
            WHERE trim(ticker) IN (SELECT DISTINCT ticker FROM Variacoes_liq)
        ), a AS (
            SELECT ticker, d, close,
                   AVG(amp) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amp_med20,
                   AVG(amp) OVER (PARTITION BY ticker ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amp_med5
            FROM s
        )
        SELECT replace(v.data, '-', '') AS dataoperacao, v.ticker, a.close AS close_oper,
               ROUND(a.amp_med20, 2) AS amp_med20, ROUND(a.amp_med5, 2) AS amp_med5
        FROM Variacoes_liq v
        JOIN a ON a.ticker = v.ticker AND a.d = replace(v.data, '-', '')
        """
    ).fetchdf()


def atualizar_sinais(con):
    """Acrescenta a TABELA_SINAIS os sinais novos da view (nunca apaga): os de hoje ficam com a hora e o close desta rodada."""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABELA_SINAIS} (
            dataoperacao VARCHAR, ticker VARCHAR, hora VARCHAR, close_oper DOUBLE, amp_med20 DOUBLE, amp_med5 DOUBLE,
            gravado_em VARCHAR
        )
        """
    )
    v = sinais_view(con)
    ja = {tuple(r) for r in con.execute(f"SELECT dataoperacao, ticker FROM {TABELA_SINAIS}").fetchall()}
    novos = v[[(d, t) not in ja for d, t in zip(v.dataoperacao, v.ticker)]].copy()
    if not novos.empty:
        agora = datetime.now()
        hoje = base.ymd(date.today())
        novos["hora"] = [agora.strftime("%H:%M") if d == hoje else None for d in novos.dataoperacao]
        novos["gravado_em"] = agora.strftime("%Y-%m-%d %H:%M")
        con.register("df_novos", novos)
        con.execute(f"INSERT INTO {TABELA_SINAIS} BY NAME SELECT * FROM df_novos")
        con.unregister("df_novos")
        print(f"{TABELA_SINAIS}: +{len(novos)} sinais ({', '.join(sorted(novos.ticker.unique()))})")
    s = con.execute(f"SELECT * FROM {TABELA_SINAIS} ORDER BY dataoperacao, ticker").fetchdf()
    s["ordem_selecao_dia"] = s.groupby("dataoperacao").amp_med20.rank(ascending=False, method="first").astype(int)
    s["estrategia"] = "variacoes_liq"
    return s


def avaliar_sem_repetir(con, sinais, px, opc):
    """Avalia os sinais por ticker em ordem de data; enquanto um ticker tem operacao aberta os sinais dele nao entram.
    Sinais de hoje gravam a entrada das opcoes em Entrada_opc_travada (1.a rodada); as rodadas seguintes reutilizam-na."""
    travas = base.carregar_travas(con, MODELO)
    hoje = base.ymd(date.today())
    linhas, novas = [], []
    for ticker, g in sinais.sort_values(["ticker", "dataoperacao"]).groupby("ticker"):
        ocupado_ate = ""
        for op in g.itertuples():
            if op.dataoperacao < ocupado_ate:
                continue
            trava = travas.get((op.dataoperacao, op.ticker, op.hora))
            r = cp.avaliar(op, px.get(ticker), opc.get(ticker.upper()), trava, alvo_pct=ALVO, corte_du=CORTE)
            e = r.pop("_entrada", None)
            if e is not None and trava is None and op.dataoperacao == hoje and op.hora:
                novas.append(dict(e, dataoperacao=op.dataoperacao, ticker=op.ticker, hora=op.hora, close_oper=op.close_oper))
            if r.get("status") in cp.STATUS_FECHADAS + ["aberta"]:
                ocupado_ate = str(r["fim_capital"])
            r["amp_med20"], r["amp_med5"] = op.amp_med20, op.amp_med5
            linhas.append(r)
    gravadas = base.gravar_travas(con, MODELO, novas)
    if gravadas:
        print(f"{MODELO}: {gravadas} entradas travadas ({', '.join(n['ticker'] for n in novas)})")
    return pd.DataFrame(linhas)


def construir(con):
    sinais = atualizar_sinais(con)
    desde = sinais.dataoperacao.min()
    tickers = sinais.ticker.unique()
    px = base.carregar_precos(con, tickers, desde)
    opc = base.carregar_opcoes(con, tickers, base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    df = avaliar_sem_repetir(con, sinais, px, opc)
    df = base.juntar_indicadores(con, cp.marcar_capital_unico(df)).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status", "liquidez"]:
        df[col] = df[col].astype("object")
    con.register("df_etapa5", df)
    con.execute(f"""CREATE OR REPLACE TABLE {TABELA_DESTINO} AS
        SELECT * REPLACE (CAST(hora AS VARCHAR) AS hora, CAST(SAIDA_data AS VARCHAR) AS SAIDA_data, CAST(venc_put AS VARCHAR) AS venc_put)
        FROM df_etapa5 ORDER BY dataoperacao DESC, ticker""")
    con.unregister("df_etapa5")
    return df


def sincronizar_sqlite(con):
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        for t in [TABELA_DESTINO, TABELA_SINAIS]:
            con.execute(f"SELECT * FROM {t}").fetchdf().to_sql(t, sq, if_exists="replace", index=False)
        travas = con.execute(f"SELECT * FROM {base.TABELA_TRAVAS} ORDER BY dataoperacao DESC, modelo, ticker").fetchdf()
        travas.to_sql(base.TABELA_TRAVAS, sq, if_exists="replace", index=False)
    finally:
        sq.close()


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    df = construir(con)
    sincronizar_sqlite(con)
    print(f"Tabela {TABELA_DESTINO} criada com {len(df)} linhas.")
    cp.resumo(df)
    con.close()


if __name__ == "__main__":
    main()
