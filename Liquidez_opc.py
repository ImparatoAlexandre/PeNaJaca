"""Liquidez das opcoes: recolha diaria e reclassificacao flexivel da Stocks_OPC_liquidez (so sobe de classe).

Recolha   1x por MES (primeira rodada do mes depois de HORA_RECOLHA_NY, hora de Nova York; ou --forcar), para os tickers com
          liquidez_base em CLASSES_RECOLHA (--todos: todos os tickers da tabela). Para cada ticker, os
          QTD_VENCIMENTOS primeiros vencimentos do yfinance: volume, open interest, contratos com volume e spread
          medio -> Stocks_OPC_liquidez_hist (1 linha por ticker e dia; o mesmo dia e regravado).
Score     o mesmo da tabela original (05/09/2026): volume + 0.01 * open interest + 10 * contratos com volume.
Classe    pelos limites originais: Muito alta >= 100000, Alta >= 30000, Media >= 5000, Baixa > 0.
Flexivel  com a mediana do score das ultimas JANELA_DIAS recolhas mensais (minimo MIN_DIAS, ou seja 2 meses seguidos), um
          ticker SOBE de classe se a mediana >= FATOR_FLEX x o limite da classe de cima (Alta: 25500, Muito alta: 85000).
          A reclassificacao so corre no dia da recolha.
Regras    - nunca desce abaixo de liquidez_base (classe de 05/09/2026): os Alta / Muito alta de hoje ficam sempre;
          - um ticker promovido nao volta a descer sozinho (so aviso no fim, se a mediana cair abaixo de
            AVISO_QUEDA x o limite original da Alta);
          - liquidez_desde = dia da promocao. Opcoes_base.carregar_operacoes e o Evolucao_opc_collar so usam os
            sinais do ticker a partir desse dia (o historico dos modelos nao muda com a promocao).
Grava     Stocks_OPC_liquidez (colunas novas: liquidez_base, liquidez_desde, score_mediana, dias_amostra,
          data_reclassificacao) e Stocks_OPC_liquidez_hist, na DuckDB e no SQLite do Metabase.
Uso       python Liquidez_opc.py [--forcar] [--todos] [--simular]
          --simular: recolhe (se for preciso) e mostra a reclassificacao, sem gravar a Stocks_OPC_liquidez.
"""
import sqlite3
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import yfinance as yf


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"
TABELA = "Stocks_OPC_liquidez"
TABELA_HIST = "Stocks_OPC_liquidez_hist"

QTD_VENCIMENTOS = 3
CLASSES_RECOLHA = ("Media", "Alta", "Muito alta")   # Baixa fica longe do limite da Alta (score maximo ~5000)
HORA_RECOLHA_NY = 15                                 # volume do dia quase completo
PAUSA_S = 0.2

LIMITES = [("Muito alta", 100_000), ("Alta", 30_000), ("Media", 5_000), ("Baixa", 0.01)]
ORDEM_CLASSE = {"Sem dados": 0, "Baixa": 1, "Media": 2, "Alta": 3, "Muito alta": 4}
FATOR_FLEX = 0.85      # promove com 85% do limite (Alta >= 25500, Muito alta >= 85000)
JANELA_DIAS = 3       # recolhas mensais (ultimos 3 meses)
MIN_DIAS = 2          # promove so com 2 meses de recolha (evita pico de 1 dia)
AVISO_QUEDA = 0.70     # promovido com mediana < 70% do limite original da Alta (21000): so aviso


def classe_por_score(score, fator=1.0):
    for nome, limite in LIMITES:
        if score >= limite * fator:
            return nome
    return "Sem dados"


