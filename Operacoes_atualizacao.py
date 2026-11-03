#=========================
# OPERACOES ATUALIZACAO
# =========================
import ctypes
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import sqlite3

BASE_DIR = Path(r"C:\Users\impar\python-projetos\Base dados Stock")
LOG_FILE = Path(r"C:\DataMarket\FINAL_STATUS.txt")
DB_FILE = Path(r"C:\BD\Stocks.duckdb")
SQLITE_FILE = Path(r"C:\Metabase\data\visualizacao.sqlite")
LOCK_FILE = BASE_DIR / "Operacoes_atualizacao.lock"
LOCK_MAX_MIN = 45        # trava mais antiga que isto e de uma rodada que morreu: e ignorada
ABERTURA_MERCADO = (9, 35)  # dia util depois disto (hora de Nova York) a Stocks_Ind tem de ter o pregao de hoje
DIAS_STOCKS_SQLITE = 10  # sincronizacao final: pregoes da Stocks_Ind regravados no SQLite (Atualizar_Stocks_Ind mexe nos ultimos 5)

SCRIPTS = [
    "Atualizar_Stocks_Ind.py",
    "Operacoes_fase1.py",
    "Operacoes_fase2.py",
    "Operacoes_abertas.py",
    "Operacoes_abertas_ext.py",
    "Evolucao.py",
    "Atualizacao_opc.py",
    "Evolucao_opc_collar.py",
    "Evolucao_stock.py",
    "Evolucao_call_put.py",
    "Evolucao_vol_call_put.py",
    "Posicao_momento.py",
    "Vol_momento.py",
    "Volume_OPC_strike.py",
]


