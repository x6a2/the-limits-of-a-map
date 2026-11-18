#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_data.py — 《一张地图的极限》数据管线

读 D:\\Ti\\learn\\design\\data\\online_retail_ii.xlsx（只读），完成：
  1. 两个 sheet 合并、按 (Invoice, StockCode, Quantity, InvoiceDate, Price) 去重（保留 2009-2010 版本）
  2. 国家名标准化、取消单/退货/匿名/零价行标记
  3. 输出 data/countries.json data/monthly.json data/skus.json data/concentration.json
  4. 输出 data/facts.md（正文所有数字的唯一来源）
  5. 输出 data/data.js（file:// 双击打开时 fetch 不可用，用 script 注入同一份数据）

口径（与 demo-blueprint.md §3 一致）：
  revenue    = sum(Quantity * Price) where 非取消单、非退货、Price > 0   （有效销售）
  orders     = 同一口径下唯一 Invoice 数
  customers  = 同一口径下唯一非空 Customer ID 数
  return_rate= 取消单金额绝对值 / revenue
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
SRC = Path(r"D:\Ti\learn\design\data\online_retail_ii.xlsx")
OUT = ROOT / "data"

SHEETS = ["Year 2009-2010", "Year 2010-2011"]
USE_COLS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
KEY = ["Invoice", "StockCode", "Quantity", "InvoiceDate", "Price"]

# 原始取值 -> 标准名。EIRE 等别名合并；Channel Islands 单列，不计入任何国家。
COUNTRY_FIX = {
    "EIRE": "Ireland",
    "RSA": "South Africa",
    "Korea": "South Korea",
    "USA": "United States",
}
NON_COUNTRY = {"Unspecified", "European Community", "West Indies"}
QUASI = {"Channel Islands"}

META = {
    # name: (iso3, 中文名, 是否国家, 是否画在地图上)
    "United Kingdom": ("GBR", "英国", True, True),
    "Ireland": ("IRL", "爱尔兰", True, True),
    "Netherlands": ("NLD", "荷兰", True, True),
    "Germany": ("DEU", "德国", True, True),
    "France": ("FRA", "法国", True, True),
    "Australia": ("AUS", "澳大利亚", True, False),
    "Spain": ("ESP", "西班牙", True, True),
    "Switzerland": ("CHE", "瑞士", True, True),
    "Sweden": ("SWE", "瑞典", True, True),
    "Denmark": ("DNK", "丹麦", True, True),
    "Belgium": ("BEL", "比利时", True, True),
    "Norway": ("NOR", "挪威", True, True),
    "Portugal": ("PRT", "葡萄牙", True, True),
    "Channel Islands": (None, "海峡群岛", False, False),
    "Japan": ("JPN", "日本", True, False),
    "Italy": ("ITA", "意大利", True, True),
    "Finland": ("FIN", "芬兰", True, True),
    "Singapore": ("SGP", "新加坡", True, False),
    "Cyprus": ("CYP", "塞浦路斯", True, True),
    "Hong Kong": ("HKG", "中国香港", True, False),
    "Austria": ("AUT", "奥地利", True, True),
    "Greece": ("GRC", "希腊", True, True),
    "Israel": ("ISR", "以色列", True, False),
    "United Arab Emirates": ("ARE", "阿联酋", True, False),
    "Unspecified": (None, "未注明", False, False),
    "Poland": ("POL", "波兰", True, True),
    "United States": ("USA", "美国", True, False),
    "Malta": ("MLT", "马耳他", True, True),
    "Iceland": ("ISL", "冰岛", True, True),
    "Lithuania": ("LTU", "立陶宛", True, True),
    "Canada": ("CAN", "加拿大", True, False),
    "South Africa": ("ZAF", "南非", True, False),
    "Bahrain": ("BHR", "巴林", True, False),
    "Thailand": ("THA", "泰国", True, False),
    "Lebanon": ("LBN", "黎巴嫩", True, False),
    "Brazil": ("BRA", "巴西", True, False),
    "European Community": (None, "欧共体（未归类）", False, False),
    "Bermuda": ("BMU", "百慕大", True, False),
    "South Korea": ("KOR", "韩国", True, False),
    "Czech Republic": ("CZE", "捷克", True, True),
    "West Indies": (None, "西印度群岛", False, False),
    "Saudi Arabia": ("SAU", "沙特阿拉伯", True, False),
    "Nigeria": ("NGA", "尼日利亚", True, False),
}

