-- Cria a view Evolucao_put_operacoes (SQLite do Metabase e DuckDB). Corre uma vez, a view acompanha as tabelas.
-- Modelos:
--   stock:        so o ativo, sinais e saidas da Evolucao_stock (alvo +10%, stop -4%), sem stop nem alvo vende no vencimento que a put teria
--   etapa1:       (tabela Evolucao_call_put) compra call delta 0.30 e put delta -0.30 (1 call + 1 put, retorno do par), so sinais ext ordem 1,
--                 IV da call e da put <= 75%, sai quando o ativo toca +7% ou -7%, sem alvo sai ao close do D7 (status corte)
--                 (alvo_saida = alvo de alta, stop_saida = alvo de baixa)
--   etapa2:       (tabela Evolucao_vol_call_put) igual a etapa1, mas entre os sinais ext ordem 1 a 3 entra 1 par por dia, o de menor vol media (call + put) / 2,
--                 sem par com IV da call ou da put acima de 80%, alvo +10% ou -10%, sem alvo sai ao close do D5 (status corte)
-- hora: hora:minuto (HH:MM) da rodada em que o sinal entrou em oper_abertas (1.a selecao do dia)
-- vol_call / vol_put: volatilidade implicita (%) usada no preco de entrada de cada opcao
-- lucro_operacao: etapa1 e etapa2 = lucro de cada operacao fechada com 5000 (todos os sinais, tambem os que entram com outra operacao aberta;
--                 capital_unico = 1 marca so os que caberiam com uma operacao de cada vez)
-- Exemplo: SELECT * FROM Evolucao_put_operacoes WHERE modelo = 'etapa1' ORDER BY dataoperacao DESC

DROP VIEW IF EXISTS Evolucao_put_operacoes;

CREATE VIEW Evolucao_put_operacoes AS
WITH ops AS (
    SELECT 'stock' AS modelo, dataoperacao, hora, ticker, estrategia AS sinal, close_oper,
           NULL AS strike_call, NULL AS valor_call, NULL AS vol_call, NULL AS strike_put, NULL AS valor_put,
           NULL AS vol_put, venc_put,
           alvo_saida, stop_saida, status, SAIDA_data, saida_ativo, NULL AS saida_call, NULL AS saida_put, var_MAX_ativo,
           ROUND((saida_ativo / close_oper - 1) * 100, 2) AS retorno, dias_capital,
           CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END AS capital_unico
    FROM Evolucao_stock
    UNION ALL
    SELECT 'etapa1', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END
    FROM Evolucao_call_put
    UNION ALL
    SELECT 'etapa2', dataoperacao, hora, ticker, estrategia, close_oper,
           strike_call, valor_call, ROUND(vol_call * 100, 1), strike_put, valor_put,
           ROUND(vol_put * 100, 1), venc_put,
           alvo_alta, alvo_baixa, status, SAIDA_data, saida_ativo, saida_call, saida_put, var_MAX_ativo,
           retorno, dias_capital, CASE WHEN selecionada_capital_unico THEN 1 ELSE 0 END
    FROM Evolucao_vol_call_put
),
filtradas AS (
    SELECT * FROM ops
    WHERE dataoperacao >= '20251001'
      AND status IN ('alvo', 'corte', 'vencimento', 'stop', 'alvo_alta', 'alvo_baixa', 'aberta')
)
SELECT
    f.*,
    CASE WHEN f.modelo IN ('etapa1', 'etapa2') AND f.status <> 'aberta'
         THEN ROUND(5000 * f.retorno / 100.0, 2) END AS lucro_operacao
FROM filtradas f;
