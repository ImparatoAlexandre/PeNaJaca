import math
import sqlite3
import sys
from datetime import date, datetime, timedelta
from statistics import NormalDist

import duckdb
import numpy as np
import pandas as pd


DB_FILE = r"C:\BD\Stocks.duckdb"
SQLITE_FILE = r"C:\Metabase\data\visualizacao.sqlite"

TABELA_DESTINO = "Evolucao_opc_collar"
# Incremental: 1.a execucao do dia (ou --completo) reconstroi tudo; nas seguintes so se recalculam sinais novos, operacoes
# abertas e as com put vencida (ou sem put) ha <= MARGEM_DIAS_FIXA dias; as restantes ficam como estao (resultado fixo).
TABELA_CONTROLE = "Evolucao_opc_collar_controle"   # data da ultima reconstrucao completa
TABELA_PARCIAL = "Evolucao_opc_collar_parcial"     # tabela de trabalho do modo incremental
MARGEM_DIAS_FIXA = 10
MAX_DIAS_ENTRE_OPERACAO_E_OPCAO = 3
DIAS_UTEIS_MINIMOS_VENCIMENTO = 8

# Saida da etapa 3: preco Black-Scholes no Max do stock (mesma formula de "BS precificacao.xlsx")
JUROS_BS = 0.04
VOL_MINIMA_BS = 0.01

# Retorno realista e variante com call OTM (colunas retorno_realista / otm_*)
ETAPA2_QUEDA_PCT = -0.02
OTM_CALL_PCT = 0.08
OTM_ALVO_PCT = 0.02
PUT_PCT_CLOSE = 0.95  # variante p95_*: put com o maior strike <= 95% do close da operacao

_N = NormalDist().cdf


