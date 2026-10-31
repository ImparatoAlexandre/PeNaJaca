# =========================
# LOG INICIAL (prova de execucao)
# =========================
from datetime import datetime
with open(r"C:\DataMarket\TESTE_TASK.txt", "a") as f:
    f.write(f"Task executou -> {datetime.now()}\n")

# =========================
# IMPORTS
# =========================
try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from scipy.stats import anderson
    from statsmodels.tsa.stattools import adfuller, kpss
    import os
    import duckdb
    import warnings
    warnings.filterwarnings("ignore")

except Exception as e:
    with open(r"C:\DataMarket\IMPORT_ERROR.txt", "a") as f:
        f.write(str(e) + "\n")
    raise

# =========================
# CONFIGURACAO
# =========================
stocks = [
    'RY.TO', 'TD.TO', 'ENB.TO', 'BNS.TO', 'BMO.TO', 'CNQ.TO', 'SHOP.TO', 'CNR.TO', 'CP.TO', 'BAM.TO',
    'BN.TO', 'CM.TO', 'SU.TO', 'TRP.TO', 'MFC.TO', 'ATD.TO', 'TRI.TO', 'NTR.TO', 'IFC.TO', 'BCE.TO',
    'T.TO', 'POW.TO', 'SLF.TO', 'FTS.TO', 'WCN.TO', 'AEM.TO', 'WPM.TO', 'ABX.TO', 'GIB-A.TO', 'CSU.TO',
    'DOL.TO', 'FM.TO', 'IMO.TO', 'TECK-B.TO', 'L.TO', 'WN.TO', 'MRU.TO', 'SAP.TO', 'QSR.TO',
    'MG.TO', 'GWO.TO', 'NA.TO', 'RCI-B.TO', 'CTC-A.TO', 'CVE.TO', 'TOU.TO', 'ARX.TO', 'WCP.TO', 'OVV.TO',
    'H.TO', 'EMA.TO', 'AQN.TO', 'BEP-UN.TO', 'BIP-UN.TO', 'CAR-UN.TO', 'REI-UN.TO', 'DIR-UN.TO', 'GRT-UN.TO', 'HR-UN.TO',
    'K.TO', 'AGI.TO', 'LUN.TO', 'IVN.TO', 'EQX.TO', 'BTO.TO', 'NG.TO', 'DPM.TO', 'TFII.TO',
    'AC.TO', 'CCL-B.TO', 'WSP.TO', 'STN.TO', 'TIH.TO', 'EFN.TO', 'GFL.TO', 'DSG.TO', 'OTEX.TO', 'BB.TO',
    'CLS.TO', 'LSPD.TO', 'WELL.TO', 'DOO.TO', 'PET.TO', 'BYD.TO', 'GOOS.TO', 'ALA.TO', 'PPL.TO', 'KEY.TO',
    'GEI.TO', 'BTE.TO', 'ATH.TO', 'DML.TO', 'CAE.TO', 'AAPL', 'MSFT', 'NVDA', 'AMZN',
    'META', 'TSLA', 'INTC', 'NFLX', 'GOOGL', 'GOOG', 'AVGO', 'MU',
    'TXN', 'JPM', 'BAC', 'C', 'WFC', 'GS', 'XOM', 'CVX', 'OXY',
    'SLB', 'KO', 'PEP', 'PG', 'WMT', 'COST', 'DIS', 'HD', 'LOW', 'NKE',
    'MCD', 'JNJ', 'PFE', 'MRK', 'ABBV', 'LLY', 'UNH', 'AMGN', 'TMO', 'DHR',
    'MDT', 'PLTR', 'SNAP', 'PYPL', 'SOFI', 'ABNB', 'BKNG',
    'BA', 'CAT', 'GE', 'HON', 'MMM', 'F', 'GM', 'RIVN', 'LCID', 'NIO',
    'SHOP', 'ROKU', 'COIN', 'MARA', 'RIOT', 'AAL', 'DAL', 'UAL', 'LUV', 'NCLH',
    'CCL', 'RCL', 'T', 'VZ', 'TMUS', 'ORCL', 'IBM', 'CSCO', 'ADBE', 'CRM',
    'V', 'MA', 'BRK-B', 'AXP', 'MS', 'SCHW', 'ISRG', 'NOW', 'PANW', 'SMCI', 'DE', 'LRCX', 'AMAT', 'PDD', 'HOOD', 'RBLX', 'U',
    'SOUN', 'TSM', 'MRVL', 'ON', 'TGT', 'CVS', 'PINS', 'DKNG', 'CELH',
    'NU', 'ONDS', 'NOK', 'PATH', 'SPCX', 'OPEN', 'SKHY', 'JOBY', 'WBD',
    'JBLU', 'RIG', 'DNN', 'KVUE', 'IREN', 'PLUG', 'ERIC', 'QS', 'CMCSA',
    'GRAB', 'FIG', 'BBD', 'RGTI', 'BB', 'INFY', 'ACHR', 'BTG', 'KEEL',
    'WULF', 'BMNR', 'CPNG', 'FRMI', 'CLF', 'ABEV', 'RKT', 'BTE', 'IONQ',
    'STLA', 'VG', 'PBR', 'ITUB', 'UMC', 'CMG', 'CIFR', 'RDW', 'QBTS', 'RKLB',
    'CRWV', 'LYG', 'HPE', 'CCC', 'VALE', 'AUR', 'HPQ', 'FRSH', 'APLD', 'AGNC',
    'CRCL', 'FCX', 'MSTR', 'AOS', 'AAON', 'ABT', 'ACHC', 'ACN', 'AYI', 'ADT',
    'WMS', 'ACM', 'AES', 'AMG', 'AFL', 'AGCO', 'A', 'ADC', 'APD', 'AKAM',
    'ALK', 'ACI', 'AA', 'ARE', 'ALGN', 'ALLE', 'ALGM', 'LNT', 'ALSN',
    'ALL', 'ALLY', 'ALNY', 'MO', 'AMCR', 'DOX', 'AMTM', 'AS', 'AEE', 'AEP',
    'AFG', 'AMH', 'AIG', 'AMT', 'AWK', 'COLD', 'AMP', 'AME', 'AMKR', 'APH',
    'ADI', 'AU', 'NLY', 'AM', 'AR', 'AON', 'APA', 'APG', 'APO', 'APPF',
    'AIT', 'APP', 'ATR', 'APTV', 'ARMK', 'ACGL', 'ARES', 'ANET', 'AWI',
    'ARW', 'AJG', 'ASH', 'AIZ', 'AGO', 'ALAB', 'ASTS', 'ATI', 'TEAM', 'ATO',
    'ADSK', 'ADP', 'AN', 'AZO', 'AVB', 'AVTR', 'AVY', 'CAR', 'AVT', 'AXTA',
    'AXS', 'AXON', 'BKR', 'BALL', 'OZK', 'BBWI', 'BAX', 'BDX', 'BRBR', 'BSY',
    'BBY', 'BILL', 'BIO', 'TECH', 'BIIB', 'BMRN', 'BIRK', 'BJ', 'BX', 'HRB',
    'XYZ', 'OWL', 'BNY', 'BOKF', 'BAH', 'BWA', 'SAM', 'BSX', 'BYD', 'BFAM',
    'BHF', 'BMY', 'BRX', 'BAM', 'BEPC', 'BRO', 'BF-A', 'BF-B', 'BRKR',
    'BC', 'BLDR', 'BLSH', 'BG', 'BURL', 'BXP', 'CHRW', 'CACI', 'CDNS',
    'CZR', 'CPT', 'CPB', 'COF', 'CAI', 'CSL', 'CG', 'KMX', 'CRS',
    'CARR', 'CVNA', 'CASY', 'CAVA', 'CBOE', 'CBRE', 'CDW', 'CE', 'COR',
    'CNP', 'CBC', 'CERT', 'CF', 'CRL', 'CHTR', 'CHE', 'LNG', 'CHWY', 'CHH',
    'CHRD', 'CB', 'CHD', 'CHDN', 'CIEN', 'CI', 'CINF', 'CTAS', 'CRUS', 'CFG',
    'CLVT', 'CLH', 'CWEN', 'CLX', 'NET', 'CME', 'CMS', 'CNA', 'CNH', 'COKE',
    'CGNX', 'CTSH', 'COHR', 'CL', 'COLB', 'COLM', 'FIX', 'CBSH', 'CAG', 'CNXC',
    'ED', 'STZ', 'COO', 'CPRT', 'CORT', 'CNM', 'GLW', 'CPAY', 'CTVA',
    'CSGP', 'COTY', 'CUZ', 'CR', 'CXT', 'CACC', 'CRH', 'CROX', 'CRWD', 'CCI',
    'CCK', 'CSX', 'CUBE', 'CMI', 'DHI', 'DRI', 'DDOG', 'DVA','SQ', 'MEG.TO', 
    'FIL.TO', 'PKI.TO', 'CR.TO']

