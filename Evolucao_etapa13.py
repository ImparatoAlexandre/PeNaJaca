"""Etapa 1+3 com buracos (na view: modelo etapa13). Junta a etapa1 e a etapa3 num so capital e usa os dias parados.

Fontes   prioridade 0: as operacoes ja calculadas das tabelas Evolucao_call_put (etapa1) e Evolucao_etapa3 (etapa3), como estao
         (mesmo sinal nas duas: fica o da etapa1).
         prioridade 1 (buracos): sinais de oper_abertas_liq (Operacoes_abertas_ext: ordem so entre os tickers com opcoes liquidas,
         ordem_liq <= 3 e amplitude_candle >= 10%), com as mesmas regras:
           liq_etapa1: ordem_liq = 1, IV da call e da put <= 75%, ±7%, corte D7 (regras do Evolucao_call_put)
           liq_etapa3: ordem_liq <= 3, VR10 < 2.5, vol_hist20 > 70, IV de cada perna em ]60%, 80%], ±7%, corte D5 (Evolucao_etapa3)
         Sinal de buraco igual (dia e ticker) a uma operacao das etapas 1/3 nao entra de novo.
Capital  capital unico partilhado: uma operacao de cada vez; no mesmo dia entram primeiro as etapas 1/3, depois os buracos
         (por ordem). Todos os sinais ficam na tabela; selecionada_capital_unico marca os que entram.
Coluna   estrategia = origem (etapa1, etapa3, liq_etapa1, liq_etapa3); ordem_selecao_dia = ordem ext (etapas 1/3) ou ordem_liq.
Trava    entrada dos buracos de hoje em Entrada_opc_travada (modelos etapa13_liq1 / etapa13_liq3).
Corre    depois do Evolucao_call_put e do Evolucao_etapa3 (le as tabelas deles).
Simulacao out/25-nov/26 (2026-11-13, sem outliers > +100%): etapas 1+3 em capital unico 58 op, +36.0k com 5000;
         com os buracos 73 op (19 buracos, acerto 84%), +46.1k, pior -52% (1 operacao abaixo de -30%).
"""
import sqlite3

import duckdb
import numpy as np
import pandas as pd

import Opcoes_base as base
import Evolucao_call_put as cp
import Evolucao_etapa3 as e3
import Operacoes_abertas_ext as ext


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE
TABELA_DESTINO = "Evolucao_etapa13"
F = cp.STATUS_FECHADAS

FONTES = {  # prioridade 0, por esta ordem
    "etapa1": dict(tabela=cp.TABELA_DESTINO, corte=cp.CORTE_DU),
    "etapa3": dict(tabela=e3.MODELOS["etapa3"]["tabela"], corte=e3.MODELOS["etapa3"]["corte"]),
}
REGRAS_LIQ = {  # prioridade 1 (buracos)
    "liq_etapa1": dict(modelo="etapa13_liq1", ordem_max=1, filtro_sinal=False,
                       iv_min=None, iv_max=cp.VOL_MAX, alvo=cp.ALVO_PCT, corte=cp.CORTE_DU),
    "liq_etapa3": dict(modelo="etapa13_liq3", ordem_max=e3.ORDEM_MAX, filtro_sinal=True,
                       iv_min=e3.MODELOS["etapa3"]["iv_min"], iv_max=e3.MODELOS["etapa3"]["iv_max"],
                       alvo=e3.MODELOS["etapa3"]["alvo"], corte=e3.MODELOS["etapa3"]["corte"]),
}
COLUNAS = cp.COLUNAS + base.INDICADORES


def fim_previsto(dataoperacao, venc, corte):
    """Ultimo dia em que uma operacao aberta ocupa o capital: o corte (dias uteis) ou o vencimento, o que vier antes."""
    fim = base.ymd(np.busday_offset(np.datetime64(base.para_data(dataoperacao)), corte, roll="forward").astype(object))
    if isinstance(venc, str) and venc:
        fim = min(fim, venc.replace("-", ""))
    return fim


def carregar_etapas(con):
    partes = []
    for fonte, f in FONTES.items():
        d = con.execute(f"SELECT * FROM {f['tabela']}").fetchdf()
        d = d[d.status.isin(F + ["aberta"])].reindex(columns=COLUNAS).copy()
        d["fim_capital"] = [str(s) if s == s and s is not None else fim_previsto(o, v, f["corte"])
                            for s, o, v in zip(d.SAIDA_data, d.dataoperacao, d.venc_put)]
        partes.append(d.assign(estrategia=fonte, prio=0))
    return pd.concat(partes).drop_duplicates(["dataoperacao", "ticker"]).reset_index(drop=True)


