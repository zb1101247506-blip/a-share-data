import os
import json
import datetime
from datetime import timezone, timedelta
import requests
import pandas as pd
import akshare as ak

data = {}
headers = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ----------------------------------------------------------------------
# 1. 绝对精确的时间与交易日判定 (锁定北京时间 UTC+8)
# ----------------------------------------------------------------------
beijing_tz = timezone(timedelta(hours=8))
now_bj = datetime.datetime.now(beijing_tz)

try:
    calendar_df = ak.tool_trade_date_hist_sinajs()
    valid_dates = [
        d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "") 
        for d in calendar_df["trade_date"]
    ]
    today_str = now_bj.strftime("%Y%m%d")
    
    # 判定今天是否属于开盘交易日
    is_today_trade_day = today_str in valid_dates

    # 08:55 处于盘前，短线生态必须取今天之前的最后一个真实交易日
    past_dates = [d for d in valid_dates if d < today_str]
    trade_date_str = past_dates[-1]
except Exception as e:
    # 备用倒推防崩机制
    is_today_trade_day = now_bj.weekday() < 5
    target = now_bj - timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    trade_date_str = target.strftime("%Y%m%d")

data["trade_date"] = trade_date_str
data["is_today_trade_day"] = is_today_trade_day
data["crawl_time_bj"] = now_bj.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# 2. 外部宏观、指数基准、汇率、大宗与外盘科技锚 (轻量新浪接口，海外极速)
# ----------------------------------------------------------------------
try:
    symbols = (
        "hf_CHA50CFD,fx_susdcnh,DINIW,hf_CL,hf_GC,hf_HG,"
        "gb_$dji,gb_ixic,gb_inx,gb_nvda,gb_tsla,gb_aapl,gb_hxc,"
        "s_sh000001,s_sz399001"
    )
    r = requests.get(f"https://hq.sinajs.cn/list={symbols}", headers=headers, timeout=12)
    lines = [line for line in r.text.strip().split("\n") if "=" in line]
    raw_dict = {}
    for line in lines:
        parts = line.split("=")
        k = parts[0].replace("var hq_str_", "").strip()
        v = parts[1].replace('"', '').replace(';', '').split(",")
        raw_dict[k] = v

    # 富时 A50 期货 (点位与涨跌幅)
    a50 = raw_dict.get("hf_CHA50CFD", [])
    if len(a50) > 7 and a50[0]:
        curr = float(a50[0])
        settle = float(a50[7]) if a50[7] else curr
        data["a50_price"] = curr
        data["a50_pct"] = round(((curr - settle) / settle) * 100, 2)
    else:
        data["a50_price"], data["a50_pct"] = 0.0, 0.0

    # 离岸人民币 USD/CNH
    cnh = raw_dict.get("fx_susdcnh", [])
    data["usdcnh"] = str(round(float(cnh[1]), 4)) if len(cnh) > 1 and cnh[1] else "7.2500"

    # 美元指数 DXY (新浪代码: DINIW)
    dxy = raw_dict.get("DINIW", [])
    data["dxy_price"] = str(round(float(dxy[1]), 2)) if len(dxy) > 1 and dxy[1] else "104.50"

    # 美股三大指数
    data["dji_pct"] = raw_dict.get("gb_$dji", ["", "", "0.00"])[2] or "0.00"
    data["ixic_pct"] = raw_dict.get("gb_ixic", ["", "", "0.00"])[2] or "0.00"
    data["spx_pct"] = raw_dict.get("gb_inx", ["", "", "0.00"])[2] or "0.00"

    # 科技映射标的与中概
    data["nvda_pct"] = raw_dict.get("gb_nvda", ["", "", "0.00"])[2] or "0.00"
    data["tsla_pct"] = raw_dict.get("gb_tsla", ["", "", "0.00"])[2] or "0.00"
    data["aapl_pct"] = raw_dict.get("gb_aapl", ["", "", "0.00"])[2] or "0.00"
    data["hxc_pct"] = raw_dict.get("gb_hxc", ["", "", "0.00"])[2] or "0.00"

    # 核心大宗商品 (原油、COMEX黄金、COMEX铜)
    data["crude_oil"] = raw_dict.get("hf_CL", ["0.0"])[0]
    data["gold"] = raw_dict.get("hf_GC", ["0.0"])[0]
    data["copper"] = raw_dict.get("hf_HG", ["0.0"])[0]

    # 上证指数点位与两市成交量
    sh = raw_dict.get("s_sh000001", ["", "0", "0", "0", "0", "0"])
    sz = raw_dict.get("s_sz399001", ["", "0", "0", "0", "0", "0"])
    data["sh_close"] = float(sh[1]) if len(sh) > 1 and sh[1] else 0.0
    data["sh_pct"] = float(sh[3]) if len(sh) > 3 and sh[3] else 0.0
    
    sh_amount = float(sh[5]) / 10000 if len(sh) > 5 and sh[5] else 0.0
    sz_amount = float(sz[5]) / 10000 if len(sz) > 5 and sz[5] else 0.0
    data["total_volume"] = round(sh_amount + sz_amount, 2)

