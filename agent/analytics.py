"""Аналитика поверх калькулятора режимов.

  sensitivity()  — нагрузка каждого режима при разном доходе и точки, где меняется лучший режим;
  kpis()         — эффективная ставка, доход «на руки», экономия;
  calendar()     — календарь платежей выбранного режима на год;
  limits()       — насколько близко бизнес к лимитам режимов;
  service()      — аналитика самой госуслуги: статистика, типовые заявители, модель эффекта.
Все суммы считаются функциями из rules.py — отдельных «ручных» цифр здесь нет,
кроме официальной статистики в SERVICE_STATS (с источниками).
"""
from __future__ import annotations

from . import rules

CODES = ["npd", "usn6", "usn15", "psn", "ausn8", "ausn20", "osno"]


def _profile_dict(p: dict | rules.Profile) -> dict:
    pr = p if isinstance(p, rules.Profile) else rules.Profile.from_dict(p)
    return {k: getattr(pr, k) for k in pr.__dataclass_fields__}


def sensitivity(profile: dict, points: int = 48) -> dict:
    """Нагрузка по режимам при доходе от ~0 до max(2,5×дохода, 6 млн).

    Расходы масштабируются пропорционально доходу (та же доля), ФОТ и работники — как есть.
    Стоимость патента, если известна, считается фиксированной.
    """
    base = _profile_dict(profile)
    inc0 = max(float(base["income"]), 1.0)
    exp_share = float(base.get("expenses") or 0) / inc0
    top = max(inc0 * 2.5, 6_000_000)
    xs = [round(top * (i + 1) / points, -3) for i in range(points)]
    series = {c: [] for c in CODES}
    best_at = []
    for x in xs:
        res = rules.compare_regimes({**base, "income": x, "expenses": x * exp_share})
        by = {r["code"]: r for r in res["results"]}
        for c in CODES:
            r = by[c]
            series[c].append(r["total"] if r["available"] and r["total"] is not None else None)
        best_at.append(res["best"]["code"] if res["best"] else None)
    switches = []
    for i in range(1, len(xs)):
        if best_at[i] != best_at[i - 1] and best_at[i] and best_at[i - 1]:
            switches.append({"income": xs[i], "from": best_at[i - 1], "to": best_at[i],
                             "from_name": rules.REGIME_NAMES[best_at[i - 1]],
                             "to_name": rules.REGIME_NAMES[best_at[i]]})
    p = rules.PARAMS[int(base["year"])]
    thresholds = [
        {"income": p["npd_limit"], "label": "лимит НПД 2,4 млн"},
        {"income": p["psn_limit"], "label": f"лимит ПСН {p['psn_limit']/1e6:g} млн"},
        {"income": p["vat_free_limit"], "label": "НДС на УСН с 20 млн"},
    ]
    return {"x": xs, "series": series, "names": {c: rules.REGIME_NAMES[c] for c in CODES},
            "best_at": best_at, "switches": switches,
            "thresholds": [t for t in thresholds if t["income"] <= top],
            "income": inc0, "expense_share": exp_share}


def kpis(calc: dict) -> dict:
    inp = calc["input"]
    income = float(inp["income"]) or 1.0
    costs = float(inp.get("expenses") or 0) + float(inp.get("payroll") or 0)
    best = calc["best"]
    ranked = [r for r in calc["results"] if r["code"] in calc["ranking"]]
    if not best:
        return {}
    worst = max(ranked, key=lambda r: r["total"])
    usn6 = next((r for r in ranked if r["code"] == "usn6"), None)
    return {
        "best_code": best["code"], "best_name": best["name"], "best_total": best["total"],
        "effective_rate": best["total"] / income,
        "net_income": income - costs - best["total"],
        "net_share": (income - costs - best["total"]) / income,
        "monthly_payment": best["total"] / 12,
        "saving_vs_worst": worst["total"] - best["total"],
        "saving_vs_usn6": (usn6["total"] - best["total"]) if usn6 else None,
        "rates": {r["code"]: r["total"] / income for r in ranked},
    }


