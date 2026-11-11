"""Etapa 3 (na view: modelo etapa3). Compra de call e put (delta 0.30 / -0.30) como a Etapa 1, nos sinais ext que tendem a mexer muito.

Sinais   oper_abertas com estrategia = "ext", dia_hist = 1 e ordem_selecao_dia <= ORDEM_MAX (3), com opcoes liquidas
         (Alta / Muito alta, mesma selecao das Etapas 1 e 2: Opcoes_base.carregar_operacoes).
Filtro   VR10 < VR10_MAX (1.3) e vol_hist20 > VOL_MIN (70%). Sem limite de IV das opcoes.
Colunas  MOM20, dist_MM50% e VR10: Stocks_Ind no dia do sinal.
         vol_hist20: vol realizada anualizada (%) dos JANELA_VOL retornos diarios ate ao dia do sinal (mesma conta do Vol_momento).
         MIN % / MAX %: menor Min e maior Max do D1 ao D7 face ao close do sinal (como a Evolucao); maior_mov % = maior entre |MIN| e |MAX|.
         liquidez: classificacao atual da Stocks_OPC_liquidez.
Entrada  saida, retorno e capital unico: iguais ao Evolucao_call_put (avaliar), com alvo ALVO_PCT (±10%) e corte ao close do
         CORTE_DU.o dia de pregao (D7); VALOR_OPERACAO no par.
Trava    entrada dos sinais de hoje em Entrada_opc_travada (modelo etapa3).
Grava    tabela Evolucao_etapa3 na DuckDB e no SQLite do Metabase.
"""
import sqlite3

import duckdb
import numpy as np
import pandas as pd

import Opcoes_base as base
import Evolucao_call_put as cp


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE
TABELA_DESTINO = "Evolucao_etapa3"
MODELO = "etapa3"

ESTRATEGIA = "ext"
ORDEM_MAX = 3
VR10_MAX = 1.3
VOL_MIN = 70.0
JANELA_VOL = 20
DIAS_MOV = 7
ALVO_PCT = 0.10
CORTE_DU = 7

INDICADORES = ["liquidez", "MOM20", "dist_MM50%", "VR10", "vol_hist20", "MIN %", "MAX %", "maior_mov %"]
COLUNAS = cp.COLUNAS + INDICADORES


def avaliar(op, px_t, opc_t, trava=None):
    return cp.avaliar(op, px_t, opc_t, trava, alvo_pct=ALVO_PCT, corte_du=CORTE_DU)


def carregar_indicadores(con, tickers, desde):
    inicio = (pd.Timestamp(desde) - pd.Timedelta(days=60)).strftime("%Y%m%d")
    px = con.execute(
        """
        SELECT CAST(data AS VARCHAR) AS d, trim(ticker) AS ticker, TRY_CAST(close AS DOUBLE) AS c,
               TRY_CAST(Max AS DOUBLE) AS h, TRY_CAST(Min AS DOUBLE) AS l,
               TRY_CAST(MOM20 AS DOUBLE) AS mom20, TRY_CAST("dist_MM50%" AS DOUBLE) AS dmm50, TRY_CAST(VR10 AS DOUBLE) AS vr10
        FROM Stocks_Ind
        WHERE CAST(data AS VARCHAR) >= ? AND trim(ticker) IN (SELECT unnest(?))
        """, [inicio, list(tickers)]).fetchdf()
    return {t: g.drop_duplicates("d", keep="last").set_index("d").sort_index() for t, g in px.groupby("ticker")}


def indicadores(op, p):
    vazio = dict.fromkeys(INDICADORES[1:], np.nan)
    if p is None or op.dataoperacao not in p.index or not op.close_oper == op.close_oper:
        return vazio
    s = p.loc[op.dataoperacao]
    hist = p.c[p.index <= op.dataoperacao].tail(JANELA_VOL + 1)
    vol = float(np.log(hist).diff().std() * np.sqrt(252) * 100) if len(hist) > JANELA_VOL else np.nan
    fut = p[p.index > op.dataoperacao].iloc[:DIAS_MOV]
    c0 = float(op.close_oper)
    mn = (fut.l.min() / c0 - 1) * 100 if len(fut) else np.nan
    mx = (fut.h.max() / c0 - 1) * 100 if len(fut) else np.nan
    return {"MOM20": s.mom20, "dist_MM50%": s.dmm50, "VR10": s.vr10, "vol_hist20": vol, "MIN %": mn, "MAX %": mx,
            "maior_mov %": max(abs(mn), abs(mx)) if len(fut) else np.nan}


def construir(con):
    base.ESTRATEGIA, base.ORDEM_MAX = ESTRATEGIA, ORDEM_MAX
    ops = base.carregar_operacoes(con)
    desde = ops.dataoperacao.min()
    ind_px = carregar_indicadores(con, ops.ticker.unique(), desde)
    ind = pd.DataFrame([indicadores(op, ind_px.get(op.ticker)) for op in ops.itertuples()], index=ops.index)
    sel = ops[(ind.VR10 < VR10_MAX) & (ind.vol_hist20 > VOL_MIN)].reset_index(drop=True)
    ind = ind[(ind.VR10 < VR10_MAX) & (ind.vol_hist20 > VOL_MIN)].reset_index(drop=True)

    px = base.carregar_precos(con, sel.ticker.unique(), desde)
    opc = base.carregar_opcoes(con, sel.ticker.unique(), base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    df = base.avaliar_com_travas(con, MODELO, sel, avaliar, px, opc)
    for col in INDICADORES[1:]:
        df[col] = ind[col].round(2).values
    liq = con.execute("SELECT trim(ticker) AS t, any_value(liquidez_classificacao) AS l FROM Stocks_OPC_liquidez GROUP BY 1").fetchdf()
    df["liquidez"] = df.ticker.map(liq.set_index("t").l)
    df = cp.marcar_capital_unico(df).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status", "liquidez"]:
        df[col] = df[col].astype("object")
    con.register("df_etapa3", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_etapa3 ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_etapa3")
    return df


def sincronizar_sqlite(con):
    df = con.execute(f"SELECT * FROM {TABELA_DESTINO}").fetchdf()
    travas = con.execute(f"SELECT * FROM {base.TABELA_TRAVAS} ORDER BY dataoperacao DESC, modelo, ticker").fetchdf()
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
        travas.to_sql(base.TABELA_TRAVAS, sq, if_exists="replace", index=False)
    finally:
        sq.close()
    return len(df)


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    df = construir(con)
    qtd = sincronizar_sqlite(con)
    print(f"Tabela {TABELA_DESTINO} criada com {qtd} linhas.")
    cp.resumo(df)
    con.close()


if __name__ == "__main__":
    main()