Periodo = 5  # sempre atualiza/grava os 5 ultimos pregoes
CALC_LOOKBACK = 90  # usa historico salvo + Yahoo recente para recalcular indicadores
DOWNLOAD_PERIOD = "3mo"  # baixa apenas dados recentes do Yahoo

OUTPUT_DIR = r"C:\Users\impar\python-projetos\Base dados Stock\csv ativos"
CSV_OUTPUT = r"C:\Users\impar\python-projetos\Base dados Stock\csv ativos\stocks3.csv"
DB_FILE = r"C:\BD\Stocks.duckdb"
os.makedirs(OUTPUT_DIR, exist_ok=True)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 5000)

windows = [10]

# =========================
# FUNCOES SEGURAS
# =========================
def safe_anderson(janela):
    try:
        serie = janela.dropna()
        if len(serie) < 3 or serie.nunique() <= 1:
            return np.nan
        return round(anderson(serie, dist="norm").statistic, 2)
    except Exception:
        return np.nan


def safe_zscore(valor_atual, janela):
    try:
        serie = janela.dropna()
        if len(serie) < 2:
            return np.nan
        std = serie.std(ddof=1)
        if pd.isna(std) or std == 0:
            return np.nan
        return round((valor_atual - serie.mean()) / std, 2)
    except Exception:
        return np.nan