def carregar_liq(con):
    return con.execute(
        f"""
        SELECT CAST(q.data_operacao AS VARCHAR) AS dataoperacao, trim(q.ticker) AS ticker, MIN(q.hora) AS hora,
               'ext_liq' AS estrategia, MIN(q.ordem_liq) AS ordem_selecao_dia, MAX(q.close) AS close_oper
        FROM {ext.TABELA_LIQ} q
        JOIN Stocks_OPC_liquidez l ON trim(l.ticker) = trim(q.ticker)
        WHERE l.liquidez_classificacao IN ('Alta', 'Muito alta')
          {base.filtro_liquidez_desde(con, col_data="q.data_operacao")}
        GROUP BY 1, 2
        ORDER BY 1, 3, 2
        """
    ).fetchdf()


def avaliar_buracos(con, liq):
    if liq.empty:
        return pd.DataFrame(columns=COLUNAS + ["fim_capital", "prio"])
    desde = liq.dataoperacao.min()
    px = base.carregar_precos(con, liq.ticker.unique(), desde)
    opc = base.carregar_opcoes(con, liq.ticker.unique(), base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    ind = base.calcular_indicadores(con, liq)
    partes = []
    for fonte, r in REGRAS_LIQ.items():
        ok = liq.ordem_selecao_dia <= r["ordem_max"]
        if r["filtro_sinal"]:
            ok &= (ind.VR10 < e3.VR10_MAX) & (ind.vol_hist20 > e3.VOL_MIN)
        sel = liq[ok].reset_index(drop=True)
        if sel.empty:
            continue

        def avaliar(op, px_t, opc_t, trava=None, r=r):
            return cp.avaliar(op, px_t, opc_t, trava, alvo_pct=r["alvo"], corte_du=r["corte"])

        d = base.avaliar_com_travas(con, r["modelo"], sel, avaliar, px, opc)
        d = d[e3.iv_na_faixa(d, r["iv_min"], r["iv_max"])]
        partes.append(d.assign(estrategia=fonte, prio=1))
    if not partes:
        return pd.DataFrame(columns=COLUNAS + ["fim_capital", "prio"])
    d = pd.concat(partes, ignore_index=True)
    d["_valida"] = d.status.isin(F + ["aberta"])
    d = d.sort_values(["_valida", "prio"], ascending=[False, True], kind="stable").drop_duplicates(["dataoperacao", "ticker"])
    d = base.juntar_indicadores(con, d.drop(columns="_valida").reset_index(drop=True))
    return d.reindex(columns=COLUNAS + ["fim_capital", "prio"])


def marcar_capital_unico(df):
    """Uma operacao de cada vez: no mesmo dia as etapas 1/3 (prio 0) antes dos buracos (prio 1)."""
    df["selecionada_capital_unico"] = False
    ocupado_ate = ""
    validas = df[df.status.isin(F + ["aberta"])]
    for i in validas.sort_values(["dataoperacao", "prio", "ordem_selecao_dia", "ticker"]).index:
        if df.at[i, "dataoperacao"] < ocupado_ate:
            continue
        df.at[i, "selecionada_capital_unico"] = True
        ocupado_ate = str(df.at[i, "fim_capital"])
    return df


def construir(con):
    etapas = carregar_etapas(con)
    buracos = avaliar_buracos(con, carregar_liq(con))
    chaves = set(zip(etapas.dataoperacao, etapas.ticker))
    buracos = buracos[[k not in chaves for k in zip(buracos.dataoperacao, buracos.ticker)]]
    df = pd.concat([etapas, buracos], ignore_index=True)
    df["fim_capital"] = df.fim_capital.fillna("99999999").astype(str)
    df = marcar_capital_unico(df).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status", "liquidez"]:
        df[col] = df[col].astype("object")
    con.register("df_etapa13", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_etapa13 ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_etapa13")
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


def resumo(df):
    fechadas = df[df.status.isin(F)]
    cu = fechadas[fechadas.selecionada_capital_unico]
    for nome, g in cu.groupby("estrategia"):
        print(f"  capital unico {nome:11s} n={len(g):3d} acerto={(g.retorno > 0).mean() * 100:.0f}% lucro={g.lucro.sum():+,.0f}")
    cp.resumo(df)


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    df = construir(con)
    qtd = sincronizar_sqlite(con)
    print(f"Tabela {TABELA_DESTINO} criada com {qtd} linhas.")
    resumo(df)
    con.close()


if __name__ == "__main__":
    main()