EUROPE_22 = {v[0] for k, v in META.items() if v[3]}

# 2011-12 只有 9 天，作为不完整月不进入月度序列；月度序列 = 2009-12 .. 2011-11，共 24 个月。
MONTH_END = "2011-11"


def money(x: float) -> str:
    return f"£{x:,.0f}"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def load() -> pd.DataFrame:
    frames = []
    for s in SHEETS:
        df = pd.read_excel(SRC, sheet_name=s, usecols=USE_COLS, engine="openpyxl")
        df["_sheet"] = s
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Invoice"] = df["Invoice"].astype(str)
    df["is_cancel"] = df["Invoice"].str.startswith("C")
    df["is_return"] = df["Quantity"] < 0
    df["is_anonymous"] = df["Customer ID"].isna()
    df["amount"] = df["Quantity"] * df["Price"]
    df["country"] = df["Country"].map(lambda c: COUNTRY_FIX.get(c, c))
    df["month"] = df["InvoiceDate"].dt.strftime("%Y-%m")
    df["valid_sale"] = (~df["is_cancel"]) & (~df["is_return"]) & (df["Price"] > 0)
    return df


def customer_table(v: pd.DataFrame) -> pd.DataFrame:
    t = (
        v.dropna(subset=["Customer ID"])
        .groupby("Customer ID")
        .agg(revenue=("amount", "sum"), orders=("Invoice", "nunique"), country=("country", "first"))
        .reset_index()
    )
    t["Customer ID"] = t["Customer ID"].astype(int)
    return t.sort_values("revenue", ascending=False).reset_index(drop=True)


def lorenz_curve(values: np.ndarray, points: int = 101):
    x = np.sort(np.asarray(values, dtype=float))
    cum = np.cumsum(x)
    if cum[-1] <= 0:
        return [[i / (points - 1), i / (points - 1)] for i in range(points)]
    idx = np.linspace(0, len(x) - 1, points).round().astype(int)
    return [[float((i + 1) / len(x)), float(cum[i] / cum[-1])] for i in idx]


def gini(values: np.ndarray) -> float:
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n == 0 or x.sum() <= 0:
        return 0.0
    cum = np.cumsum(x)
    return float((n + 1 - 2 * (cum / cum[-1]).sum()) / n)


