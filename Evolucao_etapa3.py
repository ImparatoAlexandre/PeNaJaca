"""Etapas 3 e 4 (na view: modelos etapa3 e etapa4). Compra de call e put (delta 0.30 / -0.30) como a Etapa 1, nos sinais ext
que tendem a mexer muito. A etapa3 e a regra segura (compor capital), a etapa4 a ousada.

Sinais   oper_abertas com estrategia = "ext", dia_hist = 1 e ordem_selecao_dia <= ORDEM_MAX (3), com opcoes liquidas
         (Alta / Muito alta, mesma selecao das Etapas 1 e 2: Opcoes_base.carregar_operacoes).
Filtro   VR10 < VR10_MAX (1.3) e vol_hist20 > VOL_MIN (70%), comum as duas etapas.
Regras   (MODELOS) IV de cada perna (call e put) em ]iv_min, iv_max], alvo ±alvo no ativo, corte ao close do corte.o dia de pregao:
           etapa3 (segura): IV 60-80%, ±7%, D5   -> historico out/25-out/26: 54 sinais, acerto 80%, pior -19%
           etapa4 (ousada): IV <= 100%, ±10%, D5 -> historico: 133 sinais, acerto 83%, pior -65%
         (2026-11-11; antes a etapa3 era ±10%, D7, sem limite de IV.) Sinais sem opcoes ficam na tabela (a view nao os mostra).
Colunas  (Opcoes_base.calcular_indicadores, tambem usadas nas etapas 1 e 2) MOM20, dist_MM50% e VR10: Stocks_Ind no dia do sinal.
         vol_hist20: vol realizada anualizada (%) dos JANELA_VOL retornos diarios ate ao dia do sinal (mesma conta do Vol_momento).
         MIN % / MAX %: menor Min e maior Max do D1 ao D7 face ao close do sinal (como a Evolucao); maior_mov % = maior entre |MIN| e |MAX|.
         liquidez: classificacao atual da Stocks_OPC_liquidez.
Entrada  saida, retorno e capital unico: iguais ao Evolucao_call_put (avaliar), com VALOR_OPERACAO no par.
Trava    entrada dos sinais de hoje em Entrada_opc_travada (modelo etapa3 / etapa4).
Grava    tabelas Evolucao_etapa3 e Evolucao_etapa4 na DuckDB e no SQLite do Metabase.
Opcoes   ordens 2 e 3 antes de set/26 sem cadeia real: completadas com Completar_opcoes_BS.py --tabela=Evolucao_etapa3 (precos BS).
"""
import sqlite3

import duckdb
import numpy as np
import pandas as pd

import Opcoes_base as base
import Evolucao_call_put as cp


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE

ESTRATEGIA = "ext"
ORDEM_MAX = 3
VR10_MAX = 1.3
VOL_MIN = 70.0

MODELOS = {
    "etapa3": dict(tabela="Evolucao_etapa3", iv_min=0.60, iv_max=0.80, alvo=0.07, corte=5),
    "etapa4": dict(tabela="Evolucao_etapa4", iv_min=None, iv_max=1.00, alvo=0.10, corte=5),
}

INDICADORES = base.INDICADORES  # calculados no Opcoes_base (comuns as etapas 1 a 4)
COLUNAS = cp.COLUNAS + INDICADORES


def carregar_sinais(con):
    """Sinais ext ordem 1-3 com VR10 < VR10_MAX e vol_hist20 > VOL_MIN, com os indicadores."""
    base.ESTRATEGIA, base.ORDEM_MAX = ESTRATEGIA, ORDEM_MAX
    ops = base.carregar_operacoes(con)
    desde = ops.dataoperacao.min()
    ind = base.calcular_indicadores(con, ops)
    ok = (ind.VR10 < VR10_MAX) & (ind.vol_hist20 > VOL_MIN)
    return ops[ok].reset_index(drop=True), ind[ok].reset_index(drop=True), desde


def iv_na_faixa(df, iv_min, iv_max):
    """Fica o sinal com IV da call e da put em ]iv_min, iv_max] (sinais sem opcoes ficam, a view nao os mostra)."""
    if "_iv_max" not in df:
        return pd.Series(True, index=df.index)
    iv_c, iv_p = df.vol_call.astype(float), df.vol_put.astype(float)
    fora = (df._iv_max > iv_max) if iv_max else pd.Series(False, index=df.index)
    if iv_min:
        fora |= (iv_c <= iv_min) | (iv_p <= iv_min)
    return ~fora.fillna(False)


def construir(con, modelo, regra, sel, ind, px, opc):
    def avaliar(op, px_t, opc_t, trava=None):
        return cp.avaliar(op, px_t, opc_t, trava, alvo_pct=regra["alvo"], corte_du=regra["corte"])

    df = base.avaliar_com_travas(con, modelo, sel, avaliar, px, opc)
    for col in INDICADORES:
        df[col] = ind[col].round(2).values if col != "liquidez" else ind[col].values
    df = df[iv_na_faixa(df, regra["iv_min"], regra["iv_max"])].reset_index(drop=True)
    df = cp.marcar_capital_unico(df).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status", "liquidez"]:
        df[col] = df[col].astype("object")
    con.register("df_etapa", df)
    con.execute(f"CREATE OR REPLACE TABLE {regra['tabela']} AS SELECT * FROM df_etapa ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_etapa")
    return df


def sincronizar_sqlite(con):
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        for regra in MODELOS.values():
            con.execute(f"SELECT * FROM {regra['tabela']}").fetchdf().to_sql(regra["tabela"], sq, if_exists="replace", index=False)
        travas = con.execute(f"SELECT * FROM {base.TABELA_TRAVAS} ORDER BY dataoperacao DESC, modelo, ticker").fetchdf()
        travas.to_sql(base.TABELA_TRAVAS, sq, if_exists="replace", index=False)
    finally:
        sq.close()


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    sel, ind, desde = carregar_sinais(con)
    px = base.carregar_precos(con, sel.ticker.unique(), desde)
    opc = base.carregar_opcoes(con, sel.ticker.unique(), base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    for modelo, regra in MODELOS.items():
        df = construir(con, modelo, regra, sel, ind, px, opc)
        print(f"\n{modelo}: tabela {regra['tabela']} criada com {len(df)} linhas.")
        cp.resumo(df)
    sincronizar_sqlite(con)
    con.close()


if __name__ == "__main__":
    main()
