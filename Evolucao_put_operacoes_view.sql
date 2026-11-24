-- Cria a view Evolucao_put_operacoes (SQLite do Metabase e DuckDB). Corre uma vez, a view acompanha as tabelas.
-- Modelos (o modelo stock, tabela Evolucao_stock, saiu da view em 2026-11-11):
--   etapa1:       (tabela Evolucao_call_put) compra call delta 0.30 e put delta -0.30 (1 call + 1 put, retorno do par), so sinais ext ordem 1,
--                 IV da call e da put <= 75%, sai quando o ativo toca +7% ou -7%, sem alvo sai ao close do D7 (status corte)
--                 (alvo_saida = alvo de alta, stop_saida = alvo de baixa)
--   etapa2:       (tabela Evolucao_vol_call_put) igual a etapa1, mas entre os sinais ext ordem 1 a 3 entra 1 par por dia, o de menor vol media (call + put) / 2,
--                 sem par com IV da call ou da put acima de 80%, alvo +10% ou -10%, sem alvo sai ao close do D5 (status corte)
--   etapa3:       (tabela Evolucao_etapa3, Evolucao_etapa3.py) regra segura: igual a etapa1 (call 0.30 + put -0.30), sinais ext ordem 1 a 3
--                 com VR10 < 2.5 e vol_hist20 > 70%, IV da call e da put entre 60% e 80%, alvo +7% ou -7%, sem alvo sai ao close do D5
--   etapa4:       (tabela Evolucao_etapa4, mesmo script) regra ousada: mesmos sinais da etapa3, IV da call e da put <= 100%,
--                 alvo +10% ou -10%, sem alvo sai ao close do D5
--   etapa13:      (tabela Evolucao_etapa13, Evolucao_etapa13.py) etapa1 + etapa3 num so capital unico, com os buracos: nos dias sem
--                 sinal das etapas 1/3, sinais da ordem so entre tickers com opcoes liquidas (oper_abertas_liq, amplitude >= 10%)
--                 com as mesmas regras da etapa1 (ordem 1) e da etapa3; sinal = origem (etapa1, etapa3, liq_etapa1, liq_etapa3)
--   etapa5:       (tabela Evolucao_etapa5, Evolucao_etapa5.py) sinais da view Variacoes_liq (liquidez Alta / Muito alta,
--                 amp_med20 >= 8, amp_med5 >= amp_med20, |dist_MM50%| > 25), travados em Sinais_etapa5 na 1.a rodada; call 0.30 + put -0.30
--                 sem limite de IV (validada a mao na tabela Vol), alvo +11% ou -11%, sem alvo sai ao close do D5;
--                 ticker com operacao aberta nao entra de novo ate a saida (ordem = ordem no dia pela amp_med20)
-- ordem: ordem_selecao_dia do sinal (todos os modelos)
-- liquidez, MOM20, dist_MM50%, VR10, vol_hist20, MIN %, MAX %, maior_mov %: todas as etapas (Opcoes_base.calcular_indicadores; MIN / MAX do D1 ao D7 face ao close do sinal)
-- hora: hora:minuto (HH:MM) da rodada em que o sinal entrou em oper_abertas (1.a selecao do dia)
-- vol_call / vol_put: volatilidade implicita (%) usada no preco de entrada de cada opcao
-- lucro_operacao: etapa1 a etapa4 e etapa13 = lucro de cada operacao fechada com 5000 (todos os sinais, tambem os que entram com outra operacao aberta;
--                 capital_unico = 1 marca so os que caberiam com uma operacao de cada vez)
-- Exemplo: SELECT * FROM Evolucao_put_operacoes WHERE modelo IN ('etapa1', 'etapa2', 'etapa3', 'etapa4', 'etapa13', 'etapa5') ORDER BY dataoperacao DESC

DROP VIEW IF EXISTS Evolucao_put_operacoes;

CREATE VIEW Evolucao_put_operacoes AS
WITH ops AS (
    SELECT 'etapa1' AS modelo, dataoperacao, hora, ticker, estrategia AS sinal, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1) AS vol_call, strike_put, valor_put,
           ROUND(vol_put * 100, 1) AS vol_put, venc_put,
           alvo_alta AS alvo_saida, alvo_baixa AS stop_saida, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END AS capital_unico,
           ordem_selecao_dia AS ordem, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_call_put
    UNION ALL
    SELECT 'etapa2', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END,
           ordem_selecao_dia, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_vol_call_put
    UNION ALL
    SELECT 'etapa3', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END,
           ordem_selecao_dia, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_etapa3
    UNION ALL
    SELECT 'etapa4', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END,
           ordem_selecao_dia, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_etapa4
    UNION ALL
    SELECT 'etapa13', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END,
           ordem_selecao_dia, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_etapa13
    UNION ALL
    SELECT 'etapa5', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END,
           ordem_selecao_dia, liquidez, MOM20, "dist_MM50%", VR10, vol_hist20, "MIN %", "MAX %", "maior_mov %"
    FROM Evolucao_etapa5
),
filtradas AS (
    SELECT * FROM ops
    WHERE dataoperacao >= '20251001'
      AND status IN ('alvo', 'corte', 'vencimento', 'alvo_alta', 'alvo_baixa', 'aberta')
)
SELECT
    f.modelo, f.dataoperacao, f.hora, f.ticker, f.sinal, f.close_oper,
    f.vol_hist20, f.vol_call, f.vol_put,
    f.strike_call, f.valor_call, f.strike_put, f.valor_put, f.venc_put,
    f.alvo_saida, f.stop_saida, f.status, f.SAIDA_data, f.saida_ativo, f.saida_call, f.saida_put, f.var_MAX_ativo,
    f.retorno, f.dias_capital, f.capital_unico,
    f.ordem, f.liquidez, f.MOM20, f."dist_MM50%", f.VR10, f."MIN %", f."MAX %", f."maior_mov %",
    CASE WHEN f.modelo IN ('etapa1', 'etapa2', 'etapa3', 'etapa4', 'etapa13', 'etapa5') AND f.status <> 'aberta'
         THEN ROUND(5000 * f.retorno / 100.0, 2) END AS lucro_operacao
FROM filtradas f;