def build():
    raw = load()
    n_raw = len(raw)
    dup_rows = int(raw.duplicated(subset=KEY, keep="first").sum())
    df = raw.drop_duplicates(subset=KEY, keep="first").reset_index(drop=True)
    df = prepare(df)
    n = len(df)

    v = df[df.valid_sale]
    revenue_total = float(v["amount"].sum())

    # ---------- 国家 ----------
    countries = []
    for name, g in v.groupby("country"):
        iso, zh, is_country, on_map = META.get(name, (None, name, True, False))
        rev = float(g["amount"].sum())
        orders = int(g["Invoice"].nunique())
        orders_all = int(df.loc[df.country.eq(name), "Invoice"].nunique())
        custs = int(g["Customer ID"].nunique())
        cancel_value = float(-df.loc[df.country.eq(name) & df.is_cancel, "amount"].sum())
        months = (
            g.assign(m=g["InvoiceDate"].dt.strftime("%Y-%m"))
            .groupby("m")["amount"]
            .sum()
        )
        monthly = [round(float(months.get(m, 0.0)), 2) for m in MONTHS]
        # 每个大客户自己的 24 个月序列：客户层的生意在时间上是什么形状
        cmap = (
            g.assign(m=g["InvoiceDate"].dt.strftime("%Y-%m"))
            .groupby(["Customer ID", "m"])["amount"]
            .sum()
            .to_dict()
        )
        top_customers = []
        for _, r in customer_table(g).head(5).iterrows():
            cid = int(r["Customer ID"])
            cser = [round(float(cmap.get((cid, m), 0.0)), 2) for m in MONTHS]
            act = [i for i, x in enumerate(cser) if x > 0]
            top_customers.append(
                {
                    "id": cid,
                    "revenue": round(float(r["revenue"]), 2),
                    "orders": int(r["orders"]),
                    "share": round(float(r["revenue"]) / rev, 4) if rev else 0.0,
                    "monthly": cser,
                    "active_months": len(act),
                    "first": MONTHS[act[0]] if act else None,
                    "last": MONTHS[act[-1]] if act else None,
                }
            )
        countries.append(
            {
                "name": name,
                "iso": iso,
                "name_zh": zh,
                "is_country": is_country,
                "on_map": on_map,
                "revenue": round(rev, 2),
                "orders": orders,
                "invoices_all": orders_all,
                "customers": custs,
                "share": round(rev / revenue_total, 6) if revenue_total else 0.0,
                "revenue_per_customer": round(rev / custs, 2) if custs else 0.0,
                "return_rate": round(cancel_value / rev, 4) if rev else 0.0,
                "monthly": monthly,
                "top_customers": top_customers,
            }
        )
    countries.sort(key=lambda d: d["revenue"], reverse=True)
    for i, c in enumerate(countries, 1):
        c["rank"] = i

    real = [c for c in countries if c["is_country"]]
    top12 = countries[:12]
    tail = countries[12:]

    # ---------- 月度 ----------
    months = MONTHS
    mv = v[v["InvoiceDate"].dt.strftime("%Y-%m").isin(months)]
    mrev = mv.groupby(mv["InvoiceDate"].dt.strftime("%Y-%m"))["amount"].sum()
    mord = mv.groupby(mv["InvoiceDate"].dt.strftime("%Y-%m"))["Invoice"].nunique()
    mcancel = (
        df[df.is_cancel & df["InvoiceDate"].dt.strftime("%Y-%m").isin(months)]
        .groupby(df["InvoiceDate"].dt.strftime("%Y-%m"))["amount"]
        .sum()
        .abs()
    )
    mcust = mv.dropna(subset=["Customer ID"]).groupby(mv["InvoiceDate"].dt.strftime("%Y-%m"))["Customer ID"].nunique()

    revenue_series = [round(float(mrev.get(m, 0.0)), 2) for m in months]
    orders_series = [int(mord.get(m, 0)) for m in months]
    returns_series = [round(float(mcancel.get(m, 0.0)), 2) for m in months]
    return_rate_series = [
        round(returns_series[i] / revenue_series[i], 4) if revenue_series[i] else 0.0
        for i in range(len(months))
    ]
    customers_series = [int(mcust.get(m, 0)) for m in months]

    peak_i = int(np.argmax(revenue_series))
    trough_i = int(np.argmin(revenue_series))
    peak, trough = revenue_series[peak_i], revenue_series[trough_i]
    amplitude = peak / trough if trough else 0.0
    mean_month = float(np.mean(revenue_series))

    monthly_out = {
        "months": months,
        "revenue": revenue_series,
        "orders": orders_series,
        "returns": returns_series,
        "return_rate": return_rate_series,
        "customers": customers_series,
        "peak": {"month": months[peak_i], "revenue": peak},
        "trough": {"month": months[trough_i], "revenue": trough},
        "amplitude": round(amplitude, 3),
        "mean": round(mean_month, 2),
        "note": "2011-12 仅 9 天，作为不完整月不进入序列；上表为 24 个完整自然月。",
    }

    # ---------- 逐日（首屏的数据纹理） ----------
    day_range = pd.date_range(
        v["InvoiceDate"].min().normalize(), v["InvoiceDate"].max().normalize(), freq="D"
    )
    dsum = (
        v.assign(d=v["InvoiceDate"].dt.normalize())
        .groupby("d")["amount"].sum()
    )
    dvals = [round(float(dsum.get(d, 0.0)), 2) for d in day_range]
    daily_out = {
        "start": day_range[0].strftime("%Y-%m-%d"),
        "end": day_range[-1].strftime("%Y-%m-%d"),
        "days": len(dvals),
        "zero_days": int(sum(1 for x in dvals if x <= 0)),
        "max": max(dvals) if dvals else 0.0,
        "p98": round(float(np.quantile(dvals, 0.98)), 2),
        "values": dvals,
    }

    # ---------- SKU ----------
    sku = (
        v.groupby("StockCode")
        .agg(revenue=("amount", "sum"), units=("Quantity", "sum"), desc=("Description", "first"))
        .reset_index()
        .sort_values("revenue", ascending=False)
        .head(30)
    )
    skus_out = [
        {
            "code": str(r["StockCode"]),
            "desc": (str(r["desc"]).strip() if pd.notna(r["desc"]) else ""),
            "revenue": round(float(r["revenue"]), 2),
            "units": int(r["units"]),
        }
        for _, r in sku.iterrows()
    ]

    # ---------- 集中度 ----------
    cust = customer_table(v)
    cr = cust["revenue"].to_numpy()
    n_cust = len(cust)
    top1_n = max(1, int(round(n_cust * 0.01)))
    top10_n = max(1, int(round(n_cust * 0.10)))
    top1_share = float(cr[:top1_n].sum() / cr.sum())
    top10_share = float(cr[:top10_n].sum() / cr.sum())
    anonymous_rev = float(df.loc[df.is_anonymous & df.valid_sale, "amount"].sum())
    concentration = {
        "lorenz": [[round(a, 4), round(b, 5)] for a, b in lorenz_curve(cr)],
        "gini": round(gini(cr), 4),
        "top10_share": round(top10_share * 100, 2),
        "top1_share": round(top1_share * 100, 2),
        "top10_customers": int(top10_n),
        "top1_customers": int(top1_n),
        "customers": int(n_cust),
        "anonymous_revenue_share": round(anonymous_rev / revenue_total * 100, 2),
        "note": "洛伦兹曲线与集中度只含可识别客户（有 Customer ID）。",
    }

    # ---------- facts ----------
    uk = countries[0]
    ie = next(c for c in countries if c["name"] == "Ireland")
    europe_rev = sum(c["revenue"] for c in countries if c["iso"] in EUROPE_22)
    tail_share = sum(c["share"] for c in tail)
    top12_share = sum(c["share"] for c in top12)
    non_country_rev = sum(c["revenue"] for c in countries if not c["is_country"])
    cancel_rows = int(df.is_cancel.sum())
    return_rows = int(df.is_return.sum())
    cancel_value = float(-df.loc[df.is_cancel, "amount"].sum())
    anon_rows = int(df.is_anonymous.sum())
    bad_debt = float(-df.loc[df["Price"] < 0, "amount"].sum())
    net_all = float(df.loc[~df.is_cancel, "amount"].sum())

    facts = {
        "rows_raw": n_raw,
        "rows": n,
        "rows_removed": dup_rows,
        "sheets": SHEETS,
        "date_min": df["InvoiceDate"].min().strftime("%Y-%m-%d %H:%M"),
        "date_max": df["InvoiceDate"].max().strftime("%Y-%m-%d %H:%M"),
        "days": int((df["InvoiceDate"].max() - df["InvoiceDate"].min()).days),
        "country_values": int(df["country"].nunique()),
        "real_countries": len(real),
        "non_country_values": len(NON_COUNTRY | QUASI),
        "skus": int(df["StockCode"].nunique()),
        "customers": int(n_cust),
        "revenue": round(revenue_total, 2),
        "net_incl_returns": round(net_all, 2),
        "uk_revenue": uk["revenue"],
        "uk_share": uk["share"],
        "europe_revenue": round(europe_rev, 2),
        "europe_share": round(europe_rev / revenue_total, 6),
        "top12_share": round(top12_share, 6),
        "tail_share": round(tail_share, 6),
        "tail_count": len(tail),
        "tail_revenue": round(sum(c["revenue"] for c in tail), 2),
        "cancel_rows": cancel_rows,
        "cancel_row_share": round(cancel_rows / n, 6),
        "cancel_value": round(cancel_value, 2),
        "cancel_value_share": round(cancel_value / revenue_total, 6),
        "return_rows": return_rows,
        "anon_rows": anon_rows,
        "anon_row_share": round(anon_rows / n, 6),
        "anon_revenue_share": round(anonymous_rev / revenue_total, 6),
        "bad_debt_value": round(bad_debt, 2),
        "ie_revenue": ie["revenue"],
        "ie_orders": ie["orders"],
        "ie_customers": ie["customers"],
        "ie_share": ie["share"],
        "ie_per_customer": ie["revenue_per_customer"],
        "ie_top_customers": ie["top_customers"],
        "uk_orders": uk["orders"],
        "ie_invoices_all": ie["invoices_all"],
        "uk_customers": uk["customers"],
        "peak_month": months[peak_i],
        "peak_value": peak,
        "trough_month": months[trough_i],
        "trough_value": trough,
        "amplitude": round(amplitude, 3),
        "mean_month": round(mean_month, 2),
        "months": len(months),
        "top10_share": concentration["top10_share"],
        "top1_share": concentration["top1_share"],
        "gini": concentration["gini"],
        "non_country_revenue": round(non_country_rev, 2),
        "non_country_share": round(non_country_rev / revenue_total, 6),
        "outside_eu_max_share": max(c["share"] for c in countries if c["iso"] not in EUROPE_22 and c["is_country"]),
        "top12_countries": [c["name"] for c in top12],
        "europe_countries": len([c for c in countries if c["iso"] in EUROPE_22]),
    }

    OUT.mkdir(exist_ok=True)
    write_json(OUT / "countries.json", countries)
    write_json(OUT / "monthly.json", monthly_out)
    write_json(OUT / "skus.json", skus_out)
    write_json(OUT / "concentration.json", concentration)
    write_json(OUT / "daily.json", daily_out)

    topo = None
    topo_path = OUT / "europe.topo.json"
    if topo_path.exists():
        topo = json.loads(topo_path.read_text(encoding="utf-8"))

    bundle = {
        "countries": countries,
        "monthly": monthly_out,
        "skus": skus_out,
        "concentration": concentration,
        "daily": daily_out,
        "facts": facts,
        "europe": topo,
        "meta": {
            "source": "UCI Online Retail II (online_retail_ii.xlsx)",
            "metric": "有效销售：非取消单、非退货、Price > 0",
        },
    }
    (OUT / "data.js").write_text(
        "window.__DATA__ = " + json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )

    write_facts(facts, countries, monthly_out, concentration)
    print(f"rows {n_raw:,} -> {n:,} (removed {dup_rows:,})")
    print(f"revenue {money(revenue_total)} | UK {pct(uk['share'])} | IE {money(ie['revenue'])} / {ie['orders']} inv / {ie['customers']} cust")
    print(f"months {len(months)} peak {months[peak_i]} {money(peak)} trough {months[trough_i]} {money(trough)} x{amplitude:.2f}")
    print(f"wrote {OUT}/ (data.js {((OUT/'data.js').stat().st_size/1024):.0f} KB)")


