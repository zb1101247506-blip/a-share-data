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

# 1. 抓取外盘、A股基准、期指、汇率与大宗商品 (全部合并至轻量级新浪接口)
try:
    # 注意：美元指数为 DINIW，增加 s_sh000001(上证综合指数快照)
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

    # A50 期货点位与涨跌幅
    a50 = raw_dict.get("hf_CHA50CFD", [])
    if len(a50) > 7 and a50[0]:
        curr = float(a50[0])
        settle = float(a50[7]) if a50[7] else curr
        data["a50_price"] = curr
        data["a50_pct"] = round(((curr - settle) / settle) * 100, 2)
    else:
        data["a50_price"], data["a50_pct"] = "暂无实时数据", 0.0

    # 离岸人民币 USD/CNH
    cnh = raw_dict.get("fx_susdcnh", [])
    data["usdcnh"] = cnh[1] if len(cnh) > 1 else "暂无实时数据"

    # 美元指数 (代码: DINIW)
    dxy = raw_dict.get("DINIW", [])
    data["dxy_price"] = dxy[1] if len(dxy) > 1 else "暂无实时数据"

    # 美股三大指数涨跌幅
    data["dji_pct"] = raw_dict.get("gb_$dji", ["","","0.0"])[2]
    data["ixic_pct"] = raw_dict.get("gb_ixic", ["","","0.0"])[2]
    data["spx_pct"] = raw_dict.get("gb_inx", ["","","0.0"])[2]

    # 美股科技与金龙指数
    data["nvda_pct"] = raw_dict.get("gb_nvda", ["","","0.0"])[2]
    data["tsla_pct"] = raw_dict.get("gb_tsla", ["","","0.0"])[2]
    data["aapl_pct"] = raw_dict.get("gb_aapl", ["","","0.0"])[2]
    data["hxc_pct"] = raw_dict.get("gb_hxc", ["","","0.0"])[2]

    # 大宗商品
    data["crude_oil"] = raw_dict.get("hf_CL", ["暂无"])[0]
    data["gold"] = raw_dict.get("hf_GC", ["暂无"])[0]
    data["copper"] = raw_dict.get("hf_CAD", ["暂无"])[0]

    # 上证指数收盘价/实时现价 (s_sh000001: 索引1是当前点位)
    sh_data = raw_dict.get("s_sh000001", [])
    data["sh_close"] = float(sh_data[1]) if len(sh_data) > 1 else 0.0

except Exception as e:
    print(f"外盘/行情接口抓取异常: {e}")
    data["macro_error"] = str(e)


# 2. 计算最近交易日
target_date = datetime.datetime.now() - datetime.timedelta(days=1)
while target_date.weekday() >= 5:
    target_date -= datetime.timedelta(days=1)
trade_date_str = target_date.strftime("%Y%m%d")
data["trade_date"] = trade_date_str


# 3. 短线连板生态 (AkShare 东方财富涨跌停池)
try:
    zt_df = ak.stock_zt_pool_em(date=trade_date_str)
    data["limit_up_count"] = len(zt_df)
    if not zt_df.empty:
        data["max_board"] = int(zt_df["连板数"].max())
        top = zt_df.sort_values(by="连板数", ascending=False).iloc[0]
        data["max_board_name"] = f"{top['名称']}({top['代码']})"
    else:
        data["max_board"], data["max_board_name"] = 0, "无"

    dt_df = ak.stock_zt_pool_dtgc_em(date=trade_date_str)
    data["limit_down_count"] = len(dt_df) if not dt_df.empty else 0

    zbf_df = ak.stock_zt_pool_zbgc_em(date=trade_date_str)
    broken_count = len(zbf_df) if not zbf_df.empty else 0
    total_pool = len(zt_df) + broken_count
    data["broken_rate"] = round((broken_count / total_pool) * 100, 2) if total_pool > 0 else 0.0
except Exception as e:
    print(f"涨跌停池数据拉取异常: {e}")
    data["limit_up_count"], data["limit_down_count"] = 0, 0
    data["max_board"], data["max_board_name"], data["broken_rate"] = 0, "暂无", 0.0


# 4. 上交所两融余额
try:
    margin_df = ak.stock_margin_sse()
    if not margin_df.empty:
        # 正确匹配中文字段：融资余额 或 融资融券余额
        last_row = margin_df.iloc[-1]
        val = last_row.get("融资余额") or last_row.get("本日余额") or last_row.get("rzye")
        if val is not None:
            data["margin_balance"] = round(float(val) / 100000000, 2)
        else:
            data["margin_balance"] = "暂无数据"
    else:
        data["margin_balance"] = "暂无数据"
except Exception as e:
    print(f"两融数据拉取异常: {e}")
    data["margin_balance"] = "暂无数据"


# 5. 央行公开市场操作 (OMO)
try:
    # 使用正确的公开市场每日操作接口
    omo_df = ak.macro_china_open_market_daily()
    if not omo_df.empty:
        latest_row = omo_df.iloc[-1].to_dict()
        # 兼容匹配常见表头字段
        data["omo_mature"] = str(latest_row.get("逆回购到期", latest_row.get("到期量", "见央行公告")))
        data["omo_inject"] = str(latest_row.get("逆回购操作", latest_row.get("投放量", "见央行公告")))
    else:
        data["omo_mature"], data["omo_inject"] = "见央行公告", "见央行公告"
except Exception as e:
    print(f"OMO 数据拉取异常: {e}")
    data["omo_mature"], data["omo_inject"] = "见央行公告", "见央行公告"


# 保存为 JSON
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("抓取成功完成，数据已更新至 data.json")
