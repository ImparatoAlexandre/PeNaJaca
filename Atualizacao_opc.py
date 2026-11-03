import math
import sqlite3
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import yfinance as yf


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"

TABELA_LIQUIDEZ = "Stocks_OPC_liquidez"
TABELA_OPC = "Call_Put_dados"

QUANTIDADE_VENCIMENTOS = 4
FAIXA_STRIKES = 0.20     # todos os strikes da cadeia a +-20% do preco, nos QUANTIDADE_VENCIMENTOS vencimentos (sexta)
DIAS_SQLITE = 30         # SQLite do Metabase recebe so as recolhas reais dos ultimos 30 dias (a DuckDB guarda tudo)
# Contratos das operacoes (fora da faixa ou de tickers que sairam da liquidez) continuam a ser recolhidos ate ao vencimento.
SQL_CONTRATOS_OPERACOES = """
    SELECT ticker, venc, tipo, strike FROM (
        SELECT trim(ticker) AS ticker, CAST(venc_call AS VARCHAR)[:10] AS venc, 'CALL' AS tipo, strike_call AS strike FROM Entrada_opc_travada
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'PUT', strike_put FROM Entrada_opc_travada
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'CALL', strike_call FROM Evolucao_put_operacoes
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'PUT', strike_put FROM Evolucao_put_operacoes
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'PUT', strike_put FROM Evolucao_opc_collar
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'PUT', p95_strike_put FROM Evolucao_opc_collar
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'CALL', strike_call FROM Evolucao_opc_collar
        UNION ALL SELECT trim(ticker), CAST(venc_put AS VARCHAR)[:10], 'CALL', otm_strike_call FROM Evolucao_opc_collar
    )
    WHERE strike IS NOT NULL AND venc IS NOT NULL AND replace(venc, '-', '') >= ?
"""


def ymd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def normalizar_strike(valor) -> str:
    try:
        f = float(valor)
    except Exception:
        return str(valor)
    if f.is_integer():
        return str(int(f))
    return (f"{f:.4f}").rstrip("0").rstrip(".")


def montar_id(ticker: str, vencimento: str, tipo: str, strike) -> str:
    return f"{ticker}_{vencimento}_{tipo}_{normalizar_strike(strike)}"


def valor_numero(v, default=0):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    return v


def obter_preco_atual(tk: yf.Ticker):
    hist = tk.history(period="5d", auto_adjust=False)
    if hist.empty:
        return None
    return float(hist["Close"].dropna().iloc[-1])


def proximos_vencimentos(tk: yf.Ticker, hoje: datetime):
    vencimentos = []
    for venc in tk.options:
        dt = datetime.strptime(venc, "%Y-%m-%d")
        if dt.date() >= hoje.date() and dt.weekday() == 4:
            vencimentos.append((dt, venc))
    vencimentos.sort(key=lambda item: item[0])
    return [venc for _, venc in vencimentos[:QUANTIDADE_VENCIMENTOS]]


def strikes_faixa(chain: pd.DataFrame, preco_atual: float):
    strikes = chain["strike"].dropna().astype(float).unique()
    return [k for k in strikes if abs(k / preco_atual - 1) <= FAIXA_STRIKES]


def contratos_operacoes(con, hoje_str: str):
    rows = con.execute(SQL_CONTRATOS_OPERACOES, [hoje_str]).fetchall()
    return {
        (str(ticker), str(venc), str(tipo).upper(), float(strike))
        for ticker, venc, tipo, strike in rows
        if ticker and venc and tipo and strike is not None
    }


def tickers_liquidez(con):
    rows = con.execute(
        f"""
        SELECT DISTINCT ticker
        FROM {TABELA_LIQUIDEZ}
        WHERE ticker IS NOT NULL
          AND liquidez_classificacao IN ('Alta', 'Muito alta')
        ORDER BY ticker
        """
    ).fetchall()
    return [r[0] for r in rows]


def construir_contratos_novos(ticker: str, tk: yf.Ticker, preco_atual: float, hoje: datetime):
    """Calls e puts de todos os strikes a +-FAIXA_STRIKES do preco atual (sem acumular strikes de dias anteriores)."""
    contratos = set()
    vencimentos = proximos_vencimentos(tk, hoje)

    for venc in vencimentos:
        try:
            chain = tk.option_chain(venc)
        except Exception as exc:
            print(f"[{ticker}] erro option_chain {venc}: {exc}")
            continue

        strikes_base = set()
        strikes_base.update(strikes_faixa(chain.calls, preco_atual))
        strikes_base.update(strikes_faixa(chain.puts, preco_atual))

        for strike in strikes_base:
            contratos.add((ticker, venc, "CALL", float(strike)))
            contratos.add((ticker, venc, "PUT", float(strike)))

    return contratos