def calendar(calc: dict, regime: str | None = None) -> dict:
    """Платежи на год (упрощённо: налог распределён равномерно по срокам уплаты)."""
    year = int(calc["year"])
    by = {r["code"]: r for r in calc["results"]}
    code = regime or (calc["best"] or {}).get("code") or "usn6"
    r = by.get(code)
    if not r or not r["available"] or r["total"] is None:
        return {"regime": code, "items": []}
    items = []
    fixed = rules.PARAMS[year]["fixed_contrib"]
    extra = max(r["contrib_self"] - fixed, 0)

    def add(month, day, label, amount, nxt=False):
        if amount and amount > 0.5:
            items.append({"date": f"{year + (1 if nxt else 0)}-{month:02d}-{day:02d}",
                          "label": label, "amount": round(amount)})

    if code == "npd":
        for m in range(1, 13):
            add(m % 12 + 1, 28, "НПД за месяц", r["tax"] / 12, nxt=(m == 12))
    elif code in ("usn6", "usn15"):
        q = r["tax"] / 4
        add(4, 28, "Аванс УСН за I квартал", q)
        add(7, 28, "Аванс УСН за полугодие", q)
        add(10, 28, "Аванс УСН за 9 месяцев", q)
        add(4, 28, "УСН за год", q, nxt=True)
        if r["vat"]:
            for m in (4, 7, 10):
                add(m, 28, "НДС за квартал", r["vat"] / 4)
            add(1, 28, "НДС за IV квартал", r["vat"] / 4, nxt=True)
    elif code == "psn":
        add(3, 31, "Патент: 1/3 стоимости (90 дней)", r["tax"] / 3)
        add(12, 28, "Патент: 2/3 стоимости", r["tax"] * 2 / 3)
    elif code in ("ausn8", "ausn20"):
        for m in range(1, 13):
            add(m % 12 + 1, 25, "АУСН за месяц", r["tax"] / 12, nxt=(m == 12))
    elif code == "osno":
        q = r["tax"] / 4
        add(4, 28, "Аванс НДФЛ за I квартал", q)
        add(7, 28, "Аванс НДФЛ за полугодие", q)
        add(10, 28, "Аванс НДФЛ за 9 месяцев", q)
        add(7, 15, "НДФЛ за год (3-НДФЛ)", q, nxt=True)
        if r["vat"]:
            for m in (4, 7, 10):
                add(m, 28, "НДС за квартал", r["vat"] / 4)
            add(1, 28, "НДС за IV квартал", r["vat"] / 4, nxt=True)
    if code not in ("npd", "ausn8", "ausn20"):
        add(12, 28, "Фиксированные взносы ИП", fixed)
        add(7, 1, "Взнос 1% с дохода свыше 300 тыс.", extra, nxt=True)
    if r["contrib_staff"] and code not in ("ausn8", "ausn20"):
        for m in range(1, 13):
            add(m % 12 + 1, 28, "Взносы за работников", r["contrib_staff"] / 12, nxt=(m == 12))
    items.sort(key=lambda i: i["date"])
    by_month = {}
    for it in items:
        key = it["date"][:7]
        by_month[key] = by_month.get(key, 0) + it["amount"]
    return {"regime": code, "name": rules.REGIME_NAMES[code], "items": items,
            "by_month": [{"month": k, "amount": v} for k, v in sorted(by_month.items())]}


def limits(profile: dict) -> list[dict]:
    pr = rules.Profile.from_dict(profile)
    p = rules.PARAMS[pr.year]
    rows = [
        ("НПД (самозанятость)", p["npd_limit"]),
        (f"Патент (ПСН), {pr.year}", p["psn_limit"]),
        ("УСН без НДС", p["vat_free_limit"]),
        ("АУСН", p["ausn_limit"]),
        ("УСН", p["usn_limit"]),
    ]
    return [{"name": n, "limit": lim, "share": pr.income / lim, "left": lim - pr.income} for n, lim in rows]


