"""Etapa 1 (na view: modelo etapa1). Compra de call e put (delta 0.30 / -0.30) com entrada no dia do sinal.

Sinais   oper_abertas com estrategia = ESTRATEGIA ("ext") e ordem_selecao_dia <= ORDEM_MAX (1), com opcoes liquidas
         (mesma selecao do Evolucao_stock). Fora: sinais com IV da call ou da put acima de VOL_MAX (70%).
Entrada  no dia da operacao, ao close (close_oper), no 1.o vencimento aceite por base.venc_valido (sexta com >= 10 du):
           - compra o par 1 call (delta mais proximo de CALL_DELTA) + 1 put (delta mais proximo de PUT_DELTA),
             na mesma quantidade; VALOR_OPERACAO e aplicado no par.
Saida    1.o dia apos a operacao em que o ativo toca +ALVO_PCT (Max) ou -ALVO_PCT (Min): vende a call e a put
         (ativo ao alvo ou na abertura, se abrir alem dele) -> status 'alvo_alta' / 'alvo_baixa'.
         Os dois alvos no mesmo dia: usa o close desse dia (status pelo lado com maior movimento).
         Sem alvo ate ao CORTE_DU.o dia de pregao apos a entrada: vende as duas ao close desse dia (status 'corte').
         Sem alvo ate ao vencimento: liquida as duas pelo valor intrinseco ao close do vencimento (status 'vencimento').
         avaliar recebe alvo e corte como parametros (o Evolucao_vol_call_put usa ±10% e D5).
Retorno  % do par: (saida_call + saida_put) / (valor_call + valor_put) - 1; lucro = VALOR_OPERACAO * retorno.
Capital  capital unico: uma operacao de cada vez, sempre com VALOR_OPERACAO (o lucro e retirado, a perda reposta).
Precos   opcoes a Black-Scholes com o preco do ativo no momento e a volatilidade da recolha (funcoes do Opcoes_base).
Trava    entrada (strikes, IV, valores) dos sinais de hoje gravada na 1.a rodada em Entrada_opc_travada (Opcoes_base.avaliar_com_travas).
"""
import sqlite3

import duckdb
import numpy as np
import pandas as pd

import Opcoes_base as base
from Evolucao_opc_collar import bs_opcao


DB_FILE = base.DB_FILE
SQLITE_FILE = base.SQLITE_FILE
TABELA_DESTINO = "Evolucao_call_put"

ESTRATEGIA = "ext"   # so sinais ext (None = ext + fase2)
ORDEM_MAX = 1
CALL_DELTA = 0.30
PUT_DELTA = -0.30
ALVO_PCT = 0.07      # alvo de alta e de baixa no ativo (era 0.10 ate 2026-11-08)
CORTE_DU = 7         # sem alvo: sai ao close do 7.o dia de pregao apos a entrada (None = ate ao vencimento)
VOL_MAX = 0.70       # IV da call ou da put acima disto: o sinal nao entra (2026-11-08: 80% -> 70%, mesmo lucro com metade das perdas)
VALOR_OPERACAO = 5000
STATUS_FECHADAS = ["alvo_alta", "alvo_baixa", "corte", "vencimento"]

COLUNAS = [
    "dataoperacao", "hora", "ticker", "estrategia", "close_oper", "ordem_selecao_dia",
    "strike_call", "valor_call", "vol_call", "strike_put", "valor_put", "vol_put", "venc_put", "dias_venc",
    "alvo_alta", "alvo_baixa", "SAIDA_data", "var_MAX_ativo", "var_MIN_ativo", "saida_ativo", "saida_call", "saida_put",
    "status", "retorno", "lucro", "dias_capital", "selecionada_capital_unico",
]


def perna(opc_t, tipo, delta, dia, spot):
    d0 = base.para_data(dia)
    ch = base.cadeia(opc_t, tipo, dia)
    vencs = sorted(v for v in ch.venc.unique() if base.venc_valido(d0, v)) if not ch.empty else []
    if not vencs:
        return None
    venc = vencs[0]
    du = base.dias_uteis(d0, venc)
    r = base.escolher_por_delta(ch[ch.venc == venc], spot, du, delta, tipo)
    if r is None:
        return None
    iv = float(r.iv)
    if iv > 5:
        iv = iv / 100
    preco = bs_opcao(spot, float(r.strike), dia, venc, iv, tipo) or float(r.lp)
    return dict(venc=venc, du=du, k=float(r.strike), iv=iv, p0=preco)