def log(msg):
    texto = f"{datetime.now()} -> {msg}"
    print(texto, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(texto + "\n")


def periodo_automatico():
    data_de = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
    data_ate = datetime.now().strftime("%Y%m%d")
    return data_de, data_ate


def stocks_ind_atualizada_hoje():
    hoje = datetime.now().strftime("%Y%m%d")
    if not DB_FILE.exists():
        return False

    con = duckdb.connect(str(DB_FILE), read_only=True)
    try:
        ultima_data, tickers_hoje = con.execute(
            """
            SELECT
                MAX(CAST(data AS VARCHAR)) AS ultima_data,
                COUNT(DISTINCT ticker) FILTER (WHERE CAST(data AS VARCHAR) = ?) AS tickers_hoje
            FROM Stocks_Ind
            """,
            [hoje],
        ).fetchone()
    finally:
        con.close()

    return ultima_data == hoje and tickers_hoje and tickers_hoje > 100


def rodar_script(nome_script):
    script_path = BASE_DIR / nome_script
    if not script_path.exists():
        raise FileNotFoundError(f"Script nao encontrado: {script_path}")


    comando = [sys.executable, str(script_path)]
    if nome_script == "Operacoes_fase2.py":
        data_de, data_ate = periodo_automatico()
        comando.extend([data_de, data_ate])
        log(f"PERIODO automatico {nome_script}: {data_de} ate {data_ate}")

    log(f"INICIO {nome_script}")
    resultado = subprocess.Popen(comando, cwd=str(BASE_DIR), text=True)
    sem_abrandamento(int(resultado._handle))  # o script filho tambem fica fora do abrandamento do Windows
    resultado.wait()

    if resultado.returncode != 0:
        raise RuntimeError(f"Erro ao rodar {nome_script}. Return code: {resultado.returncode}")

    log(f"FIM {nome_script}")



def sincronizar_metabase_sqlite():
    """Atualiza o SQLite usado pelo Metabase com as tabelas principais."""
    SQLITE_FILE.parent.mkdir(parents=True, exist_ok=True)

    con_duck = duckdb.connect(str(DB_FILE), read_only=True)
    con_sqlite = sqlite3.connect(str(SQLITE_FILE))
    try:
        df_oper_abertas = con_duck.execute("""
            SELECT
                CAST(data_operacao AS VARCHAR) AS data_operacao,
                CAST("Time" AS VARCHAR) AS "Time",
                hora,
                perspectiva_operacao,
                estrategia,
                ordem_selecao_dia,
                close,
                * EXCLUDE (data_operacao, "Time", hora, perspectiva_operacao, estrategia, ordem_selecao_dia, close)
            FROM oper_abertas
            ORDER BY CAST(data_operacao AS VARCHAR) DESC, ordem_selecao_dia, ticker, dia_hist DESC
        """).fetchdf()
        df_oper_abertas.to_sql("oper_abertas", con_sqlite, if_exists="replace", index=False)

        linhas_stocks, modo_stocks = sincronizar_stocks_ind(con_duck, con_sqlite)

        df_evolucao = con_duck.execute("""
            SELECT *
            FROM Evolucao
            ORDER BY CAST(dataoper AS VARCHAR) DESC, ordem, ticker
        """).fetchdf()
        df_evolucao.to_sql("Evolucao", con_sqlite, if_exists="replace", index=False)

        log(
            f"SQLite Metabase sincronizado: {len(df_oper_abertas)} linhas em oper_abertas, "
            f"{linhas_stocks} linhas em Stocks_Ind ({modo_stocks}) e "
            f"{len(df_evolucao)} linhas em Evolucao"
        )
    except Exception as e:
        log(f"ERRO na sincronizacao do SQLite Metabase: {e}")
        raise
    finally:
        con_sqlite.close()
        con_duck.close()


def sincronizar_stocks_ind(con_duck, con_sqlite):
    """Stocks_Ind no SQLite: regrava so os ultimos DIAS_STOCKS_SQLITE pregoes (numa transacao).

    A tabela inteira (~210 mil linhas) so e regravada se faltar no SQLite, se as colunas mudarem ou se a contagem
    final nao bater com a DuckDB (ex.: ticker novo com historico). Menos escrita = menos risco com o Metabase
    (Docker) a ler o arquivo durante a gravacao.
    """
    colunas = [r[0] for r in con_duck.execute("DESCRIBE Stocks_Ind").fetchall()]
    colunas_sqlite = [r[1] for r in con_sqlite.execute("PRAGMA table_info('Stocks_Ind')").fetchall()]
    total = con_duck.execute("SELECT count(*) FROM Stocks_Ind").fetchone()[0]
    if colunas_sqlite == colunas:
        desde = con_duck.execute(
            f"""SELECT MIN(d) FROM (SELECT DISTINCT CAST(data AS VARCHAR) AS d FROM Stocks_Ind
                ORDER BY d DESC LIMIT {DIAS_STOCKS_SQLITE})"""
        ).fetchone()[0]
        df = con_duck.execute(
            "SELECT * FROM Stocks_Ind WHERE CAST(data AS VARCHAR) >= ? ORDER BY ticker, CAST(data AS VARCHAR)", [desde]
        ).fetchdf()
        con_sqlite.execute("DELETE FROM Stocks_Ind WHERE CAST(data AS TEXT) >= ?", [desde])
        df.to_sql("Stocks_Ind", con_sqlite, if_exists="append", index=False)
        con_sqlite.commit()  # o DELETE tambem, mesmo sem linhas novas
        if con_sqlite.execute("SELECT count(*) FROM Stocks_Ind").fetchone()[0] == total:
            return len(df), f"desde {desde}"

    df = con_duck.execute("SELECT * FROM Stocks_Ind ORDER BY ticker, CAST(data AS VARCHAR)").fetchdf()
    df.to_sql("Stocks_Ind", con_sqlite, if_exists="replace", index=False)
    return len(df), "tabela inteira"

def processo_vivo(pid):
    """True se o processo pid ainda existe (Windows); na duvida devolve True."""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        codigo = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(codigo))
        k32.CloseHandle(h)
        return codigo.value == 259  # STILL_ACTIVE
    except Exception:
        return True