def bs_opcao(spot, strike, data_evolucao, vencimento, vol, tipo):
    if spot is None or strike is None or vol is None or vol < VOL_MINIMA_BS or not data_evolucao or vencimento is None:
        return None
    if vol > 5:  # algumas linhas inseridas manualmente gravam a IV em % (80.0 em vez de 0.80)
        vol = vol / 100
    dia = datetime.strptime(str(data_evolucao), "%Y%m%d").date()
    venc = vencimento if isinstance(vencimento, date) else datetime.strptime(str(vencimento)[:10], "%Y-%m-%d").date()
    dias_uteis = int(np.busday_count(dia + timedelta(days=1), venc + timedelta(days=1)))
    is_call = str(tipo).upper() == "CALL"
    if dias_uteis <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)

    t = dias_uteis / 252
    juros_ln = math.log(1 + JUROS_BS)
    d1 = (math.log(spot / strike) + ((vol ** 2) / 2 + juros_ln) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    desconto = (1 + JUROS_BS) ** t
    if is_call:
        return spot * _N(d1) - strike * _N(d2) / desconto
    return strike * _N(-d2) / desconto - spot * _N(-d1)


def dias_uteis(data_operacao, vencimento):
    # Dias uteis do dia seguinte a operacao ate o vencimento (inclusive).
    # Substitui a subconsulta correlacionada com generate_series, que no DuckDB 1.5.3
    # dava resultados nao deterministicos quando filtrada.
    if not data_operacao or vencimento is None:
        return None
    inicio = datetime.strptime(str(data_operacao), "%Y%m%d").date() + timedelta(days=1)
    return max(int(np.busday_count(inicio, vencimento + timedelta(days=1))), 0)


def filtro_liquidez_desde(con):
    """Ticker promovido pelo Liquidez_opc: so os sinais a partir do dia da promocao (igual ao Opcoes_base)."""
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'Stocks_OPC_liquidez'").fetchall()}
    if "liquidez_desde" not in cols:
        return ""
    return "AND (l.liquidez_desde IS NULL OR CAST(op.data_operacao AS VARCHAR) >= l.liquidez_desde)"


def registrar_funcoes(con):
    con.create_function(
        "dias_uteis",
        dias_uteis,
        ["VARCHAR", "DATE"],
        "BIGINT",
        null_handling="special",
    )
    con.create_function(
        "bs_opcao",
        bs_opcao,
        ["DOUBLE", "DOUBLE", "VARCHAR", "DATE", "DOUBLE", "VARCHAR"],
        "DOUBLE",
        null_handling="special",
    )


def criar_tabela(con, tabela=TABELA_DESTINO, so_alvo=False):
    # CREATE OR REPLACE (sem DROP antes): se a criacao falhar (ex.: falta de memoria), a tabela antiga fica.
    # so_alvo: so as operacoes da tabela temporaria ops_alvo (modo incremental).
    filtro_alvo = (
        "AND EXISTS (SELECT 1 FROM ops_alvo a WHERE a.dataoperacao = CAST(op.data_operacao AS VARCHAR) AND a.ticker = trim(op.ticker))"
        if so_alvo else ""
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {tabela} AS
        WITH operacoes AS (
            SELECT DISTINCT
                op.Stock_ID AS oper_stock_id,
                lower(trim(op.estrategia)) AS origem_pipeline,
                CAST(op.data_operacao AS VARCHAR) AS dataoperacao,
                trim(op.ticker) AS ticker,
                TRY_CAST(op.ordem_selecao_dia AS INTEGER) AS ordem_selecao_dia,
                TRY_CAST(op.close AS DOUBLE) AS close_operacao
            FROM oper_abertas AS op
            INNER JOIN Stocks_OPC_liquidez AS l
                ON trim(l.ticker) = trim(op.ticker)
            WHERE TRY_CAST(op.dia_hist AS INTEGER) = 1
              AND l.liquidez_classificacao IN ('Alta', 'Muito alta')
              {filtro_liquidez_desde(con)}
              {filtro_alvo}
        ),
        opcoes_base AS (
            SELECT
                op.oper_stock_id,
                op.origem_pipeline,
                op.dataoperacao,
                op.ticker,
                op.ordem_selecao_dia,
                op.close_operacao,
                CAST(c.DataRegistro AS VARCHAR) AS data_registro_inicial,
                CAST(c.dataStrike AS DATE) AS data_vencimento,
                TRY_CAST(c.strike AS DOUBLE) AS strike,
                TRY_CAST(c.lastPrice AS DOUBLE) AS valor_put,
                TRY_CAST(c.bid AS DOUBLE) AS bid_put,
                TRY_CAST(c.ask AS DOUBLE) AS ask_put,
                TRY_CAST(c.volume AS BIGINT) AS volume_put,
                TRY_CAST(c.openInterest AS BIGINT) AS openInterest_put,
                TRY_CAST(c.impliedVolatility AS DOUBLE) AS volatilidade_put,
                dias_uteis(op.dataoperacao, CAST(c.dataStrike AS DATE)) AS dias_uteis_ate_vencimento
            FROM operacoes op
            INNER JOIN Call_Put_dados c
                ON upper(trim(c.ticker)) = upper(trim(op.ticker))
               AND upper(trim(c.tipo)) = 'PUT'
               AND CAST(c.DataRegistro AS VARCHAR) >= op.dataoperacao
               AND date_diff(
                    'day',
                    strptime(op.dataoperacao, '%Y%m%d'),
                    strptime(CAST(c.DataRegistro AS VARCHAR), '%Y%m%d')
               ) BETWEEN 0 AND {MAX_DIAS_ENTRE_OPERACAO_E_OPCAO}
               AND CAST(c.dataStrike AS DATE) >= strptime(op.dataoperacao, '%Y%m%d')
        ),
        opcoes_validas AS (
            SELECT *
            FROM opcoes_base
            WHERE dias_uteis_ate_vencimento >= {DIAS_UTEIS_MINIMOS_VENCIMENTO}
        ),
        registro_escolhido AS (
            SELECT
                oper_stock_id,
                origem_pipeline,
                dataoperacao,
                ticker,
                MIN(data_registro_inicial) AS data_registro_inicial
            FROM opcoes_validas
            GROUP BY
                oper_stock_id,
                origem_pipeline,
                dataoperacao,
                ticker
        ),
        vencimento_escolhido AS (
            SELECT
                ov.oper_stock_id,
                ov.origem_pipeline,
                ov.dataoperacao,
                ov.ticker,
                ov.data_registro_inicial,
                MIN(data_vencimento) AS data_vencimento
            FROM opcoes_validas ov
            INNER JOIN registro_escolhido re
                ON re.oper_stock_id = ov.oper_stock_id
               AND re.origem_pipeline = ov.origem_pipeline
               AND re.dataoperacao = ov.dataoperacao
               AND re.ticker = ov.ticker
               AND re.data_registro_inicial = ov.data_registro_inicial
            GROUP BY
                ov.oper_stock_id,
                ov.origem_pipeline,
                ov.dataoperacao,
                ov.ticker,
                ov.data_registro_inicial
        ),
        strikes_vencimento AS (
            SELECT ov.*
            FROM opcoes_validas ov
            INNER JOIN vencimento_escolhido ve
                ON ve.oper_stock_id = ov.oper_stock_id
               AND ve.origem_pipeline = ov.origem_pipeline
               AND ve.dataoperacao = ov.dataoperacao
               AND ve.ticker = ov.ticker
               AND ve.data_registro_inicial = ov.data_registro_inicial
               AND ve.data_vencimento = ov.data_vencimento
        ),
        strike_no_dinheiro AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        oper_stock_id,
                        origem_pipeline,
                        dataoperacao,
                        ticker
                    ORDER BY
                        ABS(strike - close_operacao),
                        strike
                ) AS rn_no_dinheiro
            FROM strikes_vencimento
        ),
        puts_abaixo AS (
            SELECT
                p.*,
                nd.strike AS strike_no_dinheiro,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        p.oper_stock_id,
                        p.origem_pipeline,
                        p.dataoperacao,
                        p.ticker
                    ORDER BY p.strike DESC
                ) AS rn_abaixo_no_dinheiro
            FROM strikes_vencimento p
            INNER JOIN strike_no_dinheiro nd
                ON nd.oper_stock_id = p.oper_stock_id
               AND nd.origem_pipeline = p.origem_pipeline
               AND nd.dataoperacao = p.dataoperacao
               AND nd.ticker = p.ticker
               AND nd.rn_no_dinheiro = 1
            WHERE p.strike < nd.strike
        ),
        put_escolhida AS MATERIALIZED (
            SELECT *
            FROM puts_abaixo
            WHERE rn_abaixo_no_dinheiro = 4
        ),
        etapa2_rank AS (
            SELECT
                op.oper_stock_id,
                op.origem_pipeline,
                op.dataoperacao,
                op.ticker,
                CAST(s.data AS VARCHAR) AS data_etapa2,
                TRY_CAST(s.Min AS DOUBLE) AS minima_etapa2,
                TRY_CAST(s.close AS DOUBLE) AS close_etapa2,
                ROUND(((TRY_CAST(s.Min AS DOUBLE) - op.close_operacao) / op.close_operacao) * 100, 2) AS queda_min_etapa2_pct,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        op.oper_stock_id,
                        op.origem_pipeline,
                        op.dataoperacao,
                        op.ticker
                    ORDER BY CAST(s.data AS VARCHAR)
                ) AS rn_etapa2
            FROM operacoes op
            LEFT JOIN put_escolhida p
                ON p.oper_stock_id = op.oper_stock_id
               AND p.origem_pipeline = op.origem_pipeline
               AND p.dataoperacao = op.dataoperacao
               AND p.ticker = op.ticker
            INNER JOIN Stocks_Ind s
                ON upper(trim(s.ticker)) = upper(trim(op.ticker))
               AND CAST(s.data AS VARCHAR) > op.dataoperacao
               AND TRY_CAST(s.Min AS DOUBLE) IS NOT NULL
               AND ((TRY_CAST(s.Min AS DOUBLE) - op.close_operacao) / op.close_operacao) * 100 <= -2
               -- etapa 2 so vale ate o vencimento da put (sem put, mantem a etapa 2 so informativa)
               AND (p.data_vencimento IS NULL OR strptime(CAST(s.data AS VARCHAR), '%Y%m%d') <= p.data_vencimento)
        ),
        etapa2 AS (
            SELECT *
            FROM etapa2_rank
            WHERE rn_etapa2 = 1
        ),
        call_atm_rank AS (
            SELECT
                e.oper_stock_id,
                e.origem_pipeline,
                e.dataoperacao,
                e.ticker,
                TRY_CAST(c.strike AS DOUBLE) AS strike_call_no_dinheiro,
                TRY_CAST(c.lastPrice AS DOUBLE) AS valor_call_no_dinheiro,
                TRY_CAST(c.bid AS DOUBLE) AS bid_call_no_dinheiro,
                TRY_CAST(c.ask AS DOUBLE) AS ask_call_no_dinheiro,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        e.oper_stock_id,
                        e.origem_pipeline,
                        e.dataoperacao,
                        e.ticker
                    ORDER BY
                        ABS(TRY_CAST(c.strike AS DOUBLE) - e.minima_etapa2),
                        TRY_CAST(c.strike AS DOUBLE)
                ) AS rn_call_atm
            FROM etapa2 e
            INNER JOIN put_escolhida p
                ON p.oper_stock_id = e.oper_stock_id
               AND p.origem_pipeline = e.origem_pipeline
               AND p.dataoperacao = e.dataoperacao
               AND p.ticker = e.ticker
            INNER JOIN Call_Put_dados c
                ON upper(trim(c.ticker)) = upper(trim(e.ticker))
               AND upper(trim(c.tipo)) = 'CALL'
               AND CAST(c.DataRegistro AS VARCHAR) = e.data_etapa2
               AND CAST(c.dataStrike AS DATE) = p.data_vencimento
        ),
        call_atm AS (
            SELECT *
            FROM call_atm_rank
            WHERE rn_call_atm = 1
        ),
        etapa3_raw AS (
            SELECT
                e.oper_stock_id,
                e.origem_pipeline,
                e.dataoperacao,
                e.ticker,
                CAST(s.data AS VARCHAR) AS data_evolucao,
                TRY_CAST(s.close AS DOUBLE) AS close_evolucao,
                TRY_CAST(s.Max AS DOUBLE) AS max_evolucao,
                ROUND(((TRY_CAST(s.Max AS DOUBLE) - e.minima_etapa2) / e.minima_etapa2) * 100, 2) AS variacao_pct,
                ROUND(((TRY_CAST(s.Max AS DOUBLE) - e.minima_etapa2) / e.minima_etapa2) * 100, 2) AS var_atual_ativo,
                COALESCE(
                    bs_opcao(TRY_CAST(s.Max AS DOUBLE), ca.strike_call_no_dinheiro, CAST(s.data AS VARCHAR), CAST(p.data_vencimento AS DATE), call_dia.iv, 'CALL'),
                    TRY_CAST(call_dia.lastPrice AS DOUBLE)
                ) AS valor_call_dia,
                COALESCE(
                    bs_opcao(TRY_CAST(s.Max AS DOUBLE), p.strike, CAST(s.data AS VARCHAR), CAST(p.data_vencimento AS DATE), put_dia.iv, 'PUT'),
                    TRY_CAST(put_dia.lastPrice AS DOUBLE)
                ) AS valor_put_dia,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        e.oper_stock_id,
                        e.origem_pipeline,
                        e.dataoperacao,
                        e.ticker
                    ORDER BY CAST(s.data AS VARCHAR) ASC
                ) AS rn_ultima_etapa3
            FROM etapa2 e
            INNER JOIN operacoes op
                ON op.oper_stock_id = e.oper_stock_id
               AND op.origem_pipeline = e.origem_pipeline
               AND op.dataoperacao = e.dataoperacao
               AND op.ticker = e.ticker
            INNER JOIN put_escolhida p
                ON p.oper_stock_id = e.oper_stock_id
               AND p.origem_pipeline = e.origem_pipeline
               AND p.dataoperacao = e.dataoperacao
               AND p.ticker = e.ticker
            INNER JOIN call_atm ca
                ON ca.oper_stock_id = e.oper_stock_id
               AND ca.origem_pipeline = e.origem_pipeline
               AND ca.dataoperacao = e.dataoperacao
               AND ca.ticker = e.ticker
            INNER JOIN Stocks_Ind s
                ON upper(trim(s.ticker)) = upper(trim(e.ticker))
               AND CAST(s.data AS VARCHAR) > e.data_etapa2
               AND strptime(CAST(s.data AS VARCHAR), '%Y%m%d') <= p.data_vencimento
               AND TRY_CAST(s.Max AS DOUBLE) >= op.close_operacao
            LEFT JOIN (
                SELECT
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike,
                    MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lastPrice,
                    MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
                FROM Call_Put_dados
                WHERE upper(trim(ticker)) IN (SELECT DISTINCT upper(ticker) FROM operacoes)
                GROUP BY
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike
            ) call_dia
                ON upper(trim(call_dia.ticker)) = upper(trim(e.ticker))
               AND upper(trim(call_dia.tipo)) = 'CALL'
               AND CAST(call_dia.DataRegistro AS VARCHAR) = CAST(s.data AS VARCHAR)
               AND CAST(call_dia.dataStrike AS DATE) = p.data_vencimento
               AND TRY_CAST(call_dia.strike AS DOUBLE) = ca.strike_call_no_dinheiro
            LEFT JOIN (
                SELECT
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike,
                    MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lastPrice,
                    MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
                FROM Call_Put_dados
                WHERE upper(trim(ticker)) IN (SELECT DISTINCT upper(ticker) FROM operacoes)
                GROUP BY
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike
            ) put_dia
                ON upper(trim(put_dia.ticker)) = upper(trim(e.ticker))
               AND upper(trim(put_dia.tipo)) = 'PUT'
               AND CAST(put_dia.DataRegistro AS VARCHAR) = CAST(s.data AS VARCHAR)
               AND CAST(put_dia.dataStrike AS DATE) = p.data_vencimento
               AND TRY_CAST(put_dia.strike AS DOUBLE) = p.strike
        ),
        etapa3_calc AS (
            SELECT
                r.*,
                ROUND(-p.valor_put, 2) AS entrada_put,
                ROUND(-e.minima_etapa2, 2) AS entrada_ativo,
                ROUND(ca.valor_call_no_dinheiro, 2) AS entrada_call,
                ROUND(r.valor_put_dia, 2) AS saida_put,
                ROUND(r.max_evolucao, 2) AS saida_ativo,
                ROUND(-r.valor_call_dia, 2) AS saida_call,
                ROUND(
                    (
                        (r.max_evolucao - r.valor_call_dia + r.valor_put_dia)
                        - (e.minima_etapa2 - ca.valor_call_no_dinheiro + p.valor_put)
                    ) / NULLIF((e.minima_etapa2 - ca.valor_call_no_dinheiro + p.valor_put), 0) * 100,
                    2
                ) AS retorno
            FROM etapa3_raw r
            INNER JOIN etapa2 e
                ON e.oper_stock_id = r.oper_stock_id
               AND e.origem_pipeline = r.origem_pipeline
               AND e.dataoperacao = r.dataoperacao
               AND e.ticker = r.ticker
            INNER JOIN put_escolhida p
                ON p.oper_stock_id = r.oper_stock_id
               AND p.origem_pipeline = r.origem_pipeline
               AND p.dataoperacao = r.dataoperacao
               AND p.ticker = r.ticker
            INNER JOIN call_atm ca
                ON ca.oper_stock_id = r.oper_stock_id
               AND ca.origem_pipeline = r.origem_pipeline
               AND ca.dataoperacao = r.dataoperacao
               AND ca.ticker = r.ticker
        ),
        etapa3_ultima AS MATERIALIZED (
            SELECT
                oper_stock_id,
                origem_pipeline,
                dataoperacao,
                ticker,
                data_evolucao,
                variacao_pct,
                var_atual_ativo,
                entrada_put,
                entrada_ativo,
                entrada_call,
                saida_put,
                saida_ativo,
                saida_call,
                retorno
            FROM etapa3_calc
            WHERE rn_ultima_etapa3 = 1
        ),
        base_final AS MATERIALIZED (
            SELECT
                op.oper_stock_id,
                op.origem_pipeline,
                op.ordem_selecao_dia,
                op.dataoperacao,
                op.ticker,
                ROUND(op.close_operacao, 2) AS close_operacao,
                p.data_registro_inicial AS data_registro_opcao,
                ROUND(p.strike_no_dinheiro, 2) AS strike_no_dinheiro,
                ROUND(p.strike, 2) AS strike_put,
                CAST(p.data_vencimento AS VARCHAR) AS vencimento_put,
                ROUND(p.valor_put, 2) AS valor_put,
                p.dias_uteis_ate_vencimento,
                ROUND(p.bid_put, 2) AS bid_put,
                ROUND(p.ask_put, 2) AS ask_put,
                ROUND(p.volatilidade_put, 2) AS volatilidade_put,
                e.data_etapa2 AS data_etapa2,
                ROUND(e.minima_etapa2, 2) AS minima_etapa2,
                e.queda_min_etapa2_pct AS queda_min_etapa2_pct,
                ROUND(ca.strike_call_no_dinheiro, 2) AS strike_call_no_dinheiro,
                ROUND(ca.valor_call_no_dinheiro, 2) AS valor_call_no_dinheiro
            FROM operacoes op
            LEFT JOIN put_escolhida p
                ON p.oper_stock_id = op.oper_stock_id
               AND p.origem_pipeline = op.origem_pipeline
               AND p.dataoperacao = op.dataoperacao
               AND p.ticker = op.ticker
            LEFT JOIN etapa2 e
                ON e.oper_stock_id = op.oper_stock_id
               AND e.origem_pipeline = op.origem_pipeline
               AND e.dataoperacao = op.dataoperacao
               AND e.ticker = op.ticker
            LEFT JOIN call_atm ca
                ON ca.oper_stock_id = op.oper_stock_id
               AND ca.origem_pipeline = op.origem_pipeline
               AND ca.dataoperacao = op.dataoperacao
               AND ca.ticker = op.ticker
        )
        SELECT
            bf.dataoperacao AS dataoperacao,
            bf.ticker AS ticker,
            bf.close_operacao AS close_oper,
            bf.strike_put AS strike_put,
            bf.valor_put AS valor_put,
            bf.bid_put AS bid_put,
            bf.ask_put AS ask_put,
            bf.vencimento_put AS venc_put,
            bf.dias_uteis_ate_vencimento AS dias_venc,
            bf.volatilidade_put AS vol_put,
            bf.data_etapa2 AS "ETAPA_2_data",
            bf.minima_etapa2 AS "MIN_etapa2",
            bf.queda_min_etapa2_pct AS "%_MIN",
            bf.strike_call_no_dinheiro AS strike_call,
            bf.valor_call_no_dinheiro AS valor_call,
            (SELECT eu.data_evolucao FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS "ETAPA3_data",
            (SELECT eu.variacao_pct FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS "var_MAX_ativo",
            (SELECT eu.var_atual_ativo FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS "varAtualativo",
            (SELECT eu.retorno FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS retorno,
            (SELECT eu.entrada_put FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS entrada_put,
            (SELECT eu.entrada_ativo FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS entrada_ativo,
            (SELECT eu.entrada_call FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS entrada_call,
            (SELECT eu.saida_put FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS saida_put,
            (SELECT eu.saida_ativo FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS saida_ativo,
            (SELECT eu.saida_call FROM etapa3_ultima eu WHERE eu.oper_stock_id = bf.oper_stock_id AND eu.origem_pipeline = bf.origem_pipeline AND eu.dataoperacao = bf.dataoperacao AND eu.ticker = bf.ticker) AS saida_call
        FROM base_final bf
        ORDER BY
            bf.dataoperacao DESC,
            bf.origem_pipeline,
            bf.ordem_selecao_dia,
            bf.ticker
        """
    )


def preencher_retorno_sem_etapa3(con, tabela=TABELA_DESTINO):
    """Preenche retorno (e entrada_* / saida_*) das operacoes sem etapa 3 cuja put ja venceu.

    Sem etapa 2: so ha a put; retorno = (valor da put no vencimento - premio) / close_oper.
    Com etapa 2 e sem etapa 3: liquida ativo, put e call ao close do dia do vencimento (valor intrinseco das opcoes);
    retorno sobre o custo da etapa 2 (MIN_etapa2 - call + put), como na etapa 3.
    Put ainda por vencer: retorno fica vazio (operacao aberta).
    """
    base = con.execute(
        f"""
        SELECT d.dataoperacao, d.ticker, d.close_oper, d.strike_put, d.valor_put, CAST(d.venc_put AS DATE) AS venc,
               d."ETAPA_2_data" AS e2, d."MIN_etapa2" AS min_e2, d.strike_call, d.valor_call,
               s.close AS close_venc
        FROM {tabela} d
        JOIN LATERAL (
            SELECT TRY_CAST(close AS DOUBLE) AS close
            FROM Stocks_Ind
            WHERE upper(trim(ticker)) = upper(trim(d.ticker))
              AND strptime(CAST(data AS VARCHAR), '%Y%m%d') <= CAST(d.venc_put AS DATE)
            ORDER BY CAST(data AS VARCHAR) DESC
            LIMIT 1
        ) s ON TRUE
        WHERE d.strike_put IS NOT NULL
          AND d.valor_put IS NOT NULL
          AND d."ETAPA3_data" IS NULL
          AND d.retorno IS NULL
          AND (SELECT max(strptime(CAST(data AS VARCHAR), '%Y%m%d')) FROM Stocks_Ind
               WHERE upper(trim(ticker)) = upper(trim(d.ticker))) >= CAST(d.venc_put AS DATE)
        """
    ).fetchdf()
    if base.empty:
        return
    linhas = []
    for r in base.itertuples():
        S = float(r.close_venc)
        put_s = max(r.strike_put - S, 0.0)
        out = dict(dataoperacao=r.dataoperacao, ticker=r.ticker, entrada_put=round(-r.valor_put, 2), saida_put=round(put_s, 2))
        if r.e2 is None or r.e2 != r.e2:
            out["retorno"] = round((put_s - r.valor_put) / r.close_oper * 100, 2)
        elif r.strike_call == r.strike_call and r.valor_call == r.valor_call and r.min_e2 == r.min_e2:
            call_s = max(S - r.strike_call, 0.0)
            custo = r.min_e2 - r.valor_call + r.valor_put
            out.update(entrada_ativo=round(-r.min_e2, 2), entrada_call=round(r.valor_call, 2), saida_ativo=round(S, 2),
                       saida_call=round(-call_s, 2), retorno=round((S - call_s + put_s - custo) / custo * 100, 2))
        else:
            continue
        linhas.append(out)
    if not linhas:
        return
    df = pd.DataFrame(linhas).reindex(columns=["dataoperacao", "ticker", "retorno", "entrada_put", "entrada_ativo",
                                               "entrada_call", "saida_put", "saida_ativo", "saida_call"])
    con.register("df_sem_e3", df)
    con.execute(
        f"""
        UPDATE {tabela} AS d
        SET retorno = r.retorno,
            entrada_put = r.entrada_put,
            entrada_ativo = COALESCE(r.entrada_ativo, d.entrada_ativo),
            entrada_call = COALESCE(r.entrada_call, d.entrada_call),
            saida_put = r.saida_put,
            saida_ativo = COALESCE(r.saida_ativo, d.saida_ativo),
            saida_call = COALESCE(r.saida_call, d.saida_call)
        FROM df_sem_e3 r
        WHERE d.dataoperacao = r.dataoperacao AND d.ticker = r.ticker AND d.retorno IS NULL
        """
    )
    con.unregister("df_sem_e3")


def _dias_uteis_entre(inicio, fim):
    a = datetime.strptime(str(inicio), "%Y%m%d").date()
    b = fim if isinstance(fim, date) else datetime.strptime(str(fim).replace("-", "")[:8], "%Y%m%d").date()
    return max(int(np.busday_count(a, b)), 1)


def calcular_retorno_realista(con, tabela=TABELA_DESTINO):
    """Acrescenta colunas com execucao realista e a variante com call OTM.

    retorno (atual) compra no Min e vende no Max do dia, o que nao e executavel.
    retorno_realista: compra com ordem limite a -2% do close da operacao (ou na abertura, se abrir abaixo)
    e vende no close da operacao (ou na abertura, se abrir acima). Operacoes ja vencidas sem etapa 3
    recebem o resultado no vencimento.
    otm_*: mesma entrada, call com strike >= entrada * (1 + OTM_CALL_PCT), saida a entrada * (1 + OTM_ALVO_PCT).
    p95_*: igual a otm_*, mas com a put de maior strike <= close * PUT_PCT_CLOSE (saida na mesma data de otm_data_saida).
    """
    base = con.execute(
        f"""
        SELECT dataoperacao, ticker, close_oper, strike_put, valor_put, venc_put, vol_put,
               "ETAPA_2_data" AS e2, strike_call, valor_call, "ETAPA3_data" AS e3
        FROM {tabela}
        WHERE strike_put IS NOT NULL
        """
    ).fetchdf()
    if base.empty:
        return

    tickers = ",".join("'" + t.replace("'", "''") + "'" for t in base.ticker.unique())
    precos = con.execute(
        f"""
        SELECT ticker, CAST(data AS VARCHAR) AS d, TRY_CAST(Open AS DOUBLE) AS o, TRY_CAST(Max AS DOUBLE) AS h,
               TRY_CAST(close AS DOUBLE) AS c
        FROM Stocks_Ind WHERE ticker IN ({tickers}) ORDER BY ticker, d
        """
    ).fetchdf()
    px = {t: g.set_index("d") for t, g in precos.groupby("ticker")}
    opc = con.execute(
        f"""
        SELECT upper(trim(ticker)) AS ticker, upper(trim(tipo)) AS tipo, CAST(DataRegistro AS VARCHAR) AS dr,
               CAST(dataStrike AS DATE) AS venc, TRY_CAST(strike AS DOUBLE) AS strike,
               MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lp, MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
        FROM Call_Put_dados WHERE upper(trim(ticker)) IN ({tickers})
        GROUP BY ALL
        """
    ).fetchdf()
    opc["venc"] = pd.to_datetime(opc.venc).dt.date
    opc_idx ={k: (r.lp, r.iv) for k, r in zip(zip(opc.ticker, opc.tipo, opc.dr, opc.venc, opc.strike), opc.itertuples())}

    def preco_opcao(ticker, tipo, dia, venc, strike, spot, vol_fallback):
        lp, iv = opc_idx.get((ticker, tipo, dia, venc, strike), (None, None))
        v = bs_opcao(spot, strike, dia, venc, iv if iv and iv == iv else vol_fallback, tipo)
        return v if v is not None else lp

    def payoff_venc(S, kc, kp):
        return S - max(S - kc, 0.0) + max(kp - S, 0.0)

    linhas = []
    for r in base.itertuples():
        g = px.get(r.ticker)
        if g is None:
            continue
        venc = r.venc_put if isinstance(r.venc_put, date) else datetime.strptime(str(r.venc_put)[:10], "%Y-%m-%d").date()
        venc_s = venc.strftime("%Y%m%d")
        vencida = g.index[-1] >= venc_s
        S_venc = g.c[g.index <= venc_s].iloc[-1] if vencida else None
        C = r.close_oper
        L = C * (1 + ETAPA2_QUEDA_PCT)
        out = dict(dataoperacao=r.dataoperacao, ticker=r.ticker)

        # put alternativa: maior strike <= close * PUT_PCT_CLOSE, no 1.o registro ate 3 dias apos a operacao
        d_op = datetime.strptime(r.dataoperacao, "%Y%m%d").date()
        lim_reg = (d_op + timedelta(days=MAX_DIAS_ENTRE_OPERACAO_E_OPCAO)).strftime("%Y%m%d")
        puts = opc[(opc.ticker == r.ticker) & (opc.tipo == "PUT") & (opc.venc == venc) & (opc.dr >= r.dataoperacao)
                   & (opc.dr <= lim_reg) & (opc.strike <= C * PUT_PCT_CLOSE) & (opc.lp > 0)]
        p95 = None
        if not puts.empty:
            puts = puts[puts.dr == puts.dr.min()].sort_values("strike")
            p95 = (puts.strike.iloc[-1], puts.lp.iloc[-1])
            out.update(p95_strike_put=p95[0], p95_valor_put=round(p95[1], 2))

        def perda_sem_e2(kp, put0):
            return round((max(kp - S_venc, 0.0) - put0) / C * 100, 2)

        if r.e2 is None or r.e2 != r.e2:
            if vencida:
                dias_cap = _dias_uteis_entre(r.dataoperacao, venc)
                out.update(status_oper="sem_e2_vencida", retorno_realista=perda_sem_e2(r.strike_put, r.valor_put),
                           dias_capital=dias_cap, otm_retorno=perda_sem_e2(r.strike_put, r.valor_put), otm_dias_capital=dias_cap)
                if p95:
                    out.update(p95_retorno=perda_sem_e2(*p95), p95_dias_capital=dias_cap)
            else:
                out.update(status_oper="aberta_sem_e2")
            linhas.append(out)
            continue

        entrada = min(g.o.get(r.e2, L), L)
        out["entrada_real"] = round(entrada, 2)
        vol_put = r.vol_put if r.vol_put == r.vol_put else None

        # regra atual com execucao realista
        if r.strike_call == r.strike_call and r.valor_call == r.valor_call:
            # call de entrada avaliada no preco de entrada (o lastPrice e de outro momento do dia)
            call0 = preco_opcao(r.ticker, "CALL", r.e2, venc, r.strike_call, entrada, None) or r.valor_call
            out["valor_call_real"] = round(call0, 2)
            custo = entrada - call0 + r.valor_put
            if r.e3 is not None and r.e3 == r.e3:
                S = max(g.o.get(r.e3, C), C)
                call_s = preco_opcao(r.ticker, "CALL", r.e3, venc, r.strike_call, S, None)
                put_s = preco_opcao(r.ticker, "PUT", r.e3, venc, r.strike_put, S, vol_put)
                if call_s is not None and put_s is not None:
                    out.update(status_oper="etapa3", saida_real=round(S, 2),
                               retorno_realista=round((S - call_s + put_s - custo) / custo * 100, 2),
                               dias_capital=_dias_uteis_entre(r.dataoperacao, r.e3))
            elif vencida:
                out.update(status_oper="preso_vencida", saida_real=round(S_venc, 2),
                           retorno_realista=round((payoff_venc(S_venc, r.strike_call, r.strike_put) - custo) / custo * 100, 2),
                           dias_capital=_dias_uteis_entre(r.dataoperacao, venc))
            else:
                out.update(status_oper="aberta_e2")

        # variantes com call OTM: otm_* (put atual, 4 strikes abaixo) e p95_* (put a PUT_PCT_CLOSE do close)
        calls = opc[(opc.ticker == r.ticker) & (opc.tipo == "CALL") & (opc.dr == r.e2) & (opc.venc == venc)
                    & (opc.strike >= entrada * (1 + OTM_CALL_PCT)) & (opc.lp > 0)].sort_values("strike")
        if not calls.empty:
            kc, iv0 = calls.strike.iloc[0], calls.iv.iloc[0]
            call0 = preco_opcao(r.ticker, "CALL", r.e2, venc, kc, entrada, None) or calls.lp.iloc[0]
            alvo = entrada * (1 + OTM_ALVO_PCT)
            out.update(otm_strike_call=kc, otm_valor_call=round(call0, 2))
            dias = g[(g.index > r.e2) & (g.index <= venc_s) & (g.h >= alvo)]

            def variante_otm(prefixo, kp, put0):
                custo = entrada - call0 + put0
                if not dias.empty:
                    d = dias.index[0]
                    S = max(dias.o.iloc[0], alvo)
                    call_s = preco_opcao(r.ticker, "CALL", d, venc, kc, S, iv0)
                    put_s = preco_opcao(r.ticker, "PUT", d, venc, kp, S, vol_put)
                    if call_s is not None and put_s is not None:
                        out.update({f"{prefixo}_data_saida": d,
                                    f"{prefixo}_retorno": round((S - call_s + put_s - custo) / custo * 100, 2),
                                    f"{prefixo}_dias_capital": _dias_uteis_entre(r.dataoperacao, d)})
                elif vencida:
                    out.update({f"{prefixo}_retorno": round((payoff_venc(S_venc, kc, kp) - custo) / custo * 100, 2),
                                f"{prefixo}_dias_capital": _dias_uteis_entre(r.dataoperacao, venc)})

            variante_otm("otm", r.strike_put, r.valor_put)
            if p95:
                variante_otm("p95", *p95)
        linhas.append(out)

    colunas = ["dataoperacao", "ticker", "status_oper", "entrada_real", "valor_call_real", "saida_real", "retorno_realista", "dias_capital",
               "otm_strike_call", "otm_valor_call", "otm_data_saida", "otm_retorno", "otm_dias_capital",
               "p95_strike_put", "p95_valor_put", "p95_retorno", "p95_dias_capital"]
    df = pd.DataFrame(linhas).reindex(columns=colunas)
    df = df.astype({"status_oper": "object", "otm_data_saida": "object"})
    con.register("df_realista", df)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {tabela} AS
        SELECT d.*, r.* EXCLUDE (dataoperacao, ticker)
        FROM {tabela} d
        LEFT JOIN df_realista r USING (dataoperacao, ticker)
        ORDER BY d.dataoperacao DESC, d.ticker
        """
    )
    con.unregister("df_realista")


def sincronizar_sqlite(con):
    df = con.execute(f"SELECT * FROM {TABELA_DESTINO}").fetchdf()
    sq = sqlite3.connect(SQLITE_FILE)
    try:
        df.to_sql(TABELA_DESTINO, sq, if_exists="replace", index=False)
    finally:
        sq.close()
    return len(df)


def preencher_puts_faltantes(con, tabela=TABELA_DESTINO):
    con.execute(
        f"""
        WITH operacoes AS (
            SELECT DISTINCT
                op.Stock_ID AS oper_stock_id,
                lower(trim(op.estrategia)) AS origem_pipeline,
                CAST(op.data_operacao AS VARCHAR) AS dataoperacao,
                trim(op.ticker) AS ticker,
                TRY_CAST(op.ordem_selecao_dia AS INTEGER) AS ordem_selecao_dia,
                TRY_CAST(op.close AS DOUBLE) AS close_operacao
            FROM oper_abertas AS op
            INNER JOIN Stocks_OPC_liquidez AS l
                ON trim(l.ticker) = trim(op.ticker)
            INNER JOIN {tabela} AS destino
                ON destino.dataoperacao = CAST(op.data_operacao AS VARCHAR)
               AND upper(trim(destino.ticker)) = upper(trim(op.ticker))
            WHERE TRY_CAST(op.dia_hist AS INTEGER) = 1
              AND l.liquidez_classificacao IN ('Alta', 'Muito alta')
              {filtro_liquidez_desde(con)}
              AND destino.strike_put IS NULL
        ),
        opcoes_base AS (
            SELECT
                op.oper_stock_id,
                op.origem_pipeline,
                op.dataoperacao,
                op.ticker,
                op.ordem_selecao_dia,
                op.close_operacao,
                CAST(c.DataRegistro AS VARCHAR) AS data_registro_inicial,
                CAST(c.dataStrike AS DATE) AS data_vencimento,
                TRY_CAST(c.strike AS DOUBLE) AS strike,
                TRY_CAST(c.lastPrice AS DOUBLE) AS valor_put,
                TRY_CAST(c.bid AS DOUBLE) AS bid_put,
                TRY_CAST(c.ask AS DOUBLE) AS ask_put,
                TRY_CAST(c.impliedVolatility AS DOUBLE) AS volatilidade_put,
                dias_uteis(op.dataoperacao, CAST(c.dataStrike AS DATE)) AS dias_uteis_ate_vencimento
            FROM operacoes op
            INNER JOIN Call_Put_dados c
                ON upper(trim(c.ticker)) = upper(trim(op.ticker))
               AND upper(trim(c.tipo)) = 'PUT'
               AND CAST(c.DataRegistro AS VARCHAR) >= op.dataoperacao
               AND date_diff(
                    'day',
                    strptime(op.dataoperacao, '%Y%m%d'),
                    strptime(CAST(c.DataRegistro AS VARCHAR), '%Y%m%d')
               ) BETWEEN 0 AND {MAX_DIAS_ENTRE_OPERACAO_E_OPCAO}
               AND CAST(c.dataStrike AS DATE) >= strptime(op.dataoperacao, '%Y%m%d')
        ),
        opcoes_validas AS (
            SELECT *
            FROM opcoes_base
            WHERE dias_uteis_ate_vencimento >= {DIAS_UTEIS_MINIMOS_VENCIMENTO}
        ),
        registro_escolhido AS (
            SELECT
                oper_stock_id,
                origem_pipeline,
                dataoperacao,
                ticker,
                MIN(data_registro_inicial) AS data_registro_inicial
            FROM opcoes_validas
            GROUP BY
                oper_stock_id,
                origem_pipeline,
                dataoperacao,
                ticker
        ),
        vencimento_escolhido AS (
            SELECT
                ov.oper_stock_id,
                ov.origem_pipeline,
                ov.dataoperacao,
                ov.ticker,
                ov.data_registro_inicial,
                MIN(data_vencimento) AS data_vencimento
            FROM opcoes_validas ov
            INNER JOIN registro_escolhido re
                ON re.oper_stock_id = ov.oper_stock_id
               AND re.origem_pipeline = ov.origem_pipeline
               AND re.dataoperacao = ov.dataoperacao
               AND re.ticker = ov.ticker
               AND re.data_registro_inicial = ov.data_registro_inicial
            GROUP BY
                ov.oper_stock_id,
                ov.origem_pipeline,
                ov.dataoperacao,
                ov.ticker,
                ov.data_registro_inicial
        ),
        strikes_vencimento AS (
            SELECT ov.*
            FROM opcoes_validas ov
            INNER JOIN vencimento_escolhido ve
                ON ve.oper_stock_id = ov.oper_stock_id
               AND ve.origem_pipeline = ov.origem_pipeline
               AND ve.dataoperacao = ov.dataoperacao
               AND ve.ticker = ov.ticker
               AND ve.data_registro_inicial = ov.data_registro_inicial
               AND ve.data_vencimento = ov.data_vencimento
        ),
        strike_no_dinheiro AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        oper_stock_id,
                        origem_pipeline,
                        dataoperacao,
                        ticker
                    ORDER BY
                        ABS(strike - close_operacao),
                        strike
                ) AS rn_no_dinheiro
            FROM strikes_vencimento
        ),
        puts_abaixo AS (
            SELECT
                p.*,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        p.oper_stock_id,
                        p.origem_pipeline,
                        p.dataoperacao,
                        p.ticker
                    ORDER BY p.strike DESC
                ) AS rn_abaixo_no_dinheiro
            FROM strikes_vencimento p
            INNER JOIN strike_no_dinheiro nd
                ON nd.oper_stock_id = p.oper_stock_id
               AND nd.origem_pipeline = p.origem_pipeline
               AND nd.dataoperacao = p.dataoperacao
               AND nd.ticker = p.ticker
               AND nd.rn_no_dinheiro = 1
            WHERE p.strike < nd.strike
        ),
        put_escolhida AS MATERIALIZED (
            SELECT *
            FROM puts_abaixo
            WHERE rn_abaixo_no_dinheiro = 4
        )
        UPDATE {tabela} AS destino
        SET
            strike_put = ROUND(p.strike, 2),
            valor_put = ROUND(p.valor_put, 2),
            bid_put = ROUND(p.bid_put, 2),
            ask_put = ROUND(p.ask_put, 2),
            venc_put = CAST(p.data_vencimento AS VARCHAR),
            dias_venc = p.dias_uteis_ate_vencimento,
            vol_put = ROUND(p.volatilidade_put, 2)
        FROM put_escolhida p
        WHERE destino.dataoperacao = p.dataoperacao
          AND upper(trim(destino.ticker)) = upper(trim(p.ticker))
          AND destino.strike_put IS NULL
        """
    )


def preencher_calls_faltantes(con, tabela=TABELA_DESTINO):
    con.execute(
        f"""
        WITH calls_rank AS (
            SELECT
                destino.dataoperacao,
                destino.ticker,
                TRY_CAST(c.strike AS DOUBLE) AS strike_call,
                TRY_CAST(c.lastPrice AS DOUBLE) AS valor_call,
                ROW_NUMBER() OVER (
                    PARTITION BY destino.dataoperacao, upper(trim(destino.ticker))
                    ORDER BY
                        ABS(TRY_CAST(c.strike AS DOUBLE) - destino."MIN_etapa2"),
                        TRY_CAST(c.strike AS DOUBLE)
                ) AS rn_call
            FROM {tabela} AS destino
            INNER JOIN Call_Put_dados c
                ON upper(trim(c.ticker)) = upper(trim(destino.ticker))
               AND upper(trim(c.tipo)) = 'CALL'
               AND CAST(c.DataRegistro AS VARCHAR) = destino."ETAPA_2_data"
               AND CAST(c.dataStrike AS DATE) = CAST(destino.venc_put AS DATE)
            WHERE destino.strike_put IS NOT NULL
              AND destino."ETAPA_2_data" IS NOT NULL
              AND destino."MIN_etapa2" IS NOT NULL
              AND destino.venc_put IS NOT NULL
              AND destino.strike_call IS NULL
        ),
        call_escolhida AS (
            SELECT *
            FROM calls_rank
            WHERE rn_call = 1
        )
        UPDATE {tabela} AS destino
        SET
            strike_call = ROUND(c.strike_call, 2),
            valor_call = ROUND(c.valor_call, 2)
        FROM call_escolhida c
        WHERE destino.dataoperacao = c.dataoperacao
          AND upper(trim(destino.ticker)) = upper(trim(c.ticker))
          AND destino.strike_call IS NULL
        """
    )


def preencher_etapa3_faltante(con, tabela=TABELA_DESTINO):
    con.execute(
        f"""
        WITH etapa3_raw AS (
            SELECT
                destino.dataoperacao,
                destino.ticker,
                CAST(s.data AS VARCHAR) AS data_evolucao,
                TRY_CAST(s.close AS DOUBLE) AS close_evolucao,
                TRY_CAST(s.Max AS DOUBLE) AS max_evolucao,
                COALESCE(
                    bs_opcao(TRY_CAST(s.Max AS DOUBLE), destino.strike_call, CAST(s.data AS VARCHAR), CAST(destino.venc_put AS DATE), call_dia.iv, 'CALL'),
                    TRY_CAST(call_dia.lastPrice AS DOUBLE)
                ) AS valor_call_dia,
                COALESCE(
                    bs_opcao(TRY_CAST(s.Max AS DOUBLE), destino.strike_put, CAST(s.data AS VARCHAR), CAST(destino.venc_put AS DATE), put_dia.iv, 'PUT'),
                    TRY_CAST(put_dia.lastPrice AS DOUBLE)
                ) AS valor_put_dia,
                ROUND(((TRY_CAST(s.Max AS DOUBLE) - destino."MIN_etapa2") / destino."MIN_etapa2") * 100, 2) AS variacao_pct,
                ROUND(((TRY_CAST(s.Max AS DOUBLE) - destino."MIN_etapa2") / destino."MIN_etapa2") * 100, 2) AS var_atual_ativo,
                ROW_NUMBER() OVER (
                    PARTITION BY destino.dataoperacao, upper(trim(destino.ticker))
                    ORDER BY CAST(s.data AS VARCHAR) ASC
                ) AS rn_ultima_etapa3
            FROM {tabela} AS destino
            INNER JOIN Stocks_Ind s
                ON upper(trim(s.ticker)) = upper(trim(destino.ticker))
               AND CAST(s.data AS VARCHAR) > destino."ETAPA_2_data"
               AND strptime(CAST(s.data AS VARCHAR), '%Y%m%d') <= CAST(destino.venc_put AS DATE)
               AND TRY_CAST(s.Max AS DOUBLE) >= destino.close_oper
            LEFT JOIN (
                SELECT
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike,
                    MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lastPrice,
                    MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
                FROM Call_Put_dados
                WHERE upper(trim(ticker)) IN (SELECT DISTINCT upper(trim(ticker)) FROM {tabela})
                GROUP BY
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike
            ) call_dia
                ON upper(trim(call_dia.ticker)) = upper(trim(destino.ticker))
               AND upper(trim(call_dia.tipo)) = 'CALL'
               AND CAST(call_dia.DataRegistro AS VARCHAR) = CAST(s.data AS VARCHAR)
               AND CAST(call_dia.dataStrike AS DATE) = CAST(destino.venc_put AS DATE)
               AND TRY_CAST(call_dia.strike AS DOUBLE) = destino.strike_call
            LEFT JOIN (
                SELECT
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike,
                    MAX(TRY_CAST(lastPrice AS DOUBLE)) AS lastPrice,
                    MAX(TRY_CAST(impliedVolatility AS DOUBLE)) AS iv
                FROM Call_Put_dados
                WHERE upper(trim(ticker)) IN (SELECT DISTINCT upper(trim(ticker)) FROM {tabela})
                GROUP BY
                    ticker,
                    tipo,
                    DataRegistro,
                    dataStrike,
                    strike
            ) put_dia
                ON upper(trim(put_dia.ticker)) = upper(trim(destino.ticker))
               AND upper(trim(put_dia.tipo)) = 'PUT'
               AND CAST(put_dia.DataRegistro AS VARCHAR) = CAST(s.data AS VARCHAR)
               AND CAST(put_dia.dataStrike AS DATE) = CAST(destino.venc_put AS DATE)
               AND TRY_CAST(put_dia.strike AS DOUBLE) = destino.strike_put
            WHERE destino.strike_put IS NOT NULL
              AND destino.valor_put IS NOT NULL
              AND destino.venc_put IS NOT NULL
              AND destino."ETAPA_2_data" IS NOT NULL
              AND destino."MIN_etapa2" IS NOT NULL
              AND destino.strike_call IS NOT NULL
              AND destino.valor_call IS NOT NULL
              AND destino."ETAPA3_data" IS NULL
        ),
        etapa3_escolhida AS (
            SELECT
                r.dataoperacao,
                r.ticker,
                r.data_evolucao,
                r.variacao_pct,
                r.var_atual_ativo,
                ROUND(-destino.valor_put, 2) AS entrada_put,
                ROUND(-destino."MIN_etapa2", 2) AS entrada_ativo,
                ROUND(destino.valor_call, 2) AS entrada_call,
                ROUND(r.valor_put_dia, 2) AS saida_put,
                ROUND(r.max_evolucao, 2) AS saida_ativo,
                ROUND(-r.valor_call_dia, 2) AS saida_call,
                ROUND(
                    (
                        (r.max_evolucao - r.valor_call_dia + r.valor_put_dia)
                        - (destino."MIN_etapa2" - destino.valor_call + destino.valor_put)
                    ) / NULLIF((destino."MIN_etapa2" - destino.valor_call + destino.valor_put), 0) * 100,
                    2
                ) AS retorno
            FROM etapa3_raw r
            INNER JOIN {tabela} destino
                ON destino.dataoperacao = r.dataoperacao
               AND upper(trim(destino.ticker)) = upper(trim(r.ticker))
            WHERE rn_ultima_etapa3 = 1
        )
        UPDATE {tabela} AS destino
        SET
            "ETAPA3_data" = e.data_evolucao,
            "var_MAX_ativo" = e.variacao_pct,
            "varAtualativo" = e.var_atual_ativo,
            entrada_put = e.entrada_put,
            entrada_ativo = e.entrada_ativo,
            entrada_call = e.entrada_call,
            saida_put = e.saida_put,
            saida_ativo = e.saida_ativo,
            saida_call = e.saida_call,
            retorno = e.retorno
        FROM etapa3_escolhida e
        WHERE destino.dataoperacao = e.dataoperacao
          AND upper(trim(destino.ticker)) = upper(trim(e.ticker))
          AND destino."ETAPA3_data" IS NULL
        """
    )


def calcular(con, tabela, so_alvo=False):
    criar_tabela(con, tabela, so_alvo)
    preencher_puts_faltantes(con, tabela)
    preencher_calls_faltantes(con, tabela)
    preencher_etapa3_faltante(con, tabela)
    preencher_retorno_sem_etapa3(con, tabela)
    calcular_retorno_realista(con, tabela)


def completa_feita_hoje(con, hoje):
    existentes = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if TABELA_DESTINO not in existentes or TABELA_CONTROLE not in existentes:
        return False
    return con.execute(f"SELECT max(ultima_completa) FROM {TABELA_CONTROLE}").fetchone()[0] == hoje


def marcar_alvo(con):
    """ops_alvo = operacoes a recalcular (sinais novos, abertas e put vencida ha <= MARGEM_DIAS_FIXA dias);
    ops_fixas = operacoes ja na tabela com resultado fixo (put vencida, ou sem put, ha mais de MARGEM_DIAS_FIXA dias)."""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ops_fixas AS
        SELECT dataoperacao, ticker
        FROM {TABELA_DESTINO}
        GROUP BY dataoperacao, ticker
        HAVING bool_and(CASE WHEN venc_put IS NOT NULL
                             THEN CAST(venc_put AS DATE) < current_date - {MARGEM_DIAS_FIXA}
                             ELSE strptime(dataoperacao, '%Y%m%d') < current_date - {MARGEM_DIAS_FIXA} END)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ops_alvo AS
        SELECT DISTINCT CAST(op.data_operacao AS VARCHAR) AS dataoperacao, trim(op.ticker) AS ticker
        FROM oper_abertas AS op
        INNER JOIN Stocks_OPC_liquidez AS l ON trim(l.ticker) = trim(op.ticker)
        WHERE TRY_CAST(op.dia_hist AS INTEGER) = 1
          AND l.liquidez_classificacao IN ('Alta', 'Muito alta')
          {filtro_liquidez_desde(con)}
          AND NOT EXISTS (SELECT 1 FROM ops_fixas f
                          WHERE f.dataoperacao = CAST(op.data_operacao AS VARCHAR) AND f.ticker = trim(op.ticker))
        """
    )
    return con.execute("SELECT count(*) FROM ops_alvo").fetchone()[0], con.execute("SELECT count(*) FROM ops_fixas").fetchone()[0]


def juntar_parcial(con):
    """Tabela final = linhas fixas + operacoes recalculadas (colunas e tipos da tabela final)."""
    tipos = con.execute(f"DESCRIBE {TABELA_DESTINO}").fetchall()
    parcial = {r[0] for r in con.execute(f"DESCRIBE {TABELA_PARCIAL}").fetchall()}
    sel = ", ".join(
        f'CAST(p."{nome}" AS {tipo}) AS "{nome}"' if nome in parcial else f'CAST(NULL AS {tipo}) AS "{nome}"'
        for nome, tipo, *_ in tipos
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {TABELA_DESTINO} AS
        SELECT * FROM (
            SELECT d.* FROM {TABELA_DESTINO} d
            WHERE EXISTS (SELECT 1 FROM ops_fixas f WHERE f.dataoperacao = d.dataoperacao AND f.ticker = d.ticker)
            UNION ALL
            SELECT {sel} FROM {TABELA_PARCIAL} p
        )
        ORDER BY dataoperacao DESC, ticker
        """
    )
    con.execute(f"DROP TABLE {TABELA_PARCIAL}")


def main(completo=None):
    """completo=None: reconstroi tudo na 1.a execucao do dia (ou com --completo); nas seguintes, incremental."""
    con = duckdb.connect(DB_FILE, config={"memory_limit": "2GB"})
    registrar_funcoes(con)
    hoje = datetime.now().strftime("%Y%m%d")
    if completo is None:
        completo = "--completo" in sys.argv or not completa_feita_hoje(con, hoje)
    if completo:
        calcular(con, TABELA_DESTINO)
        con.execute(f"CREATE OR REPLACE TABLE {TABELA_CONTROLE} AS SELECT '{hoje}' AS ultima_completa")
        modo = "completa"
    else:
        n_alvo, n_fixas = marcar_alvo(con)
        calcular(con, TABELA_PARCIAL, so_alvo=True)
        juntar_parcial(con)
        modo = f"incremental: {n_alvo} operacoes recalculadas, {n_fixas} fixas"
    qtd = sincronizar_sqlite(con)

    print(f"Tabela {TABELA_DESTINO} criada com {qtd} linhas ({modo}).")
    print(
        con.execute(
            f"""
            SELECT *
            FROM {TABELA_DESTINO}
            ORDER BY dataoperacao DESC, ticker
            LIMIT 80
            """
        ).fetchdf().to_string(index=False)
    )
    con.close()


if __name__ == "__main__":
    main()