def safe_adf(janela):
    try:
        serie = janela.dropna()
        if len(serie) < 4 or serie.nunique() <= 1:
            return np.nan
        return round(adfuller(serie, autolag="AIC")[1], 2)
    except Exception:
        return np.nan


def safe_kpss(janela):
    try:
        serie = janela.dropna()
        if len(serie) < 4 or serie.nunique() <= 1:
            return np.nan
        return round(kpss(serie, nlags="auto")[1], 2)
    except Exception:
        return np.nan


def safe_regression_angle(janela):
    try:
        serie = janela.dropna()
        if len(serie) < 2 or serie.nunique() <= 1:
            return np.nan
        x = np.arange(1, len(serie) + 1)
        slope = np.polyfit(x, serie.to_numpy(), 1)[0]
        return round(np.degrees(np.arctan(slope)), 1)
    except Exception:
        return np.nan


def safe_regression_next_value(janela):
    try:
        serie = janela.dropna()
        if len(serie) < 2 or serie.nunique() <= 1:
            return np.nan
        x = np.arange(1, len(serie) + 1)
        slope, intercept = np.polyfit(x, serie.to_numpy(), 1)
        return round((slope * len(serie)) + intercept, 2)
    except Exception:
        return np.nan


def baixar_historico_lote(tickers, tamanho_lote=50):
    tickers_unicos = list(dict.fromkeys(tickers))
    historicos = {}
    total_lotes = int(np.ceil(len(tickers_unicos) / tamanho_lote))
    print(f"Baixando Yahoo em lote: {len(tickers_unicos)} ativos em {total_lotes} lotes", flush=True)

    for lote_idx, inicio in enumerate(range(0, len(tickers_unicos), tamanho_lote), start=1):
        lote = tickers_unicos[inicio:inicio + tamanho_lote]
        print(f"Lote {lote_idx}/{total_lotes}: {len(lote)} ativos", flush=True)
        try:
            dados_lote = yf.download(
                tickers=lote,
                period=DOWNLOAD_PERIOD,
                auto_adjust=False,
                group_by="ticker",
                threads=True,
                progress=False,
                timeout=30,
            )
        except Exception as e:
            with open(r"C:\DataMarket\ERROR_LOG.txt", "a") as f:
                f.write(f"{datetime.now()} -> LOTE {lote_idx} -> {str(e)}\n")
            dados_lote = pd.DataFrame()

        for ticker_symbol in lote:
            historicos[ticker_symbol] = extrair_ticker_do_lote(dados_lote, ticker_symbol)

    return historicos


