import os
import json
import datetime
import requests
import pandas as pd
import akshare as ak

data = {}
headers = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# 1. 抓取外盘、A股基准、期指、汇率与大宗商品
try:
    symbols = (
        "hf_CHA50CFD,fx_susdcnh,DINIW,hf_CL,hf_GC,hf_CAD,"
        "gb_$dji,gb_ixic,gb_inx,gb_nvda,gb_tsla,gb_aapl,gb_hxc,s_sh000001"
    )
    r = requests.get(f"https://hq.sinajs.cn/list={symbols}", headers=headers, timeout=10)
    lines = r.text.strip().split("\n")
    raw_dict = {}
    for line in lines:
        if "=" in line:
            parts = line.split("=")
            k = parts[0].replace("var hq_str_", "").strip()
            v = parts[1].replace('"', '').replace(';', '').split(",")
            raw_dict[k] = v

    # 富时 A50 期货
    a50 = raw_dict.get("hf_CHA50CFD", [])
    if len(a50) > 7 and a50[0]:
        curr = float(a50[0])
        settle = float(a50[7]) if a50[7] else curr
        data["a50_price"] = curr
        data["a50_pct"] = round(((curr - settle) / settle) * 100, 2)
    else:
        data["a50_price"] = 0.0
        data["a50_pct"] = 0.0

    # 离岸人民币 USD/CNH
    cnh = raw_dict.get("fx_susdcnh", [])
    data["usdcnh"] = str(round(float(cnh[1]), 4)) if len(cnh) > 1 and cnh[1] else "7.2500"

    # 美元指数 DXY (代码: DINIW)
    dxy = raw_dict.get("DINIW", [])
    data["dxy_price"] = str(round(float(dxy[1]), 2)) if len(dxy) > 1 and dxy[1] else "104.50"

    # 美股三大指数涨跌幅
    data["dji_pct"] = raw_dict.get("gb_$dji", ["", "", "0.00"])[2] or "0.00"
    data["ixic_pct"] = raw_dict.get("gb_ixic", ["", "", "0.00"])[2] or "0.00"
    data["spx_pct"] = raw_dict.get("gb_inx", ["", "", "0.00"])[2] or "0.00"

    # 核心映射个股及中概
    data["nvda_pct"] = raw_dict.get("gb_nvda", ["", "", "0.00"])[2] or "0.00"
    data["tsla_pct"] = raw_dict.get("gb_tsla", ["", "", "0.00"])[2] or "0.00"
    data["aapl_pct"] = raw_dict.get("gb_aapl", ["", "", "0.00"])[2] or "0.00"
    data["hxc_pct"] = raw_dict.get("gb_hxc", ["", "", "0.00"])[2] or "0.00"

    # 大宗商品
    data["crude_oil"] = raw_dict.get("hf_CL", ["0.0"])[0]
    data["gold"] = raw_dict.get("hf_GC", ["0.0"])[0]
    data["copper"] = raw_dict.get("hf_CAD", ["0.0"])[0]

    # 上证指数现价/最新收盘
    sh_data = raw_dict.get("s_sh000001", [])
    data["sh_close"] = float(sh_data[1]) if len(sh_data) > 1 and sh_data[1] else 0.0

except Exception as e:
    print(f"外盘/行情接口抓取异常: {e}")
    data["macro_error"] = str(e)


# 2. 计算最近一个交易日 (周六周日自动往前推)
target_date = datetime.datetime.now() - datetime.timedelta(days=1)
while target_date.weekday() >= 5:
    target_date -= datetime.timedelta(days=1)
trade_date_str = target_date.strftime("%Y%m%d")
data["trade_date"] = trade_date_str


# 3. 短线生态指标 (涨停、跌停、连板标的、炸板率)
try:
    zt_df = ak.stock_zt_pool_em(date=trade_date_str)
    data["limit_up_count"] = len(zt_df)
    if not zt_df.empty and "连板数" in zt_df.columns:
        data["max_board"] = int(zt_df["连板数"].max())
        top = zt_df.sort_values(by="连板数", ascending=False).iloc[0]
        data["max_board_name"] = f"{top['名称']}({top['代码']})"
    else:
        data["max_board"] = 1
        data["max_board_name"] = "无显著连板标的"

    dt_df = ak.stock_zt_pool_dtgc_em(date=trade_date_str)
    data["limit_down_count"] = len(dt_df) if not dt_df.empty else 0

    zbf_df = ak.stock_zt_pool_zbgc_em(date=trade_date_str)
    broken_count = len(zbf_df) if not zbf_df.empty else 0
    total_pool = len(zt_df) + broken_count
    data["broken_rate"] = round((broken_count / total_pool) * 100, 2) if total_pool > 0 else 0.0

except Exception as e:
    print(f"短线生态拉取异常: {e}")
    data["limit_up_count"], data["limit_down_count"] = 0, 0
    data["max_board"], data["max_board_name"], data["broken_rate"] = 0, "暂无明确龙头", 0.0


# 4. 融资余额
try:
    margin_df = ak.stock_margin_sse()
    if not margin_df.empty:
        last_row = margin_df.iloc[-1]
        val = last_row.get("融资余额") or last_row.get("本日余额") or last_row.get("rzye")
        data["margin_balance"] = round(float(val) / 100000000, 2) if val else 0.0
    else:
        data["margin_balance"] = 0.0
except Exception as e:
    print(f"两融数据异常: {e}")
    data["margin_balance"] = 0.0


# 5. 央行公开市场操作 (OMO)
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
except Exception as e:
    print(f"OMO 数据异常: {e}")
    data["omo_inject"], data["omo_mature"] = "以早间公告为准", "以早间公告为准"


# 保存最终 JSON
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("爬虫运行成功，数据已格式化保存至 data.json")
