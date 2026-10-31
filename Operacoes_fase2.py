# =========================
# OPERACOES FASE 2
# =========================
import sys
import duckdb
import pandas as pd
import sys
from datetime import datetime

DB_FILE = r"C:\BD\Stocks.duckdb"
LOG_FILE = r"C:\DataMarket\FINAL_STATUS.txt"


def pedir_data(nome):
    valor = input(f"{nome} (AAAAMMDD): ").strip().lstrip("\ufeff")
    if len(valor) != 8 or not valor.isdigit():
        raise ValueError(f"Data invalida para {nome}: {valor}. Use AAAAMMDD.")
    return valor


def obter_periodo():
    if len(sys.argv) >= 3:
        data_de = sys.argv[1].strip().lstrip("\ufeff")
        data_ate = sys.argv[2].strip().lstrip("\ufeff")
        if len(data_de) != 8 or not data_de.isdigit():
            raise ValueError(f"Data invalida para DE: {data_de}. Use AAAAMMDD.")
        if len(data_ate) != 8 or not data_ate.isdigit():
            raise ValueError(f"Data invalida para ATE: {data_ate}. Use AAAAMMDD.")
        return data_de, data_ate

    data_de = pedir_data("DE")
    data_ate = pedir_data("ATE")
    return data_de, data_ate


def montar_grupo_fase2(con, fase1_row):
    fase1_id = fase1_row["Stock_ID"]
    ticker = fase1_row["ticker"]
    data_inicio = fase1_row["data"]

    serie = con.execute(
        """
        SELECT *
        FROM Stocks_Ind
        WHERE ticker = ?
          AND data >= ?
        ORDER BY data
        """,
        [ticker, data_inicio],
    ).fetchdf()

    if serie.empty:
        return pd.DataFrame()

    fim_idx = len(serie) - 1
    encontrou_operacao = False
    for i in range(1, len(serie)):
        kp_anterior = serie.loc[i - 1, "KP10"]
        kp_atual = serie.loc[i, "KP10"]
        if pd.notna(kp_anterior) and pd.notna(kp_atual):
            if round(float(kp_atual), 2) > round(float(kp_anterior), 2):
                fim_idx = i
                encontrou_operacao = True
                break

    grupo = serie.iloc[:fim_idx + 1].copy()
    grupo.insert(0, "fase1_id", fase1_id)
    grupo.insert(1, "fase1_data", data_inicio)
    grupo.insert(2, "seq_fase2", range(1, len(grupo) + 1))

    grupo["status"] = "evolucao"
    grupo["perspectiva"] = None
    grupo.loc[grupo.index[0], "status"] = "entrada"

    if encontrou_operacao and len(grupo) > 1:
        grupo.loc[grupo.index[-1], "status"] = "operacao"

        close_ontem = grupo.iloc[-2]["close"]
        close_hoje = grupo.iloc[-1]["close"]
        close_max_pct = grupo.iloc[-1]["close-max%"]
        close_min_pct = grupo.iloc[-1]["close-min%"]
        y_estimado_hoje = grupo.iloc[-1].get("y_estimado")
        rl15_hoje = grupo.iloc[-1]["RL15"]

        if pd.notna(close_ontem) and pd.notna(close_hoje):
            tendencia = None
            perspectiva_final = None
            close_y_pct = None

            if float(close_hoje) > float(close_ontem):
                tendencia = "Alta"
            elif float(close_hoje) < float(close_ontem):
                tendencia = "Baixa"

            if pd.notna(y_estimado_hoje) and float(close_hoje) != 0:
                close_y_pct = ((float(close_hoje) - float(y_estimado_hoje)) / float(close_hoje)) * 100

            if (
                tendencia == "Alta"
                and close_y_pct is not None
                and close_y_pct > 4
                and pd.notna(rl15_hoje)
                and float(rl15_hoje) < 0
            ):
                perspectiva_final = "Baixa"

            elif (
                tendencia == "Baixa"
                and close_y_pct is not None
                and close_y_pct < -4
                and pd.notna(rl15_hoje)
                and float(rl15_hoje) > 0
            ):
                perspectiva_final = "Alta"

            elif tendencia == "Alta":
                if pd.notna(close_max_pct) and float(close_max_pct) > -1.5:
                    perspectiva_final = "Baixa"
                else:
                    perspectiva_final = "Alta"

            elif tendencia == "Baixa":
                if pd.notna(close_min_pct) and float(close_min_pct) < 1.0:
                    perspectiva_final = "Alta"
                else:
                    perspectiva_final = "Baixa"

            if perspectiva_final is not None:
                grupo.loc[grupo.index[-1], "perspectiva"] = perspectiva_final.lower()

    return grupo


