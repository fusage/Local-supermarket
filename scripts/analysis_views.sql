-- 1. 店舗別 3大ロスおよび粗利インパクト集計ビュー
DROP VIEW IF EXISTS V_STORE_LOSS_SUMMARY;
CREATE VIEW V_STORE_LOSS_SUMMARY AS
SELECT 
    s.store_cd,
    s.store_name,
    s.trade_area_type,
    SUM(sl.amount) AS total_sales_amount,
    SUM(sl.qty * p.cost_price) AS total_cost_amount,
    SUM(sl.amount) - SUM(sl.qty * p.cost_price) AS estimated_gross_profit,
    COALESCE(SUM(wd.waste_qty * p.cost_price), 0) AS total_waste_loss,
    COALESCE(SUM(wd.discount_amount), 0) AS total_discount_loss,
    COALESCE(SUM(inv.lost_sales_est_amount), 0) AS total_opportunity_loss,
    (COALESCE(SUM(wd.waste_qty * p.cost_price), 0) 
     + COALESCE(SUM(wd.discount_amount), 0) 
     + COALESCE(SUM(inv.lost_sales_est_amount), 0)) AS total_loss_amount
FROM M_STORE s
LEFT JOIN T_SALES sl ON s.store_cd = sl.store_cd
LEFT JOIN M_PRODUCT p ON sl.product_cd = p.product_cd
LEFT JOIN T_INVENTORY inv ON sl.sales_date = inv.date AND sl.store_cd = inv.store_cd AND sl.product_cd = inv.product_cd
LEFT JOIN T_WASTE_DISCOUNT wd ON sl.sales_date = wd.date AND sl.store_cd = wd.store_cd AND sl.product_cd = wd.product_cd
GROUP BY s.store_cd, s.store_name, s.trade_area_type;


-- 2. 気温急変日の仮説検証ビュー
DROP VIEW IF EXISTS V_WEATHER_HYPOTHESIS_CHECK;
CREATE VIEW V_WEATHER_HYPOTHESIS_CHECK AS
SELECT 
    o.delivery_date AS date,
    w.weather,
    w.temp_diff_prev_day,
    s.store_name,
    p.product_name,
    p.category,
    o.ai_recommended_qty,
    o.final_order_qty,
    (o.final_order_qty - o.ai_recommended_qty) AS order_gap,
    o.manager_memo,
    sl.qty AS actual_sales_qty,
    inv.out_of_stock_hours,
    inv.lost_sales_est_amount AS opportunity_loss
FROM T_ORDER o
JOIN M_STORE s ON o.store_cd = s.store_cd
JOIN M_PRODUCT p ON o.product_cd = p.product_cd
LEFT JOIN M_WEATHER w ON o.delivery_date = w.date
LEFT JOIN T_SALES sl ON o.delivery_date = sl.sales_date AND o.store_cd = sl.store_cd AND o.product_cd = sl.product_cd
LEFT JOIN T_INVENTORY inv ON o.delivery_date = inv.date AND o.store_cd = inv.store_cd AND o.product_cd = inv.product_cd
WHERE w.temp_diff_prev_day <= -2.5
ORDER BY o.delivery_date DESC, inv.lost_sales_est_amount DESC;


-- 3. 夕方17時の見切り推奨候補抽出ビュー
DROP VIEW IF EXISTS V_EVENING_DISCOUNT_CANDIDATES;
CREATE VIEW V_EVENING_DISCOUNT_CANDIDATES AS
SELECT 
    sl.sales_date AS date,
    s.store_name,
    p.product_cd,
    p.product_name,
    p.category,
    p.sales_price,
    sl.qty AS sales_qty_so_far,
    o.final_order_qty AS initial_order_qty,
    (o.final_order_qty - sl.qty) AS remaining_stock_estimate,
    CASE 
        WHEN (o.final_order_qty - sl.qty) >= 10 THEN '30%〜50%引き推奨（過剰在庫）'
        WHEN (o.final_order_qty - sl.qty) >= 5 THEN '20%引き推奨'
        ELSE '定価売り切り可能'
    END AS discount_recommendation
FROM T_SALES sl
JOIN M_STORE s ON sl.store_cd = s.store_cd
JOIN M_PRODUCT p ON sl.product_cd = p.product_cd
JOIN T_ORDER o ON sl.sales_date = o.delivery_date AND sl.store_cd = o.store_cd AND sl.product_cd = o.product_cd
WHERE p.shelf_life_days <= 2
ORDER BY remaining_stock_estimate DESC;