def extrair_ticker_do_lote(historico_lote, ticker_symbol):
    if isinstance(historico_lote, dict):
        return historico_lote.get(ticker_symbol, pd.DataFrame()).copy()

    if historico_lote is None or historico_lote.empty:
        return pd.DataFrame()

    if isinstance(historico_lote.columns, pd.MultiIndex):
        if ticker_symbol not in historico_lote.columns.get_level_values(0):
            return pd.DataFrame()
        df = historico_lote[ticker_symbol].copy()
    else:
        df = historico_lote.copy()

    colunas_necessarias = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in colunas_necessarias):
        return pd.DataFrame()

    df = df[colunas_necessarias].dropna(how="all")
    return df



def normalizar_yahoo_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy().reset_index()
    if "Date" not in df.columns:
        primeira_coluna = df.columns[0]
        df = df.rename(columns={primeira_coluna: "Date"})

    colunas_necessarias = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in colunas_necessarias):
        return pd.DataFrame()

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()

    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y%m%d")
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    df.columns = ["data", "Open", "Max", "Min", "close", "volume"]
    df[["Open", "Max", "Min", "close"]] = df[["Open", "Max", "Min", "close"]].round(2)
    return df


def carregar_historico_salvo(con, tickers, lookback):
    tickers_unicos = list(dict.fromkeys(tickers))
    if not tickers_unicos:
        return {}

    con.register("tickers_tmp", pd.DataFrame({"ticker": tickers_unicos}))
    historico = con.execute(
        """
        WITH base AS (
            SELECT
                s.ticker,
                s.data,
                s.Open,
                s.Max,
                s.Min,
                s.close,
                s.volume,
                ROW_NUMBER() OVER (
                    PARTITION BY s.ticker
                    ORDER BY s.data DESC
                ) AS rn
            FROM Stocks_Ind s
            JOIN tickers_tmp t
              ON t.ticker = s.ticker
        )
        SELECT ticker, data, Open, Max, Min, close, volume
        FROM base
        WHERE rn <= ?
        ORDER BY ticker, data
        """,
        [lookback],
    ).fetchdf()

    if historico.empty:
        return {}

    return {
        ticker: grupo.drop(columns=["ticker"]).copy().reset_index(drop=True)
        for ticker, grupo in historico.groupby("ticker", sort=False)
    }


def combinar_historico_salvo_yahoo(historico_salvo, historico_yahoo):
    partes = []
    if historico_salvo is not None and not historico_salvo.empty:
        partes.append(historico_salvo.copy())
    if historico_yahoo is not None and not historico_yahoo.empty:
        partes.append(historico_yahoo.copy())

    if not partes:
        return pd.DataFrame()

    df = pd.concat(partes, ignore_index=True)
    df = df.dropna(subset=["data", "Open", "Max", "Min", "close", "volume"])
    df["data"] = df["data"].astype(str)
    df = df.sort_values("data").drop_duplicates(subset=["data"], keep="last").tail(CALC_LOOKBACK).reset_index(drop=True)
    return df