def linha_contrato(ticker: str, preco_atual: float, venc: str, tipo: str, strike: float, chain, data_registro: str):
    df = chain.calls if tipo == "CALL" else chain.puts
    if df.empty:
        return None

    selecionado = df[df["strike"].astype(float) == float(strike)]
    if selecionado.empty:
        return None

    r = selecionado.iloc[0]
    return {
        "ID": montar_id(ticker, venc, tipo, strike),
        "ticker": ticker,
        "precoAtual": preco_atual,
        "tipo": tipo,
        "dataStrike": venc,
        "strike": float(strike),
        "lastPrice": float(valor_numero(r.get("lastPrice"), 0)),
        "bid": float(valor_numero(r.get("bid"), 0)),
        "ask": float(valor_numero(r.get("ask"), 0)),
        "volume": int(valor_numero(r.get("volume"), 0)),
        "openInterest": int(valor_numero(r.get("openInterest"), 0)),
        "impliedVolatility": float(valor_numero(r.get("impliedVolatility"), 0)),
        "inTheMoney": bool(valor_numero(r.get("inTheMoney"), False)),
        "DataRegistro": data_registro,
    }


def sem_negocio(linha):
    """Sem bid, sem ask e sem volume no dia: contrato parado (nao entra, salvo se for de uma operacao)."""
    return not numero_valido(linha["bid"]) and not numero_valido(linha["ask"]) and not numero_valido(linha["volume"])


def coletar_linhas(ticker: str, contratos_ticker, preco_atual: float, data_registro: str, operacoes=frozenset()):
    tk = yf.Ticker(ticker)
    por_vencimento = {}
    linhas = []

    for contrato in sorted(contratos_ticker):
        _, venc, tipo, strike = contrato
        try:
            if venc not in por_vencimento:
                por_vencimento[venc] = tk.option_chain(venc)
            linha = linha_contrato(ticker, preco_atual, venc, tipo, strike, por_vencimento[venc], data_registro)
            if linha and (contrato in operacoes or not sem_negocio(linha)):
                linhas.append(linha)
        except Exception as exc:
            print(f"[{ticker}] erro contrato {venc} {tipo} {strike}: {exc}")

    return linhas