def adquirir_trava():
    """Cria LOCK_FILE; False se outra rodada ainda esta a correr.

    A trava e ignorada se o processo que a criou ja nao existe (janela fechada, queda) ou se tem mais de
    LOCK_MAX_MIN minutos.
    """
    if LOCK_FILE.exists():
        texto = LOCK_FILE.read_text(encoding="utf-8").strip()
        idade_min = (datetime.now().timestamp() - LOCK_FILE.stat().st_mtime) / 60
        pid = texto.split("pid ")[-1] if "pid " in texto else None
        if pid and not processo_vivo(pid):
            log(f"Trava de rodada interrompida ({texto}) ignorada: o processo ja nao existe")
        elif idade_min < LOCK_MAX_MIN:
            log(f"OUTRA RODADA EM CURSO ({texto}, ha {idade_min:.0f} min) - atualizacao nao executada")
            return False
        else:
            log(f"Trava antiga ({idade_min:.0f} min) ignorada")
    LOCK_FILE.write_text(f"inicio {datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()}", encoding="utf-8")
    return True


def sem_abrandamento(handle=None):
    """Tira o processo (o proprio ou o do handle) do abrandamento de energia do Windows (EcoQoS / power throttling)."""
    try:
        from ctypes import wintypes

        class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL
        estado = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0x0)  # EXECUTION_SPEED controlado e desligado
        alvo = wintypes.HANDLE(handle) if handle else k32.GetCurrentProcess()
        return bool(k32.SetProcessInformation(alvo, 4, ctypes.byref(estado), ctypes.sizeof(estado)))  # ProcessPowerThrottling
    except Exception:
        return False


def modo_rodada(ativo):
    """Durante a rodada: tela acesa, sistema ativo, prioridade normal e sem abrandamento de energia.

    Com a tela apagada (mesmo sem dormir) o Windows abranda muito os processos: Atualizar_Stocks_Ind chegou a ficar
    ~5x mais lento, e a tarefa agendada arranca com prioridade 7 (abaixo do normal). Se a tela ja estiver apagada,
    um movimento de 1 pixel do rato acende-a; ES_DISPLAY_REQUIRED mantem-na acesa ate ao fim. Os scripts chamados
    herdam a prioridade e saem do abrandamento em rodar_script. ativo=False devolve o controlo ao Windows (a tela
    volta a apagar pelo tempo normal).
    """
    try:
        from ctypes import wintypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        k32.SetThreadExecutionState.restype = ctypes.c_uint32
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
        if ativo:
            u32.mouse_event(0x0001, 1, 0, 0, 0)   # MOUSEEVENTF_MOVE: acende a tela se estiver apagada
            u32.mouse_event(0x0001, -1, 0, 0, 0)
        ok = k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if ativo else 0)) != 0
        if ativo:
            ok = bool(k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000020)) and ok  # NORMAL_PRIORITY_CLASS
            ok = sem_abrandamento() and ok
        if not ok:
            log(f"modo_rodada({ativo}) nao aplicado por completo pelo Windows")
    except Exception as e:  # fora do Windows ou sem permissao: a rodada continua no modo normal
        log(f"modo_rodada({ativo}) nao aplicado: {e}")


def exige_pregao_hoje(agora):
    return agora.weekday() < 5 and (agora.hour, agora.minute) >= ABERTURA_MERCADO


def main():
    log("INICIO Operacoes_atualizacao")

    hoje = datetime.now()
    if hoje.weekday() >= 5:
        log("FIM DE SEMANA - atualizacao nao executada")
        return

    if not adquirir_trava():
        return
    modo_rodada(True)
    try:
        for script in SCRIPTS:
            rodar_script(script)
            if script == "Atualizar_Stocks_Ind.py" and exige_pregao_hoje(hoje) and not stocks_ind_atualizada_hoje():
                # Yahoo sem o pregao de hoje (falha / limite de pedidos, ou feriado): nao reprocessa o dia anterior como se fosse hoje
                log("SEM PREGAO DE HOJE em Stocks_Ind (Yahoo sem dados ou feriado) - restantes scripts nao executados")
                return

        sincronizar_metabase_sqlite()

        log("FIM Operacoes_atualizacao")
    finally:
        modo_rodada(False)
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()