MONTHS: list[str] = []


def write_json(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def write_facts(f, countries, monthly, concentration):
    uk = countries[0]
    ie = next(c for c in countries if c["name"] == "Ireland")
    lines = []
    A = lines.append
    A("# facts.md — 正文数字的唯一来源")
    A("")
    A("> 全部数值由 `build_data.py` 生成。页面不得手写任何数字；文案引用本文件。")
    A("")
    A("## 0. 六个关键数字（验收用）")
    A("")
    A("| # | 键 | 值 | 页面出现位置 |")
    A("|---|---|---|---|")
    A(f"| 1 | 去重后行数 | **{f['rows']:,}** 行 | 第 0 幕 |")
    A(f"| 2 | 国家字段取值 | **{f['country_values']}** 个（其中真实国家 {f['real_countries']}，非国家/单独列出 {f['non_country_values']}） | 第 0 幕 |")
    A(f"| 3 | 月度序列 | **{f['months']}** 个完整自然月 | 第 0 幕 / 第 4 幕 |")
    A(f"| 4 | 有效销售额 | **{money(f['revenue'])}** | 第 0 幕 |")
    A(f"| 5 | 英国份额 | **{pct(f['uk_share'], 2)}** | 第 1 幕 |")
    A(f"| 6 | 爱尔兰客户数 | **{ie['customers']}** 个（营收 {money(ie['revenue'])}、有效发票 {ie['orders']} 张） | 第 3 幕 |")
    A("")
    A("## 1. 数据规模与期间")
    A("")
    A(f"- 原始行数（两 sheet 合计）：{f['rows_raw']:,}")
    A(f"- 重复行（按 Invoice+StockCode+Quantity+InvoiceDate+Price，保留 2009-2010 版本）：{f['rows_removed']:,}")
    A(f"- 去重后行数：**{f['rows']:,}**")
    A(f"- 期间：{f['date_min']} → {f['date_max']}（{f['days']} 天）")
    A(f"- 国家字段取值：{f['country_values']} 个；其中真实国家 {f['real_countries']} 个，非国家取值 3 个（Unspecified / European Community / West Indies），Channel Islands 单列不计入任何国家")
    A(f"- SKU：{f['skus']:,}")
    A(f"- 可识别客户：{f['customers']:,}")
    A("- 月度序列：2009-12 至 2011-11 共 24 个完整月；2011-12 仅 9 天，不计入月度序列（仍计入总额）")
    A("")
    A("## 2. 营收口径")
    A("")
    A("- `revenue` = 非取消单 + 非退货 + Price > 0 的 `Quantity × Price` 之和（施工图 §3.3 的 valid_sale）")
    A(f"- 有效销售额：**{money(f['revenue'])}**")
    A(f"- 非取消行合计（含退货冲减与坏账调整，即“净额”口径）：{money(f['net_incl_returns'])}")
    A(f"- 坏账调整（Price < 0 的 5 行）：{money(f['bad_debt_value'])}（全在英国）")
    A("")
    A("## 3. 国家分布")
    A("")
    A(f"- 英国：{money(uk['revenue'])}，份额 **{pct(uk['share'], 2)}**，有效发票 {uk['orders']:,} 张，客户 {uk['customers']:,}")
    A(f"- 欧洲 22 国合计：{money(f['europe_revenue'])}（{pct(f['europe_share'], 2)}）")
    A(f"- 前 12 国合计：{pct(f['top12_share'], 2)}")
    A(f"- 后 {f['tail_count']} 个取值合计：{pct(f['tail_share'], 2)}（{money(f['tail_revenue'])}）")
    A(f"- 非国家 + 海峡群岛：{money(f['non_country_revenue'])}（{pct(f['non_country_share'], 2)}）")
    A(f"- 欧洲以外最大单一市场：{pct(f['outside_eu_max_share'], 2)}（澳大利亚）")
    A("")
    A("| # | 国家 | 营收 | 份额 | 有效发票 | 客户 | 每客户营收 | 退货率 |")
    A("|---|---|---|---|---|---|---|---|")
    for c in countries[:15]:
        A(f"| {c['rank']} | {c['name_zh']} {c['name']} | {money(c['revenue'])} | {pct(c['share'], 2)} | {c['orders']:,} | {c['customers']:,} | {money(c['revenue_per_customer'])} | {pct(c['return_rate'])} |")
    A("")
    A(f"其余 {len(countries) - 15} 个取值见 `countries.json`。")
    A("")
    A("## 4. 爱尔兰（第 3 幕）")
    A("")
    A(f"- 营收 **{money(ie['revenue'])}**；份额 {pct(ie['share'], 2)}")
    A(f"- 有效发票 **{ie['orders']}** 张；若把取消单也算进去是 {ie['invoices_all']} 张")
    A(f"- 可识别客户 **{ie['customers']}** 个；每客户营收 {money(ie['revenue_per_customer'])}")
    A("")
    A("| 客户 ID | 营收 | 发票 | 占爱尔兰 | 有下单的月数 | 首单 | 末单 |")
    A("|---|---|---|---|---|---|---|")
    for c in ie["top_customers"]:
        A(f"| {c['id']} | {money(c['revenue'])} | {c['orders']} | {pct(c['share'], 1)} | "
          f"{c['active_months']}/24 | {c['first']} | {c['last']} |")
    _act20 = sum(1 for c in ie["top_customers"] if c["active_months"] >= 20)
    A("")
    A(f"- 24 个月里至少 20 个月有下单的客户：**{_act20}** 个（抽屉里的“日常”一句引用这个数）")
    A("")
    A("## 5. 月度与季节（第 4 幕）")
    A("")
    A(f"- 峰值：**{f['peak_month']} {money(f['peak_value'])}**")
    A(f"- 谷值：**{f['trough_month']} {money(f['trough_value'])}**")
    A(f"- 峰谷振幅：**{f['amplitude']:.2f}×**")
    A(f"- 月均：{money(f['mean_month'])}")
    A(f"- 月度退货率范围：{pct(min(monthly['return_rate']))} – {pct(max(monthly['return_rate']))}")
    A("")
    A("## 5b. 自身基线（第 4 幕热力图）")
    A("")
    A("每一行都除以该国自己的 24 月均值；下表是前 12 个取值。")
    A("")
    A("| 国家 | 占比 | 自身均值 | 峰值月 | 峰 / 均值 | 谷值月 | 谷 / 均值 |")
    A("|---|---|---|---|---|---|---|")
    _rows = countries[:12]
    for c in _rows:
        _m = c["monthly"]
        _mean = sum(_m) / len(_m) or 1
        _pi = max(range(len(_m)), key=lambda i: _m[i])
        _ti = min(range(len(_m)), key=lambda i: _m[i])
        A(f"| {c['name_zh']} | {pct(c['share'], 2)} | {money(_mean)} | {monthly['months'][_pi]} | "
          f"{_m[_pi] / _mean:.1f}× | {monthly['months'][_ti]} | {_m[_ti] / _mean:.1f}× |")
    A("")
    _uk = _rows[0]
    _ukm = _uk["monthly"]
    _ukmean = sum(_ukm) / len(_ukm)
    _ukp = max(range(len(_ukm)), key=lambda i: _ukm[i])
    _ukt = min(range(len(_ukm)), key=lambda i: _ukm[i])
    _wild = max(((_rows[i]["monthly"][max(range(24), key=lambda j: _rows[i]["monthly"][j])] /
                  (sum(_rows[i]["monthly"]) / 24), i) for i in range(len(_rows))), key=lambda t: t[0])
    _wi = _wild[1]
    _wj = max(range(24), key=lambda j: _rows[_wi]["monthly"][j])
    A(f"- 英国：峰 {monthly['months'][_ukp]} 是自身均值的 {_ukm[_ukp] / _ukmean:.1f} 倍；"
      f"谷 {monthly['months'][_ukt]} 是 {_ukm[_ukt] / _ukmean:.1f} 倍（热力图下方那句话引用这三个数）")
    A(f"- 十二国里相对波动最大：{_rows[_wi]['name_zh']} {monthly['months'][_wj]}，"
      f"{_rows[_wi]['monthly'][_wj] / (sum(_rows[_wi]['monthly']) / 24):.1f} 倍")
    A("- 措辞纪律：只能说“高于/低于自身基线”，不写“过热/过冷”")
    A("")
    A("## 6. 客户集中度（第 3 幕）")
    A("")
    A(f"- 可识别客户：{concentration['customers']:,}")
    A(f"- 前 10% 客户（{concentration['top10_customers']:,} 人）贡献 **{concentration['top10_share']:.1f}%** 营收")
    A(f"- 前 1% 客户（{concentration['top1_customers']:,} 人）贡献 **{concentration['top1_share']:.1f}%** 营收")
    A(f"- 基尼系数：{concentration['gini']:.3f}")
    A("")
    A("## 7. 数据的诚实话（第 5.5 幕）")
    A("")
    A(f"- 取消单：{f['cancel_rows']:,} 行（行占比 {pct(f['cancel_row_share'], 2)}），金额 {money(f['cancel_value'])}（占有效销售额 {pct(f['cancel_value_share'], 2)}）")
    A(f"- 负数量行（含退货/冲销）：{f['return_rows']:,} 行；其中带金额的退货全部以取消单形式出现")
    A(f"- 匿名订单：{f['anon_rows']:,} 行（行占比 {pct(f['anon_row_share'], 2)}），占销售额 {pct(f['anon_revenue_share'], 1)}；所有“每客户”指标的分母只含可识别客户")
    A(f"- 后 {f['tail_count']} 个取值合计 {pct(f['tail_share'], 2)}；4 个非国家/单列取值合计 {money(f['non_country_revenue'])}")
    A(f"- 2011-12 只有 9 天，未进入月度序列")
    A("")
    A("## 8. 与施工图原始值的差异（施工图为去重前口径）")
    A("")
    A("| 指标 | 施工图 | 本文件 | 差异原因 |")
    A("|---|---|---|---|")
    A(f"| 行数 | 1,067,371 | {f['rows']:,} | 去掉两 sheet 重叠 9 天的 {f['rows_removed']:,} 行 |")
    A(f"| 有效销售额 | £20.97M | {money(f['revenue'])} | 去重；且剔除 Price ≤ 0 的行 |")
    A(f"| 英国份额 | 84.94% | {pct(f['uk_share'], 2)} | 去重后分母变化；口径为有效销售 |")
    A(f"| 爱尔兰营收 | £615,520 | {money(ie['revenue'])} | 施工图该列实为“非取消行净额”，且未去重 |")
    A(f"| 爱尔兰发票 | 806 | {ie['orders']}（含取消单 {ie['invoices_all']}） | 施工图计的是含取消单的全部发票；此处只计有效销售发票 |")
    A(f"| 峰值 | 2011-11 £1.46M | {f['peak_month']} {money(f['peak_value'])} | 施工图把 2010-11 的数值标到了 2011-11 上 |")
    A(f"| 谷值 | 2010-02 £0.53M | {f['trough_month']} {money(f['trough_value'])} | 2011-02 实际更低 |")
    A(f"| 季节振幅 | 2.7× | {f['amplitude']:.2f}× | 由上一行峰谷值决定 |")
    A(f"| 取消单 | 19,494 行 / 1.83% | {f['cancel_rows']:,} 行 / {pct(f['cancel_row_share'], 2)} | 去重 |")
    A(f"| 匿名行 | 243,007 行 / 22.8% | {f['anon_rows']:,} 行 / {pct(f['anon_row_share'], 2)} | 去重 |")
    A(f"| 后 31 国合计 | 1.88% | {pct(f['tail_share'], 2)} | 去重；“31 个取值”含 4 个非国家取值 |")
    A("")
    A("## 9. 页面不得使用的说法")
    A("")
    A("- 不出现“实时”（这是 2009-12 → 2011-12 的历史批量数据）。")
    A("- 不出现“过热 / 过冷”，统一说“高于 / 低于自身基线”。")
    A("- 不写城市级下钻（数据里没有城市字段）。")
    A("- 不写“我们可以做……”或产品原型式表述。")
    (OUT / "facts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    # 24 个完整自然月：2009-12 .. 2011-11
    MONTHS = list(pd.period_range("2009-12", MONTH_END, freq="M").astype(str))
    build()