def garantir_colunas(con):
    cols = {r[0] for r in con.execute(f"DESCRIBE {TABELA}").fetchall()}
    novas = {"liquidez_base": "VARCHAR", "liquidez_desde": "VARCHAR", "score_mediana": "DOUBLE",
             "dias_amostra": "INTEGER", "data_reclassificacao": "VARCHAR"}
    for c, t in novas.items():
        if c not in cols:
            con.execute(f"ALTER TABLE {TABELA} ADD COLUMN {c} {t}")
    # classe de referencia: a da tabela no 1.o uso (05/09/2026); nunca e reescrita
    con.execute(f"UPDATE {TABELA} SET liquidez_base = liquidez_classificacao WHERE liquidez_base IS NULL")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABELA_HIST} (
            data VARCHAR, ticker VARCHAR, qtd_vencimentos_total INTEGER, volume_total_opcoes BIGINT,
            volume_call BIGINT, volume_put BIGINT, open_interest_total BIGINT, contratos_com_volume INTEGER,
            bid_ask_spread_pct_medio DOUBLE, score_liquidez DOUBLE, erro VARCHAR, data_hora VARCHAR)""")


def ler_ticker(ticker):
    tk = yf.Ticker(ticker)
    vencs = list(tk.options)
    linhas = []
    for v in vencs[:QTD_VENCIMENTOS]:
        ch = tk.option_chain(v)
        for tipo, df in (("CALL", ch.calls), ("PUT", ch.puts)):
            if not df.empty:
                linhas.append(df.assign(tipo=tipo))
    if not linhas:
        return dict(qtd_vencimentos_total=len(vencs), volume_total_opcoes=0, volume_call=0, volume_put=0,
                    open_interest_total=0, contratos_com_volume=0, bid_ask_spread_pct_medio=None, score_liquidez=0.0)
    d = pd.concat(linhas)
    vol = d.volume.fillna(0)
    oi = d.openInterest.fillna(0)
    com_vol = d[vol > 0]
    meio = (com_vol.bid + com_vol.ask) / 2
    spread = ((com_vol.ask - com_vol.bid) / meio * 100).replace([np.inf, -np.inf], np.nan)
    v_total, oi_total, n = int(vol.sum()), int(oi.sum()), int(len(com_vol))
    return dict(qtd_vencimentos_total=len(vencs), volume_total_opcoes=v_total,
                volume_call=int(vol[d.tipo == "CALL"].sum()), volume_put=int(vol[d.tipo == "PUT"].sum()),
                open_interest_total=oi_total, contratos_com_volume=n,
                bid_ask_spread_pct_medio=round(float(spread.mean()), 4) if spread.notna().any() else None,
                score_liquidez=round(v_total + 0.01 * oi_total + 10 * n, 2))


def recolher(con, hoje, todos):
    filtro = "" if todos else "WHERE liquidez_base IN (" + ",".join(f"'{c}'" for c in CLASSES_RECOLHA) + ")"
    tickers = [r[0] for r in con.execute(f"SELECT DISTINCT trim(ticker) FROM {TABELA} {filtro} ORDER BY 1").fetchall()]
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    linhas = []
    for i, t in enumerate(tickers, 1):
        try:
            r = ler_ticker(t)
            r["erro"] = None
        except Exception as exc:
            r = dict(score_liquidez=None, erro=str(exc)[:200])
        linhas.append(dict(data=hoje, ticker=t, data_hora=agora, **r))
        if i % 25 == 0:
            print(f"[{i}/{len(tickers)}] {t}", flush=True)
        time.sleep(PAUSA_S)
    df = pd.DataFrame(linhas).reindex(columns=[c[0] for c in con.execute(f"DESCRIBE {TABELA_HIST}").fetchall()])
    con.register("df_liq", df)
    con.execute(f"DELETE FROM {TABELA_HIST} WHERE data = ? AND ticker IN (SELECT ticker FROM df_liq)", [hoje])
    con.execute(f"INSERT INTO {TABELA_HIST} BY NAME SELECT * FROM df_liq")
    con.unregister("df_liq")
    erros = df.erro.notna().sum()
    print(f"Recolha {hoje}: {len(df)} tickers, {erros} com erro.")


def reclassificar(con, hoje, simular):
    tab = con.execute(f"SELECT trim(ticker) AS ticker, liquidez_classificacao, liquidez_base, liquidez_desde FROM {TABELA}").fetchdf()
    hist = con.execute(f"""
        SELECT ticker, median(score_liquidez) AS score_mediana, count(*) AS dias_amostra FROM (
            SELECT ticker, score_liquidez, row_number() OVER (PARTITION BY ticker ORDER BY data DESC) AS rn
            FROM {TABELA_HIST} WHERE score_liquidez IS NOT NULL)
        WHERE rn <= {JANELA_DIAS} GROUP BY 1""").fetchdf()
    df = tab.merge(hist, on="ticker", how="left")
    df["dias_amostra"] = df.dias_amostra.fillna(0).astype(int)
    df["classe_flex"] = [classe_por_score(s, FATOR_FLEX) if d >= MIN_DIAS else "Sem dados"
                         for s, d in zip(df.score_mediana.fillna(0), df.dias_amostra)]
    nova, desde = [], []
    for r in df.itertuples():
        atual = r.liquidez_classificacao
        cand = max([atual, r.liquidez_base, r.classe_flex], key=lambda c: ORDEM_CLASSE.get(c, 0))
        if ORDEM_CLASSE.get(cand, 0) > ORDEM_CLASSE.get(atual, 0):  # promocao
            nova.append(cand)
            desde.append(r.liquidez_desde if isinstance(r.liquidez_desde, str) else hoje)
        else:
            nova.append(atual)
            desde.append(r.liquidez_desde)
    df["nova"], df["desde_novo"] = nova, desde
    promovidos = df[df.nova != df.liquidez_classificacao]
    print(f"\nReclassificacao (mediana das ultimas {JANELA_DIAS} recolhas, minimo {MIN_DIAS} dias, fator {FATOR_FLEX}):")
    if promovidos.empty:
        print("  nenhum ticker sobe de classe hoje.")
    for r in promovidos.itertuples():
        print(f"  {r.ticker}: {r.liquidez_classificacao} -> {r.nova} (mediana {r.score_mediana:,.0f}, {r.dias_amostra} dias)")
    lim_alta = dict(LIMITES)["Alta"]
    quedas = df[df.desde_novo.notna() & (df.dias_amostra >= MIN_DIAS) & (df.score_mediana < AVISO_QUEDA * lim_alta)]
    for r in quedas.itertuples():
        print(f"  AVISO {r.ticker}: promovido em {r.desde_novo}, mediana caiu para {r.score_mediana:,.0f}")
    perto = df[(df.nova == "Media") & (df.dias_amostra >= 1) & (df.score_mediana >= 0.7 * lim_alta)].sort_values("score_mediana", ascending=False)
    if not perto.empty:
        print("  Media perto do limite flexivel (25500): " + ", ".join(
            f"{r.ticker} {r.score_mediana:,.0f} ({r.dias_amostra}d)" for r in perto.head(15).itertuples()))
    if simular:
        print("(simular: Stocks_OPC_liquidez nao gravada)")
        return
    upd = df[["ticker", "nova", "desde_novo", "score_mediana", "dias_amostra"]]
    con.register("upd_liq", upd)
    con.execute(f"""
        UPDATE {TABELA} AS t SET liquidez_classificacao = u.nova, liquidez_desde = u.desde_novo,
               score_mediana = u.score_mediana, dias_amostra = u.dias_amostra, data_reclassificacao = ?
        FROM upd_liq AS u WHERE trim(t.ticker) = u.ticker""", [hoje])
    con.unregister("upd_liq")


def sincronizar_sqlite(con):
    tab = con.execute(f"SELECT * FROM {TABELA} ORDER BY ranking_liquidez").fetchdf()
    hist = con.execute(f"SELECT * FROM {TABELA_HIST} ORDER BY data DESC, ticker").fetchdf()
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        tab.to_sql(TABELA, sq, if_exists="replace", index=False)
        hist.to_sql(TABELA_HIST, sq, if_exists="replace", index=False)
    finally:
        sq.close()


def main():
    forcar, todos, simular = "--forcar" in sys.argv, "--todos" in sys.argv, "--simular" in sys.argv
    hoje = datetime.now().strftime("%Y%m%d")
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    try:
        garantir_colunas(con)
        ultima = con.execute(f"SELECT max(data) FROM {TABELA_HIST}").fetchone()[0]
        hora_ny = datetime.now(ZoneInfo("America/New_York")).hour
        if ultima and ultima[:6] == hoje[:6] and not forcar:
            print(f"Liquidez ja recolhida este mes ({ultima}): proxima recolha no mes seguinte.")
            return
        if datetime.now().weekday() >= 5 and not forcar:
            print("Fim de semana: sem recolha.")
            return
        if hora_ny < HORA_RECOLHA_NY and not forcar:
            print(f"Antes das {HORA_RECOLHA_NY}h de Nova York: recolha do mes fica para uma rodada mais tarde.")
            return
        recolher(con, hoje, todos)
        reclassificar(con, hoje, simular)
        if not simular:
            sincronizar_sqlite(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
