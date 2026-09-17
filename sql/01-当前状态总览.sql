-- ============================================================
-- 当前状态总览：最新交易日每个标的状态、趋势分、位置
-- 这是每天开盘前第一眼要看的表
-- ============================================================

WITH latest AS (SELECT MAX(trade_date) AS d FROM features_daily)
SELECT f.code                       AS 标的,
       f.state                      AS 状态,
       ROUND(f.trend_score, 1)      AS 趋势分,
       f.state_since                AS 状态起始日,
       f.state_days                 AS 持续天数,
       ROUND(f.opportunity_score, 2) AS 机会分,
       ROUND(f.vol_ratio_20, 2)     AS 量比,
       f.consolidation_days         AS 整理天数,
       f.breakout_confirmed         AS 放量突破,
       ROUND(b.close, 3)            AS 收盘价,
       f.data_quality_flag          AS 数据质量
FROM features_daily f
JOIN latest ON f.trade_date = latest.d
LEFT JOIN bars_daily b ON b.code = f.code AND b.trade_date = f.trade_date
ORDER BY f.trend_score DESC;

-- 同日市场广度（量能与情绪的地基）
SELECT trade_date                           AS 交易日,
       up_count                             AS 上涨家数,
       down_count                           AS 下跌家数,
       limit_up_count                       AS 涨停家数,
       ROUND(up_ratio * 100, 1)             AS 上涨占比百分比,
       ROUND(median_pct_chg, 2)             AS 涨跌幅中位数,
       ROUND(total_amount / 1e8, 0)         AS 全市场成交额亿
FROM market_breadth
ORDER BY trade_date DESC
LIMIT 5;