def processar_ticker(ticker_symbol, historico_lote=None, historico_salvo=None):
    df_yahoo = normalizar_yahoo_df(extrair_ticker_do_lote(historico_lote, ticker_symbol))

    if df_yahoo.empty:
        try:
            df_yahoo = normalizar_yahoo_df(yf.Ticker(ticker_symbol).history(period=DOWNLOAD_PERIOD, auto_adjust=False, timeout=15))
        except Exception:
            df_yahoo = pd.DataFrame()

    if df_yahoo.empty:
        sem_yahoo.append(ticker_symbol)  # so historico salvo: regrava os dias antigos, sem o pregao de hoje
    df = combinar_historico_salvo_yahoo(historico_salvo, df_yahoo)

    if df.empty:
        return pd.DataFrame({"ticker": [ticker_symbol], "mensagem": [f"Sem dados salvos/Yahoo para {ticker_symbol}"]})
    df["close-max"] = (df["close"] - df["Max"]).round(2)
    df["close-min"] = (df["close"] - df["Min"]).round(2)
    df["max-min"] = (df["Open"] - df["close"]).round(2)
    df["close-max%"] = ((df["close-max"] / df["close"]) * 100).round(2)
    df["close-min%"] = ((df["close-min"] / df["close"]) * 100).round(2)
    df["max-min%"] = ((df["max-min"] / df["close"]) * 100).round(2)
    df["VR10"] = (df["volume"] / df["volume"].rolling(window=10, min_periods=10).mean()).round(2)
    df["MM5"] = df["close"].rolling(window=5, min_periods=5).mean().round(2)
    df["MM10"] = df["close"].rolling(window=10, min_periods=10).mean().round(2)
    df["MM15"] = df["close"].rolling(window=15, min_periods=15).mean().round(2)
    df["MM50"] = df["close"].rolling(window=50, min_periods=50).mean().round(2)
    df["dist_MM50%"] = ((df["close"] / df["MM50"] - 1) * 100).round(2)
    df["MM_5_10"] = (df["MM5"] - df["MM10"]).round(2)
    df["MM_5_15"] = (df["MM5"] - df["MM15"]).round(2)
    # MOMENTUM (% sobre o close de N periodos atras) e MEDIA MOVEL EXPONENCIAL (k = 2 / (N + 1))
    for n in (5, 10, 20):
        df[f"MOM{n}"] = ((df["close"] / df["close"].shift(n) - 1) * 100).round(2)
        df[f"MME{n}"] = df["close"].ewm(span=n, adjust=False, min_periods=n).mean().round(2)

    # DISPERSAO
    df["ln"] = np.log(df["volume"] / df["volume"].shift(1)).round(2)
    df["stdev"] = df["ln"].rolling(window=3, min_periods=3).std(ddof=1)
    df["vol_abs"] = (1 + df["stdev"]) ** 3
    df["vol"] = df["vol_abs"] - 1
    df["dis.vl"] = (df["vol"] * 100).round(2)

    # ESTATISTICAS
    for window in windows:
        df[f"And_{window}"] = np.nan
        df[f"Z_{window}d"] = np.nan
        df[f"ADF{window}"] = np.nan
        df[f"KP{window}"] = np.nan

        for i in range(window - 1, len(df)):
            janela = df.loc[i - window + 1:i, "close"]
            df.loc[i, f"And_{window}"] = safe_anderson(janela)
            df.loc[i, f"Z_{window}d"] = safe_zscore(df.loc[i, "close"], janela)
            df.loc[i, f"ADF{window}"] = safe_adf(janela)
            df.loc[i, f"KP{window}"] = safe_kpss(janela)

    df["RL10"] = df["close"].rolling(window=10, min_periods=10).apply(
        safe_regression_angle,
        raw=False
    )
    df["RL15"] = df["close"].rolling(window=15, min_periods=15).apply(
        safe_regression_angle,
        raw=False
    )
    df["y_estimado"] = df["close"].rolling(window=10, min_periods=10).apply(
        safe_regression_next_value,
        raw=False
    )

    df_resultado = df.tail(Periodo).copy()
    df_resultado.insert(0, "ticker", ticker_symbol)
    df_resultado.insert(0, "Stock_ID", df_resultado["ticker"].astype(str) + "_" + df_resultado["data"].astype(str))

    colunas_final = ["Stock_ID", "ticker", "data", "Open", "Max", "Min", "close", "volume", "dis.vl"] + [
        col for col in df_resultado.columns
        if "And_" in col or "Z_" in col or "ADF" in col or "KP" in col
    ] + ["RL10", "RL15", "y_estimado", "MM5", "MM10", "MM15", "MM50", "dist_MM50%", "MM_5_10", "MM_5_15", "MOM5", "MOM10", "MOM20", "MME5", "MME10", "MME20", "close-max", "close-min", "max-min", "close-max%", "close-min%", "max-min%", "VR10"]

    return df_resultado[colunas_final]


# =========================
# BUSCA E CALCULO
# =========================
resultados = []
sem_yahoo = []  # tickers sem nenhum dado do Yahoo (lote e fallback individual)
stocks_para_processar = list(dict.fromkeys(stocks))

