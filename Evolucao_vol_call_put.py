"""Compra de call e put (delta 0.30 / -0.30) como o Evolucao_call_put, mas escolhe o par de menor volatilidade do dia.

Sinais   oper_abertas com estrategia = "ext" e ordem_selecao_dia <= ORDEM_MAX (3), com opcoes liquidas (Alta / Muito alta).
Escolha  para cada sinal calcula as pernas de entrada do Evolucao_call_put (call delta 0.30 e put delta -0.30) e a IV de
         cada uma. Fora: pares com IV da call ou da put acima de VOL_MAX. Entre os restantes do dia entra 1 so: o de menor
         vol_par = (IV call + IV put) / 2. Dia com par ja travado (Entrada_opc_travada, modelo vol_call_put): fica esse
         par, mesmo que uma rodada seguinte traga um par de vol menor.
Entrada  saida, retorno e capital unico: iguais ao Evolucao_call_put (avaliar), com VALOR_OPERACAO no par.
"""
import sqlite3

import duckdb
import pandas as pd

import Opcoes_base as base
import Evolucao_call_put as cp


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE
TABELA_DESTINO = "Evolucao_vol_call_put"
MODELO = "vol_call_put"

ESTRATEGIA = "ext"
ORDEM_MAX = 3
VOL_MAX = 0.80  # IV da call ou da put acima disto: o par nao entra
# 80% (era 60%) em 2026-10-24: grade 50%..sem limite, capital unico 77.5k -> 102k; pares de IV 80-100% rendem ~+4%, acima de 100% perdem

COLUNAS = cp.COLUNAS + ["vol_par", "candidatos"]


def escolher_pares(con, ops, opc):
    """1 sinal por dia: o par com menor vol_par entre os de IV <= VOL_MAX (ou o ja travado do dia)."""
    travas = base.carregar_travas(con, MODELO)
    dias_travados = {k[0]: k for k in travas}
    linhas = []
    for op in ops.itertuples():
        trava = travas.get((op.dataoperacao, op.ticker, op.hora))
        o = opc.get(op.ticker.upper())
        if trava is not None:
            iv_c, iv_p = trava["iv_call"], trava["iv_put"]
        elif o is not None and op.close_oper == op.close_oper:
            c = cp.perna(o, "CALL", cp.CALL_DELTA, op.dataoperacao, float(op.close_oper))
            p = cp.perna(o, "PUT", cp.PUT_DELTA, op.dataoperacao, float(op.close_oper))
            if c is None or p is None:
                continue
            iv_c, iv_p = c["iv"], p["iv"]
        else:
            continue
        linhas.append(dict(i=op.Index, dataoperacao=op.dataoperacao, ticker=op.ticker, iv_c=iv_c, iv_p=iv_p,
                           vol_par=(iv_c + iv_p) / 2, travado=trava is not None))
    cand = pd.DataFrame(linhas)
    if cand.empty:
        return ops.iloc[0:0].assign(vol_par=None, candidatos=None)
    cand = cand[(cand.iv_c <= VOL_MAX) & (cand.iv_p <= VOL_MAX)]
    escolhidos = []
    for d, g in cand.groupby("dataoperacao"):
        if d in dias_travados and g.travado.any():
            r = g[g.travado].iloc[0]
        else:
            r = g.sort_values(["vol_par", "ticker"]).iloc[0]
        escolhidos.append((r.i, round(r.vol_par * 100, 1), len(g)))
    sel = ops.loc[[e[0] for e in escolhidos]].copy()
    sel["vol_par"] = [e[1] for e in escolhidos]
    sel["candidatos"] = [e[2] for e in escolhidos]
    return sel


def construir(con):
    base.ESTRATEGIA, base.ORDEM_MAX = ESTRATEGIA, ORDEM_MAX
    ops = base.carregar_operacoes(con)
    desde = ops.dataoperacao.min()
    px = base.carregar_precos(con, ops.ticker.unique(), desde)
    opc = base.carregar_opcoes(con, ops.ticker.unique(), base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    sel = escolher_pares(con, ops, opc).reset_index(drop=True)
    df = base.avaliar_com_travas(con, MODELO, sel, cp.avaliar, px, opc)
    df["vol_par"], df["candidatos"] = sel.vol_par.values, sel.candidatos.values
    df = cp.marcar_capital_unico(df).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status"]:
        df[col] = df[col].astype("object")
    con.register("df_vol_call_put", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_vol_call_put ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_vol_call_put")
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
