-- ============================================================
-- 因子贡献回放：回答"这个结论是被谁拉过去的"
-- 把 code 换成你要查的标的
-- ============================================================

-- 最新一天的因子明细
SELECT factor_id          AS 因子,
       raw_value          AS 原始值,
       normalized_score   AS 归一化分数,
       weight             AS 权重,
       contribution       AS 贡献分,
       CASE WHEN weight = 0 THEN '观察期，只记录不计分' ELSE '' END AS 备注
FROM factor_contributions
WHERE code = 'SH000300'
  AND trade_date = (SELECT MAX(trade_date) FROM factor_contributions WHERE code = 'SH000300')
ORDER BY contribution DESC;

-- 同日各因子贡献占比，以及和趋势分的对应关系
WITH cur AS (
  SELECT * FROM factor_contributions
  WHERE code = 'SH000300'
    AND trade_date = (SELECT MAX(trade_date) FROM factor_contributions WHERE code = 'SH000300')
)
SELECT (SELECT SUM(contribution) / SUM(weight) * 100 FROM cur WHERE weight > 0) AS 推算趋势分,
       (SELECT trend_score FROM features_daily
         WHERE code = 'SH000300' AND trade_date = (SELECT MAX(trade_date) FROM cur)) AS 实际趋势分,
       SUM(CASE WHEN factor_id = 'ma_slope'    THEN contribution ELSE 0 END) AS 均线斜率贡献,
       SUM(CASE WHEN factor_id = 'ma_align'    THEN contribution ELSE 0 END) AS 均线排列贡献,
       SUM(CASE WHEN factor_id = 'vol_confirm' THEN contribution ELSE 0 END) AS 量能配合贡献,
       SUM(CASE WHEN factor_id = 'atr_pct'     THEN contribution ELSE 0 END) AS 波动率贡献,
       SUM(CASE WHEN factor_id = 'donchian'    THEN contribution ELSE 0 END) AS 通道突破贡献
FROM cur;

-- 最近 10 天各因子的贡献走势（看是谁在主导判断）
SELECT trade_date AS 交易日,
       ROUND(SUM(CASE WHEN factor_id = 'ma_slope'    THEN contribution ELSE 0 END), 3) AS 均线斜率,
       ROUND(SUM(CASE WHEN factor_id = 'ma_align'    THEN contribution ELSE 0 END), 3) AS 均线排列,
       ROUND(SUM(CASE WHEN factor_id = 'vol_confirm' THEN contribution ELSE 0 END), 3) AS 量能配合,
       ROUND(SUM(CASE WHEN factor_id = 'atr_pct'     THEN contribution ELSE 0 END), 3) AS 波动率,
       ROUND(SUM(CASE WHEN factor_id = 'donchian'    THEN contribution ELSE 0 END), 3) AS 通道突破
FROM factor_contributions
WHERE code = 'SH000300'
GROUP BY trade_date
ORDER BY trade_date DESC
LIMIT 10;