except Exception as e:
    data["macro_error"] = str(e)


# ----------------------------------------------------------------------
# 3. 短线生态指标 (涨停、跌停、最高板标的及题材、炸板率)
# ----------------------------------------------------------------------
try:
    zt_df = ak.stock_zt_pool_em(date=trade_date_str)
    data["limit_up_count"] = len(zt_df)
    
    if not zt_df.empty and "连板数" in zt_df.columns:
        data["max_board"] = int(zt_df["连板数"].max())
        top = zt_df.sort_values(by="连板数", ascending=False).iloc[0]
        name = top.get("名称", "未知")
        code = top.get("代码", "000000")
        industry = top.get("所属行业", top.get("行业", "无细分题材"))
        data["max_board_name"] = f"{name}({code}) [{industry}]"
    else:
        data["max_board"] = 1
        data["max_board_name"] = "无高标连板"

    dt_df = ak.stock_zt_pool_dtgc_em(date=trade_date_str)
    data["limit_down_count"] = len(dt_df) if not dt_df.empty else 0

    zbf_df = ak.stock_zt_pool_zbgc_em(date=trade_date_str)
    broken_count = len(zbf_df) if not zbf_df.empty else 0
    total_pool = len(zt_df) + broken_count
    data["broken_rate"] = round((broken_count / total_pool) * 100, 2) if total_pool > 0 else 0.0

except Exception as e:
    data["limit_up_count"], data["limit_down_count"] = 0, 0
    data["max_board"], data["max_board_name"], data["broken_rate"] = 0, "暂无高标", 0.0


# ----------------------------------------------------------------------
# 4. 全市场两融余额与央行公开市场操作 (OMO)
# ----------------------------------------------------------------------
try:
    margin_all = ak.stock_margin_account_info()
    if not margin_all.empty and "融资融券余额" in margin_all.columns:
        last_val = margin_all.iloc[-1]["融资融券余额"]
        data["margin_balance"] = round(float(last_val) / 100000000, 2)
    else:
        margin_df = ak.stock_margin_sse()
        last_row = margin_df.iloc[-1]
        val = last_row.get("融资余额") or last_row.get("本日余额")
        data["margin_balance"] = round(float(val) / 100000000, 2) if val else 0.0
except Exception:
    data["margin_balance"] = 0.0

try:
    omo_df = ak.macro_china_open_market_daily()
    if not omo_df.empty:
        latest_row = omo_df.iloc[-1].to_dict()
        inject = latest_row.get("逆回购操作", latest_row.get("投放量", "0"))
        mature = latest_row.get("逆回购到期", latest_row.get("到期量", "0"))
        data["omo_inject"] = f"{inject}亿元" if str(inject).isdigit() else str(inject)
        data["omo_mature"] = f"{mature}亿元" if str(mature).isdigit() else str(mature)
    else:
        data["omo_inject"], data["omo_mature"] = "以早间公告为准", "以早间公告为准"
except Exception:
    data["omo_inject"], data["omo_mature"] = "以早间公告为准", "以早间公告为准"


# ----------------------------------------------------------------------
# 5. 保存结果至 data.json
# ----------------------------------------------------------------------
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"[{data['crawl_time_bj']}] 数据更新成功！基准交易日: {data['trade_date']}, 今日是否交易日: {data['is_today_trade_day']}")