def full(profile: dict) -> dict:
    """Всё сразу — для одного запроса с фронтенда."""
    calc = rules.compare_regimes(profile)
    regime = profile.get("calendar_regime") if isinstance(profile, dict) else None
    return {"calc": calc, "summary": rules.summary_text(calc), "kpis": kpis(calc),
            "sensitivity": sensitivity(profile), "calendar": calendar(calc, regime),
            "limits": limits(profile)}


# ---------------------------------------------------------------------------
# Аналитика самой услуги
# ---------------------------------------------------------------------------
SERVICE_STATS = [
    {"value": "874 тыс.", "label": "новых предпринимателей в 2025 году, 84% — ИП",
     "source": "Точка Банк / Sostav, итоги 2025",
     "url": "https://www.sostav.ru/publication/samye-perspektivnye-sfery-i-kuda-idut-novichki-tochka-bank-podvel-itogi-goda-80486.html"},
    {"value": "778 тыс.", "label": "предпринимателей закрылись за 2025 год, 68% — ИП",
     "source": "Точка Банк / Sostav, итоги 2025",
     "url": "https://www.sostav.ru/publication/samye-perspektivnye-sfery-i-kuda-idut-novichki-tochka-bank-podvel-itogi-goda-80486.html"},
    {"value": "4,59 млн", "label": "ИП в реестре МСП на 10.07.2026, +7,8% за год",
     "source": "ОПОРА РОССИИ по данным ФНС",
     "url": "https://opora.ru/site/assets/files/106444/reestra_msp.pdf"},
    {"value": "15,7 млн", "label": "самозанятых — будущие ИП, когда перерастут лимит 2,4 млн ₽",
     "source": "Альфа-Банк по данным ФНС",
     "url": "https://kurs.alfabank.ru/articles/nalog-na-professionalnyj-dohod-dlya-samozanyatyh-grazhdan/"},
]

REFORM = [
    {"what": "НДС для упрощенцев", "before": 60_000_000, "after": 20_000_000, "unit": "порог дохода, ₽"},
    {"what": "Лимит патента 2026", "before": 60_000_000, "after": 20_000_000, "unit": "₽"},
    {"what": "Лимит патента 2027", "before": 60_000_000, "after": 15_000_000, "unit": "₽"},
]

PERSONAS = [
    {"id": "nails", "name": "Маникюр на дому", "profile": {"income": 1_440_000, "b2c_share": 1.0}},
    {"id": "it", "name": "Сайты для компаний", "profile": {"income": 3_600_000, "b2c_share": 0.0, "psn_allowed": False}},
    {"id": "tutor", "name": "Репетитор", "profile": {"income": 900_000, "b2c_share": 1.0}},
    {"id": "repair", "name": "Ремонт квартир, 2 работника",
     "profile": {"income": 6_000_000, "expenses": 1_800_000, "employees": 2, "payroll": 1_440_000, "b2c_share": 1.0}},
    {"id": "market", "name": "Маркетплейс, 3 работника",
     "profile": {"income": 30_000_000, "expenses": 18_000_000, "employees": 3, "payroll": 1_800_000, "resale": True}},
]


def service(year: int = 2027) -> dict:
    personas = []
    for pers in PERSONAS:
        calc = rules.compare_regimes({**pers["profile"], "year": year})
        by = {r["code"]: r for r in calc["results"]}
        best = calc["best"]
        personas.append({
            "id": pers["id"], "name": pers["name"], "income": pers["profile"]["income"],
            "best_code": best["code"], "best_name": best["name"], "best": best["total"],
            "usn6": by["usn6"]["total"] if by["usn6"]["available"] else None,
            "osno": by["osno"]["total"],
        })
    overpay = [p["osno"] - p["best"] for p in personas if p["income"] <= 5_000_000]
    return {"stats": SERVICE_STATS, "reform": REFORM, "personas": personas,
            "new_ip_per_year": 734_000,  # 874 тыс. × 84%
            "typical_overpay_osno": round(sum(overpay) / len(overpay)),
            "year": year}


def effect(new_ip: int, share_helped: float, overpay: float) -> dict:
    helped = new_ip * share_helped
    return {"helped": round(helped), "saved": round(helped * overpay)}
