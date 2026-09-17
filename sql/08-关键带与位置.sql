-- ============================================================
-- 关键带与当前位置：离支撑/阻力还有多远
-- ============================================================

WITH latest AS (SELECT MAX(trade_date) AS d FROM levels)
SELECT l.code              AS 标的,
       l.level_type        AS 类型,
       ROUND(l.price_low, 3)  AS 下沿,
       ROUND(l.price_high, 3) AS 上沿,
       ROUND(l.weight / 1e8, 1) AS 成交量权重亿,
       ROUND(b.close, 3)      AS 收盘价,
       ROUND((b.close - (l.price_low + l.price_high) / 2) / b.close * 100, 2) AS 距带中心百分比,
       l.engine            AS 生成方式
FROM levels l
JOIN latest ON l.trade_date = latest.d
LEFT JOIN bars_daily b ON b.code = l.code AND b.trade_date = l.trade_date
ORDER BY l.code, l.price_low;

-- 只看向上空间：距离最近阻力带 3% 以内的标的（接近压力位，谨慎追）
WITH latest AS (SELECT MAX(trade_date) AS d FROM levels),
     pos AS (
       SELECT l.code,
              b.close,
              MIN((l.price_low + l.price_high) / 2) AS nearest_resistance
       FROM levels l
       JOIN latest ON l.trade_date = latest.d
       JOIN bars_daily b ON b.code = l.code AND b.trade_date = l.trade_date
       WHERE l.level_type = 'resistance' AND (l.price_low + l.price_high) / 2 > b.close
       GROUP BY l.code, b.close
     )
SELECT code AS 标的,
       ROUND(close, 3) AS 收盘价,
       ROUND(nearest_resistance, 3) AS 最近阻力带中心,
       ROUND((nearest_resistance - close) / close * 100, 2) AS 距阻力百分比
FROM pos
ORDER BY 距阻力百分比;