def valor_saida(opc_t, tipo, p, dia, spot):
    if base.dias_uteis(base.para_data(dia), p["venc"]) > 0:
        return base.preco_opcao(opc_t, tipo, p["venc"], p["k"], dia, spot, p["iv"])
    return max(spot - p["k"], 0.0) if tipo == "CALL" else max(p["k"] - spot, 0.0)


def avaliar(op, px_t, opc_t, trava=None, alvo_pct=None, corte_du=None):
    """trava: entrada ja gravada em Entrada_opc_travada para este sinal (usa-a em vez de escolher pela cadeia do dia).
    alvo_pct / corte_du: None = ALVO_PCT / CORTE_DU deste modulo."""
    alvo_pct = ALVO_PCT if alvo_pct is None else alvo_pct
    corte_du = CORTE_DU if corte_du is None else corte_du
    out = {"dataoperacao": op.dataoperacao, "hora": op.hora, "ticker": op.ticker, "estrategia": op.estrategia, "close_oper": op.close_oper,
           "ordem_selecao_dia": op.ordem_selecao_dia}
    if px_t is None or opc_t is None or op.close_oper != op.close_oper:
        out["status"] = "sem_dados"
        return out
    C = float(op.close_oper)
    if trava is not None:
        c = dict(venc=trava["venc_call"], k=trava["strike_call"], iv=trava["iv_call"], p0=trava["valor_call"])
        p = dict(venc=trava["venc_put"], k=trava["strike_put"], iv=trava["iv_put"], p0=trava["valor_put"])
    else:
        c = perna(opc_t, "CALL", CALL_DELTA, op.dataoperacao, C)
        p = perna(opc_t, "PUT", PUT_DELTA, op.dataoperacao, C)
    if c is None or p is None:
        out["status"] = "sem_opcoes"
        return out
    out["_entrada"] = dict(venc_call=c["venc"], strike_call=c["k"], iv_call=c["iv"], valor_call=c["p0"],
                           venc_put=p["venc"], strike_put=p["k"], iv_put=p["iv"], valor_put=p["p0"])
    venc = min(c["venc"], p["venc"])
    vs = base.ymd(venc)
    alta, baixa = C * (1 + alvo_pct), C * (1 - alvo_pct)
    out.update(strike_call=c["k"], valor_call=round(c["p0"], 2), vol_call=round(c["iv"], 2), strike_put=p["k"],
               valor_put=round(p["p0"], 2), vol_put=round(p["iv"], 2), venc_put=venc.isoformat(),
               dias_venc=base.dias_uteis(base.para_data(op.dataoperacao), c["venc"]),
               alvo_alta=round(alta, 2), alvo_baixa=round(baixa, 2), _iv_max=max(c["iv"], p["iv"]))
    dias = px_t[(px_t.index > op.dataoperacao) & (px_t.index <= vs)]
    d_corte = None
    if corte_du:
        if len(dias) >= corte_du:
            d_corte = dias.index[corte_du - 1]
        dias = dias.iloc[:corte_du]
    up = dias[dias.h >= alta]
    dn = dias[dias.l <= baixa]
    d_up = up.index[0] if not up.empty else None
    d_dn = dn.index[0] if not dn.empty else None
    if d_up or d_dn:
        if d_up and d_dn and d_up == d_dn:
            d = d_up
            S = float(dias.c[d])
            status = "alvo_alta" if S >= C else "alvo_baixa"
        elif d_up and (not d_dn or d_up < d_dn):
            d, S, status = d_up, float(max(up.o.iloc[0], alta)), "alvo_alta"
        else:
            d, S, status = d_dn, float(min(dn.o.iloc[0], baixa)), "alvo_baixa"
        call_s = valor_saida(opc_t, "CALL", c, d, S)
        put_s = valor_saida(opc_t, "PUT", p, d, S)
    elif d_corte is not None and d_corte < vs:
        d, S, status = d_corte, float(dias.c[d_corte]), "corte"
        call_s = valor_saida(opc_t, "CALL", c, d, S)
        put_s = valor_saida(opc_t, "PUT", p, d, S)
    elif px_t.index[-1] >= vs:
        d = dias.index[-1]
        S = float(dias.c.iloc[-1])
        call_s, put_s = max(S - c["k"], 0.0), max(p["k"] - S, 0.0)
        status = "vencimento"
    else:
        fim = vs
        if corte_du:  # fim previsto: o dia do corte (dias uteis, sem feriados), se vier antes do vencimento
            prev = base.ymd(np.busday_offset(np.datetime64(base.para_data(op.dataoperacao)), corte_du, roll="forward").astype(object))
            fim = min(vs, prev)
        out.update(status="aberta", fim_capital=fim)
        return out
    janela = dias[dias.index <= d]
    retorno = ((call_s + put_s) / (c["p0"] + p["p0"]) - 1) * 100
    out.update(status=status, SAIDA_data=d, saida_ativo=round(S, 2), saida_call=round(call_s, 2), saida_put=round(put_s, 2),
               var_MAX_ativo=round((janela.h.max() / C - 1) * 100, 2), var_MIN_ativo=round((janela.l.min() / C - 1) * 100, 2),
               retorno=round(retorno, 2), lucro=round(VALOR_OPERACAO * retorno / 100, 2),
               dias_capital=max(int(np.busday_count(base.para_data(op.dataoperacao), base.para_data(d))), 1), fim_capital=d)
    return out