def quote_ident(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def garantir_colunas_oper_fase2(con):
    cols_destino = {row[1] for row in con.execute("PRAGMA table_info('oper_fase2')").fetchall()}
    cols_origem = con.execute("DESCRIBE final_fase2").fetchall()

    for row in cols_origem:
        nome = row[0]
        tipo = row[1]
        if nome not in cols_destino:
            con.execute(f"ALTER TABLE oper_fase2 ADD COLUMN {quote_ident(nome)} {tipo}")
            cols_destino.add(nome)


def main():
    data_de, data_ate = obter_periodo()

    if data_de > data_ate:
        raise ValueError("DE nao pode ser maior que ATE.")

    con = duckdb.connect(DB_FILE)

    fase1 = con.execute(
        """
        SELECT *
        FROM oper_fase1
        WHERE data BETWEEN ? AND ?
        ORDER BY data, ticker, Stock_ID
        """,
        [data_de, data_ate],
    ).fetchdf()

    grupos = []
    for _, fase1_row in fase1.iterrows():
        grupo = montar_grupo_fase2(con, fase1_row)
        if not grupo.empty:
            grupos.append(grupo)

    if grupos:
        final = pd.concat(grupos, ignore_index=True)
    else:
        final = pd.DataFrame()

    if final.empty:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS oper_fase2 AS
            SELECT
                CAST(NULL AS VARCHAR) AS fase1_id,
                CAST(NULL AS VARCHAR) AS fase1_data,
                CAST(NULL AS INTEGER) AS seq_fase2,
                *,
                CAST(NULL AS VARCHAR) AS status,
                CAST(NULL AS VARCHAR) AS perspectiva
            FROM Stocks_Ind
            WHERE 1=0
            """
        )
    else:
        con.register("final_fase2", final)
        con.execute("CREATE TABLE IF NOT EXISTS oper_fase2 AS SELECT * FROM final_fase2 WHERE 1=0")
        garantir_colunas_oper_fase2(con)
        con.execute("CREATE TEMP TABLE ids_fase2 AS SELECT DISTINCT fase1_id FROM final_fase2")
        con.execute("DELETE FROM oper_fase2 WHERE fase1_id IN (SELECT fase1_id FROM ids_fase2)")
        con.execute("INSERT INTO oper_fase2 BY NAME SELECT * FROM final_fase2")

    resumo = con.execute(
        """
        SELECT
            COUNT(DISTINCT fase1_id) AS operacoes_fase1,
            COUNT(*) AS linhas_fase2,
            SUM(CASE WHEN status = 'entrada' THEN 1 ELSE 0 END) AS entradas,
            SUM(CASE WHEN status = 'evolucao' THEN 1 ELSE 0 END) AS evolucoes,
            SUM(CASE WHEN status = 'operacao' THEN 1 ELSE 0 END) AS operacoes
        FROM oper_fase2
        """
    ).fetchdf()

    por_data = con.execute(
        """
        SELECT fase1_data, COUNT(DISTINCT fase1_id) AS operacoes, COUNT(*) AS linhas
        FROM oper_fase2
        GROUP BY fase1_data
        ORDER BY fase1_data
        """
    ).fetchdf()

    con.close()

    print("\n================ RESULTADO FASE 2 =================\n")
    print(resumo.to_string(index=False))
    print("\nPOR DATA\n")
    print(por_data.to_string(index=False))
    print("\n===================================================\n")

    with open(LOG_FILE, "a") as f:
        f.write(
            f"{datetime.now()} -> Operacoes_fase2 DE {data_de} ATE {data_ate}; "
            f"fase1 {len(fase1)}; linhas {len(final)}\n"
        )


if __name__ == "__main__":
    main()