def garantir_tabela(con):
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABELA_OPC} (
            ID VARCHAR,
            ticker VARCHAR,
            precoAtual DOUBLE,
            tipo VARCHAR,
            dataStrike VARCHAR,
            strike DOUBLE,
            lastPrice DOUBLE,
            bid DOUBLE,
            ask DOUBLE,
            volume BIGINT,
            openInterest BIGINT,
            impliedVolatility DOUBLE,
            inTheMoney BOOLEAN,
            DataRegistro VARCHAR
        )
        """
    )


def numero_valido(v):
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
        return float(v) > 0
    except Exception:
        return False


def linha_tem_preco_valido(linha):
    return any(numero_valido(linha.get(campo)) for campo in ("lastPrice", "bid", "ask"))


def preservar_campo_se_api_zerou(linha, antiga, campo):
    novo = linha.get(campo)
    antigo = antiga.get(campo) if antiga is not None else None
    if numero_valido(novo):
        return novo, False
    if numero_valido(antigo):
        return antigo, True
    return novo, False


def inserir_linhas(con, linhas, data_registro: str):
    if not linhas:
        return 0

    df_novo = pd.DataFrame(linhas)
    ids = df_novo["ID"].dropna().unique().tolist()

    con.execute("CREATE OR REPLACE TEMP TABLE tmp_ids_opc(ID VARCHAR)")
    con.register("df_ids_opc", pd.DataFrame({"ID": ids}))
    con.execute("INSERT INTO tmp_ids_opc SELECT * FROM df_ids_opc")

    df_antigo = con.execute(
        f"""
        SELECT *
        FROM {TABELA_OPC}
        WHERE DataRegistro = ?
          AND ID IN (SELECT ID FROM tmp_ids_opc)
        """,
        [data_registro],
    ).fetchdf()
    antigos = {row["ID"]: row for _, row in df_antigo.iterrows()}

    linhas_finais = []
    linhas_ignoradas = 0
    campos_preservados = 0
    campos_protegidos = ["lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]

    for linha in linhas:
        linha_final = dict(linha)
        antiga = antigos.get(linha_final.get("ID"))

        if antiga is None and not linha_tem_preco_valido(linha_final):
            linhas_ignoradas += 1
            continue

        if antiga is not None:
            for campo in campos_protegidos:
                valor, preservou = preservar_campo_se_api_zerou(linha_final, antiga, campo)
                linha_final[campo] = valor
                if preservou:
                    campos_preservados += 1

        linhas_finais.append(linha_final)

    if not linhas_finais:
        con.execute("DROP TABLE tmp_ids_opc")
        print(f"Linhas ignoradas por preco zerado/sem bid/ask: {linhas_ignoradas}")
        return 0

    df = pd.DataFrame(linhas_finais)
    ids_finais = df["ID"].dropna().unique().tolist()
    con.execute("CREATE OR REPLACE TEMP TABLE tmp_ids_opc_final(ID VARCHAR)")
    con.register("df_ids_opc_final", pd.DataFrame({"ID": ids_finais}))
    con.execute("INSERT INTO tmp_ids_opc_final SELECT * FROM df_ids_opc_final")

    con.execute(
        f"""
        DELETE FROM {TABELA_OPC}
        WHERE DataRegistro = ?
          AND ID IN (SELECT ID FROM tmp_ids_opc_final)
        """,
        [data_registro],
    )

    con.register("df_call_put_novo", df)
    con.execute(f"INSERT INTO {TABELA_OPC} SELECT * FROM df_call_put_novo")
    con.execute("DROP TABLE tmp_ids_opc")
    con.execute("DROP TABLE tmp_ids_opc_final")

    print(f"Linhas ignoradas por preco zerado/sem bid/ask: {linhas_ignoradas}")
    print(f"Campos preservados por API zerada/nula: {campos_preservados}")
    return len(df)


def sincronizar_sqlite(con, data_registro):
    """SQLite do Metabase: troca so a recolha do dia e tira a que passou dos DIAS_SQLITE dias (numa transacao).

    So esta rodada mexe em dias reais (as linhas _BS dos Completar_* nao vao para o SQLite), por isso os dias
    anteriores ja estao certos. A tabela inteira so e regravada se faltar, se as colunas mudarem ou se a contagem
    final nao bater com a DuckDB. Menos escrita = menos risco com o Metabase (Docker) a ler o arquivo.
    """
    desde = ymd(datetime.now() - timedelta(days=DIAS_SQLITE))
    reais = "ID NOT LIKE '%\\_BS' ESCAPE '\\'"
    colunas = [r[0] for r in con.execute(f"DESCRIBE {TABELA_OPC}").fetchall()]
    total = con.execute(f"SELECT count(*) FROM {TABELA_OPC} WHERE DataRegistro >= ? AND {reais}", [desde]).fetchone()[0]
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        if [r[1] for r in sq.execute(f"PRAGMA table_info('{TABELA_OPC}')").fetchall()] == colunas:
            df = con.execute(
                f"SELECT * FROM {TABELA_OPC} WHERE DataRegistro = ? AND {reais} ORDER BY ID", [data_registro]
            ).fetchdf()
            sq.execute(f"DELETE FROM {TABELA_OPC} WHERE DataRegistro = ? OR DataRegistro < ?", [data_registro, desde])
            df.to_sql(TABELA_OPC, sq, if_exists="append", index=False)
            sq.commit()  # o DELETE tambem, mesmo sem linhas novas
            if sq.execute(f"SELECT count(*) FROM {TABELA_OPC}").fetchone()[0] == total:
                return f"{len(df)} linhas do dia {data_registro}"
        df = con.execute(
            f"SELECT * FROM {TABELA_OPC} WHERE DataRegistro >= ? AND {reais} ORDER BY DataRegistro, ID", [desde]
        ).fetchdf()
        df.to_sql(TABELA_OPC, sq, if_exists="replace", index=False)
        return f"tabela inteira ({len(df)} linhas)"
    finally:
        sq.close()


def main():
    hoje = datetime.now()
    data_registro = ymd(hoje)

    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    garantir_tabela(con)

    tickers = tickers_liquidez(con)
    operacoes = contratos_operacoes(con, data_registro)
    contratos = set(operacoes)

    print(f"Tickers em {TABELA_LIQUIDEZ}: {len(tickers)}")
    print(f"Contratos de operacoes ate ao vencimento: {len(operacoes)}")

    precos = {}
    for i, ticker in enumerate(tickers, start=1):
        try:
            tk = yf.Ticker(ticker)
            preco_atual = obter_preco_atual(tk)
            if preco_atual is None or math.isnan(preco_atual):
                print(f"[{i}/{len(tickers)}] {ticker}: sem preco atual")
                continue

            precos[ticker] = preco_atual
            novos = construir_contratos_novos(ticker, tk, preco_atual, hoje)
            contratos.update(novos)
            print(f"[{i}/{len(tickers)}] {ticker}: preco={preco_atual:.2f}, novos_contratos={len(novos)}")
        except Exception as exc:
            print(f"[{i}/{len(tickers)}] {ticker}: erro {exc}")

    contratos_por_ticker = {}
    for contrato in contratos:
        contratos_por_ticker.setdefault(contrato[0], set()).add(contrato)

    todas_linhas = []
    for i, (ticker, contratos_ticker) in enumerate(sorted(contratos_por_ticker.items()), start=1):
        preco_atual = precos.get(ticker)
        if preco_atual is None:
            try:
                preco_atual = obter_preco_atual(yf.Ticker(ticker))
            except Exception:
                preco_atual = None
        if preco_atual is None:
            print(f"[coleta {i}/{len(contratos_por_ticker)}] {ticker}: sem preco, ignorado")
            continue

        linhas = coletar_linhas(ticker, contratos_ticker, preco_atual, data_registro, operacoes)
        todas_linhas.extend(linhas)
        print(f"[coleta {i}/{len(contratos_por_ticker)}] {ticker}: linhas={len(linhas)}")

    inseridas = inserir_linhas(con, todas_linhas, data_registro)
    sincronizado = sincronizar_sqlite(con, data_registro)
    con.close()

    print(f"Linhas inseridas/atualizadas em {TABELA_OPC}: {inseridas}")
    print(f"SQLite Metabase sincronizado: {sincronizado}.")


if __name__ == "__main__":
    main()