def filtrar_iv(df):
    """Tira os sinais com IV da call ou da put acima de VOL_MAX (sinais sem opcoes ficam, como antes)."""
    if "_iv_max" not in df:
        return df
    return df[~(df["_iv_max"] > VOL_MAX)].reset_index(drop=True)


def marcar_capital_unico(df):
    """Uma operacao de cada vez: entra a 1.a operacao (por data e ordem) com o capital livre."""
    df["selecionada_capital_unico"] = False
    ocupado_ate = ""
    validas = df[df.status.isin(STATUS_FECHADAS + ["aberta"])]
    for i in validas.sort_values(["dataoperacao", "ordem_selecao_dia", "ticker"]).index:
        if df.at[i, "dataoperacao"] < ocupado_ate:
            continue
        df.at[i, "selecionada_capital_unico"] = True
        ocupado_ate = df.at[i, "fim_capital"]
    return df


def construir(con):
    base.ESTRATEGIA, base.ORDEM_MAX = ESTRATEGIA, ORDEM_MAX
    ops = base.carregar_operacoes(con)
    desde = ops.dataoperacao.min()
    px = base.carregar_precos(con, ops.ticker.unique(), desde)
    opc = base.carregar_opcoes(con, ops.ticker.unique(), base.ymd(base.para_data(desde) - pd.Timedelta(days=base.DIAS_FALLBACK_CADEIA)))
    df = base.avaliar_com_travas(con, "call_put", ops, avaliar, px, opc)
    df = filtrar_iv(df)
    df = marcar_capital_unico(df).reindex(columns=COLUNAS)
    for col in ["hora", "estrategia", "venc_put", "SAIDA_data", "status"]:
        df[col] = df[col].astype("object")
    con.register("df_call_put", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABELA_DESTINO} AS SELECT * FROM df_call_put ORDER BY dataoperacao DESC, ticker")
    con.unregister("df_call_put")
    return df


def sincronizar_sqlite(con):
    df = con.execute(f"SELECT * FROM {TABELA_DESTINO}").fetchdf()
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
    finally:
        sq.close()
    return len(df)


def resumo(df):
    print(df.status.value_counts().to_string())
    fechadas = df[df.status.isin(STATUS_FECHADAS)]
    cu = fechadas[fechadas.selecionada_capital_unico]
    for nome, f in [("todos os sinais", fechadas), ("capital unico", cu)]:
        if not f.empty:
            print(f"{nome:16s} n={len(f):3d} media={f.retorno.mean():+.2f}% acerto={(f.retorno > 0).mean() * 100:.0f}% "
                  f"lucro com {VALOR_OPERACAO} por operacao={f.lucro.sum():+,.0f}")


def main():
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    df = construir(con)
    qtd = sincronizar_sqlite(con)
    print(f"Tabela {TABELA_DESTINO} criada com {qtd} linhas.")
    resumo(df)
    con.close()


if __name__ == "__main__":
    main()