con_hist = duckdb.connect(DB_FILE)
historico_salvo_por_ticker = carregar_historico_salvo(con_hist, stocks_para_processar, CALC_LOOKBACK)
con_hist.close()
print(f"Historico salvo carregado de Stocks_Ind: {len(historico_salvo_por_ticker)} ativos", flush=True)

historico_lote = baixar_historico_lote(stocks_para_processar)

total_stocks = len(stocks_para_processar)
for idx, ticker_symbol in enumerate(stocks_para_processar, start=1):
    print(f"[{idx}/{total_stocks}] Processando {ticker_symbol}", flush=True)
    with open(r"C:\DataMarket\CURRENT_TICKER.txt", "w") as f:
        f.write(f"{datetime.now()} -> {idx}/{total_stocks} {ticker_symbol}\n")

    try:
        resultados.append(processar_ticker(ticker_symbol, historico_lote, historico_salvo_por_ticker.get(ticker_symbol)))
        print(f"[{idx}/{total_stocks}] OK {ticker_symbol}", flush=True)
    except Exception as e:
        print(f"[{idx}/{total_stocks}] ERRO {ticker_symbol}: {e}", flush=True)
        with open(r"C:\DataMarket\ERROR_LOG.txt", "a") as f:
            f.write(f"{datetime.now()} -> {ticker_symbol} -> {str(e)}\n")
        resultados.append(pd.DataFrame({"ticker": [ticker_symbol], "mensagem": [f"Erro ao buscar/processar {ticker_symbol}: {e}"]}))

final = pd.concat(resultados, ignore_index=True) if resultados else pd.DataFrame({"mensagem": ["Nenhum resultado gerado"]})
final.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")

# =========================
# GRAVA RESULTADO EM STOCKS_IND
# =========================
db_cols = ["ticker", "data", "Open", "Max", "Min", "close", "volume", "dis.vl", "And_10", "Z_10d", "ADF10", "KP10", "RL10", "RL15", "y_estimado", "MM5", "MM10", "MM15", "MM50", "dist_MM50%", "MM_5_10", "MM_5_15", "MOM5", "MOM10", "MOM20", "MME5", "MME10", "MME20", "close-max", "close-min", "max-min", "close-max%", "close-min%", "max-min%", "VR10", "Stock_ID"]
final_db = final.copy()
if "Stock_ID" in final_db.columns:
    final_db = final_db[final_db["Stock_ID"].notna()].copy()
    final_db = final_db[db_cols]
    con = duckdb.connect(DB_FILE)
    con.execute('ALTER TABLE Stocks_Ind ADD COLUMN IF NOT EXISTS "MM50" DOUBLE')
    con.execute('ALTER TABLE Stocks_Ind ADD COLUMN IF NOT EXISTS "dist_MM50%" DOUBLE')
    for col in ("MOM5", "MOM10", "MOM20", "MME5", "MME10", "MME20"):
        con.execute(f'ALTER TABLE Stocks_Ind ADD COLUMN IF NOT EXISTS "{col}" DOUBLE')
    con.register("final_db", final_db)
    con.execute("CREATE TEMP TABLE ids_atualizar AS SELECT Stock_ID FROM final_db")
    con.execute("DELETE FROM Stocks_Ind WHERE Stock_ID IN (SELECT Stock_ID FROM ids_atualizar)")
    con.execute("INSERT INTO Stocks_Ind BY NAME SELECT * FROM final_db")
    linhas_gravadas = con.execute("SELECT COUNT(*) FROM final_db").fetchone()[0]
    con.close()
else:
    linhas_gravadas = 0

# =========================
# RESULTADO FINAL
# =========================
# Sem impressao no terminal: o resultado e gravado apenas no CSV.

# =========================
# FINAL
# =========================
with open(r"C:\DataMarket\FINAL_STATUS.txt", "a") as f:
    ultima_data = final["data"].dropna().astype(str).max() if "data" in final.columns else None
    f.write(f"{datetime.now()} -> terminou Atualizar_Stocks_Ind incremental ultimos {Periodo} periodos; lookback {CALC_LOOKBACK}; csv {len(final)} linhas; Stocks_Ind atualizadas {linhas_gravadas} linhas; "
            f"Yahoo sem dados {len(sem_yahoo)}/{total_stocks}; ultima data {ultima_data}\n")









