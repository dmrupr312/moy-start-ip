"""Налоговые параметры и калькулятор режимов для ИП (2026–2027).

Все числа — из НК РФ и федеральных законов (ссылки в SOURCES).
Калькулятор даёт ОЦЕНКУ годовой нагрузки при полном годе работы,
для сравнения режимов между собой. Это не налоговая консультация.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# ---------------------------------------------------------------------------
# Параметры по годам
# ---------------------------------------------------------------------------
PARAMS = {
    2026: {
        "fixed_contrib": 57_390,          # ст. 430 НК РФ
        "extra_contrib_cap": 321_818,     # максимум 1% с дохода > 300 тыс.
        "usn_limit": 490_500_000,         # 450 млн × дефлятор 1,09
        "usn_staff_limit": 130,
        "vat_free_limit": 20_000_000,     # ст. 145 НК РФ (425-ФЗ, 228-ФЗ)
        "vat5_limit": 272_500_000,        # УСН: НДС 5% до этого дохода, далее 7%
        "psn_limit": 20_000_000,          # ст. 346.45 НК РФ (425-ФЗ)
        "psn_staff_limit": 15,
        "npd_limit": 2_400_000,           # 422-ФЗ
        "ausn_limit": 60_000_000,         # 17-ФЗ
        "ausn_staff_limit": 5,
        "ausn_injury": 2_959,             # взнос на травматизм на АУСН, руб./год
        "vat_rate": 0.22,
        "note": "",
    },
    2027: {
        "fixed_contrib": 61_154,
        "extra_contrib_cap": 342_923,
        "usn_limit": 490_500_000,         # будет проиндексирован дефлятором 2027
        "usn_staff_limit": 130,
        "vat_free_limit": 20_000_000,     # заморожен 228-ФЗ от 04.07.2026
        "vat5_limit": 272_500_000,        # будет проиндексирован
        "psn_limit": 15_000_000,          # снижается по 425-ФЗ
        "psn_staff_limit": 15,
        "npd_limit": 2_400_000,
        "ausn_limit": 60_000_000,
        "ausn_staff_limit": 5,
        "ausn_injury": 2_959,
        "vat_rate": 0.22,
        "note": "Лимиты УСН на 2027 год будут проиндексированы на дефлятор; "
                "в расчёте использованы значения 2026 года.",
    },
}

NDFL_SCALE = [  # прогрессивная шкала НДФЛ с 2025 г. (ст. 224 НК РФ)
    (2_400_000, 0.13),
    (5_000_000, 0.15),
    (20_000_000, 0.18),
    (50_000_000, 0.20),
    (float("inf"), 0.22),
]

EMPLOYEE_CONTRIB_RATE = 0.30  # упрощённо: единый тариф взносов за работников

SOURCES = {
    "Взносы ИП 2026–2027": "ст. 430 НК РФ; https://www.nalog.gov.ru/rn16/news/tax_doc_news/16617063/",
    "НДС на УСН, порог 20 млн": "ст. 145 НК РФ, 425-ФЗ от 28.11.2025, 228-ФЗ от 04.07.2026",
    "ПСН, лимит 20/15 млн": "пп. 1 п. 6 ст. 346.45 НК РФ (425-ФЗ); https://www.nalog.gov.ru/rn48/news/activities_fts/16591469/",
    "УСН 6%/15%, лимиты": "ст. 346.13, 346.20 НК РФ",
    "НПД": "422-ФЗ от 27.11.2018",
    "АУСН": "17-ФЗ от 25.02.2022",
    "НДФЛ, прогрессивная шкала": "ст. 224 НК РФ",
}

REGIME_NAMES = {
    "npd": "Самозанятость (НПД) для ИП",
    "usn6": "УСН «Доходы» 6%",
    "usn15": "УСН «Доходы минус расходы» 15%",
    "psn": "Патент (ПСН)",
    "ausn8": "АУСН «Доходы» 8%",
    "ausn20": "АУСН «Доходы минус расходы» 20%",
    "osno": "ОСНО (НДФЛ + НДС)",
}


@dataclass
class Profile:
    income: float                      # ожидаемая выручка за год, руб.
    expenses: float = 0.0              # расходы за год без зарплат, руб.
    employees: int = 0                 # число работников по трудовым договорам
    payroll: float = 0.0               # фонд оплаты труда за год, руб.
    b2c_share: float = 1.0             # доля выручки от физлиц (для НПД)
    resale: bool = False               # перепродажа чужих товаров
    psn_allowed: Optional[bool] = None # вид деятельности есть в региональном перечне ПСН
    patent_cost: Optional[float] = None  # стоимость патента на год (калькулятор ФНС)
    patent_potential_income: Optional[float] = None
    ausn_region: Optional[bool] = None # АУСН введена в регионе
    usn6_rate: float = 0.06            # региональная ставка УСН «Доходы»
    usn15_rate: float = 0.15           # региональная ставка УСН «Д-Р»
    year: int = 2027

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None and d[k] != ""}
        p = cls(**known)
        p.income = float(p.income)
        p.expenses = float(p.expenses or 0)
        p.employees = int(p.employees or 0)
        p.payroll = float(p.payroll or 0)
        p.b2c_share = min(max(float(p.b2c_share), 0.0), 1.0)
        p.year = int(p.year) if int(p.year) in PARAMS else 2027
        if isinstance(p.resale, str):
            p.resale = p.resale.lower() in ("1", "true", "да", "yes")
        return p


@dataclass
class RegimeResult:
    code: str
    name: str
    available: bool
    total: Optional[float] = None       # итоговая нагрузка за год
    tax: float = 0.0
    contrib_self: float = 0.0
    contrib_staff: float = 0.0
    vat: float = 0.0
    reasons: list = field(default_factory=list)   # почему недоступен / оговорки
    notes: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        for k in ("total", "tax", "contrib_self", "contrib_staff", "vat"):
            if d[k] is not None:
                d[k] = round(d[k])
        return d


# ---------------------------------------------------------------------------
# Вспомогательные расчёты
# ---------------------------------------------------------------------------
def self_contrib(base_income: float, year: int) -> float:
    """Взносы ИП за себя: фиксированные + 1% с дохода свыше 300 тыс. (с потолком)."""
    p = PARAMS[year]
    extra = min(max(base_income - 300_000, 0) * 0.01, p["extra_contrib_cap"])
    return p["fixed_contrib"] + extra


def ndfl_progressive(base: float) -> float:
    tax, prev = 0.0, 0.0
    for limit, rate in NDFL_SCALE:
        if base <= prev:
            break
        part = min(base, limit) - prev
        tax += part * rate
        prev = limit
    return tax


def usn_vat(income: float, year: int) -> tuple[float, str]:
    """НДС для упрощенца с пониженной ставкой без вычетов (выручка с НДС)."""
    p = PARAMS[year]
    if income <= p["vat_free_limit"]:
        return 0.0, ""
    rate = 0.05 if income <= p["vat5_limit"] else 0.07
    return income * rate / (1 + rate), f"НДС {int(rate*100)}% (без вычетов) — доход выше {p['vat_free_limit']/1e6:.0f} млн"


def fmt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{round(x):,}".replace(",", " ") + " ₽"


# ---------------------------------------------------------------------------
# Расчёт по каждому режиму
# ---------------------------------------------------------------------------
def calc_npd(pr: Profile) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult("npd", REGIME_NAMES["npd"], True)
    if pr.income > p["npd_limit"]:
        r.reasons.append(f"доход выше лимита {fmt(p['npd_limit'])} в год")
    if pr.employees > 0:
        r.reasons.append("нельзя нанимать работников по трудовым договорам")
    if pr.resale:
        r.reasons.append("нельзя перепродавать чужие товары")
    if r.reasons:
        r.available = False
        return r
    b2c = pr.income * pr.b2c_share
    b2b = pr.income - b2c
    tax = b2c * 0.04 + b2b * 0.06
    # налоговый вычет 10 000 ₽: снижает ставку 4%→3% и 6%→4% до исчерпания
    deduction = min(10_000, b2c * 0.01 + b2b * 0.02)
    r.tax = tax - deduction
    r.total = r.tax
    r.notes += [
        "4% с доходов от физлиц, 6% — от юрлиц и ИП; вычет 10 000 ₽ учтён",
        "фиксированные взносы ИП не платятся, но и пенсионный стаж не идёт без добровольных взносов",
        "регистрация в приложении «Мой налог», декларации нет",
    ]
    return r


def calc_usn6(pr: Profile) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult("usn6", REGIME_NAMES["usn6"], True)
    if pr.income > p["usn_limit"] or pr.employees > p["usn_staff_limit"]:
        r.available = False
        r.reasons.append("превышены лимиты УСН (доход или численность)")
        return r
    r.contrib_self = self_contrib(pr.income, pr.year)
    r.contrib_staff = pr.payroll * EMPLOYEE_CONTRIB_RATE
    tax = pr.income * pr.usn6_rate
    contribs = r.contrib_self + r.contrib_staff
    reduction = min(contribs, tax) if pr.employees == 0 else min(contribs, tax * 0.5)
    r.tax = tax - reduction
    r.vat, vat_note = usn_vat(pr.income, pr.year)
    r.total = r.tax + r.contrib_self + r.contrib_staff + r.vat
    r.notes.append("налог уменьшается на взносы: без работников — до нуля, с работниками — до 50%")
    if pr.usn6_rate != 0.06:
        r.notes.append(f"применена региональная ставка {pr.usn6_rate*100:g}%")
    if vat_note:
        r.notes.append(vat_note)
    return r


def calc_usn15(pr: Profile) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult("usn15", REGIME_NAMES["usn15"], True)
    if pr.income > p["usn_limit"] or pr.employees > p["usn_staff_limit"]:
        r.available = False
        r.reasons.append("превышены лимиты УСН (доход или численность)")
        return r
    profit_before = pr.income - pr.expenses - pr.payroll
    r.contrib_self = self_contrib(max(profit_before, 0), pr.year)
    r.contrib_staff = pr.payroll * EMPLOYEE_CONTRIB_RATE
    r.vat, vat_note = usn_vat(pr.income, pr.year)
    base = profit_before - r.contrib_self - r.contrib_staff - r.vat
    r.tax = max(base * pr.usn15_rate, pr.income * 0.01)
    r.total = r.tax + r.contrib_self + r.contrib_staff + r.vat
    r.notes.append("расходы нужно подтверждать документами; минимальный налог — 1% от доходов")
    if base * pr.usn15_rate < pr.income * 0.01:
        r.notes.append("сработал минимальный налог 1%")
    if vat_note:
        r.notes.append(vat_note)
    return r


def calc_psn(pr: Profile) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult("psn", REGIME_NAMES["psn"], True)
    if pr.income > p["psn_limit"]:
        r.reasons.append(f"доход выше лимита ПСН {fmt(p['psn_limit'])} на {pr.year} год")
    if pr.employees > p["psn_staff_limit"]:
        r.reasons.append(f"больше {p['psn_staff_limit']} работников")
    if pr.psn_allowed is False:
        r.reasons.append("вид деятельности обычно не входит в перечни ПСН (уточните для своего региона)")
    if r.reasons:
        r.available = False
        return r
    if pr.patent_cost is None:
        r.notes.append("стоимость патента зависит от региона и вида деятельности — "
                       "посчитайте на patent.nalog.gov.ru и введите её, чтобы сравнить")
        if pr.psn_allowed is None:
            r.notes.append("проверьте, есть ли ваш вид деятельности в региональном перечне ПСН")
        return r
    base_for_contrib = pr.patent_potential_income if pr.patent_potential_income else pr.income
    r.contrib_self = self_contrib(base_for_contrib, pr.year)
    r.contrib_staff = pr.payroll * EMPLOYEE_CONTRIB_RATE
    contribs = r.contrib_self + r.contrib_staff
    cost = pr.patent_cost
    reduction = min(contribs, cost) if pr.employees == 0 else min(contribs, cost * 0.5)
    r.tax = cost - reduction
    r.total = r.tax + r.contrib_self + r.contrib_staff
    r.notes.append("стоимость патента уменьшается на взносы (без работников — до нуля)")
    if pr.psn_allowed is None:
        r.notes.append("проверьте, есть ли ваш вид деятельности в региональном перечне ПСН")
    r.notes.append("ПСН можно совмещать с УСН для остальных видов деятельности")
    return r


def calc_ausn(pr: Profile, code: str) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult(code, REGIME_NAMES[code], True)
    if pr.income > p["ausn_limit"]:
        r.reasons.append(f"доход выше лимита АУСН {fmt(p['ausn_limit'])}")
    if pr.employees > p["ausn_staff_limit"]:
        r.reasons.append(f"больше {p['ausn_staff_limit']} работников")
    if pr.ausn_region is False:
        r.reasons.append("АУСН не введена в вашем регионе")
    if r.reasons:
        r.available = False
        return r
    r.contrib_self = 0.0
    r.contrib_staff = p["ausn_injury"] if pr.employees > 0 else 0.0
    if code == "ausn8":
        r.tax = pr.income * 0.08
    else:
        base = pr.income - pr.expenses - pr.payroll
        r.tax = max(base * 0.20, pr.income * 0.03)
    r.total = r.tax + r.contrib_staff
    r.notes += [
        "взносы за себя и за работников не платятся (только на травматизм)",
        "НДС не платится при доходе до 60 млн; отчётность считает банк и ФНС",
        "счёт — только в уполномоченном банке, зарплата — только безналом",
    ]
    if pr.ausn_region is None:
        r.notes.append("проверьте, введена ли АУСН в вашем регионе")
    return r


def calc_osno(pr: Profile) -> RegimeResult:
    p = PARAMS[pr.year]
    r = RegimeResult("osno", REGIME_NAMES["osno"], True)
    profit_before = pr.income - pr.expenses - pr.payroll
    r.contrib_self = self_contrib(max(profit_before, 0), pr.year)
    r.contrib_staff = pr.payroll * EMPLOYEE_CONTRIB_RATE
    # НДС: если выручка за 3 месяца ≤ 2 млн, можно получить освобождение (ст. 145)
    if pr.income / 4 <= 2_000_000:
        r.vat = 0.0
        r.notes.append("возможно освобождение от НДС: выручка за 3 месяца не больше 2 млн")
    else:
        r.vat = max(pr.income - pr.expenses, 0) * p["vat_rate"] / (1 + p["vat_rate"])
        r.notes.append("НДС 22% с вычетом входного НДС (упрощённо: все расходы с НДС)")
    base = max(profit_before - r.contrib_self - r.contrib_staff - r.vat, 0)
    r.tax = ndfl_progressive(base)
    r.total = r.tax + r.contrib_self + r.contrib_staff + r.vat
    r.notes.append("НДФЛ по прогрессивной шкале 13–22%, самый сложный учёт")
    return r


def compare_regimes(profile: dict | Profile) -> dict:
    pr = profile if isinstance(profile, Profile) else Profile.from_dict(profile)
    results = [
        calc_npd(pr), calc_usn6(pr), calc_usn15(pr), calc_psn(pr),
        calc_ausn(pr, "ausn8"), calc_ausn(pr, "ausn20"), calc_osno(pr),
    ]
    ranked = sorted([r for r in results if r.available and r.total is not None],
                    key=lambda r: r.total)
    best = ranked[0] if ranked else None
    warnings = []
    p = PARAMS[pr.year]
    if p["note"]:
        warnings.append(p["note"])
    if p["vat_free_limit"] < pr.income <= p["usn_limit"]:
        warnings.append("Доход выше 20 млн: на УСН появится НДС. Сравните ставку 5% без вычетов "
                        "и 22% с вычетами — зависит от ваших покупателей и поставщиков.")
    if pr.employees == 0 and pr.income <= p["npd_limit"] and not pr.resale:
        warnings.append("Если доход стабильно ниже 2,4 млн и нет работников — "
                        "НПД (самозанятость) почти всегда дешевле всего.")
    if pr.year == 2027 and 15_000_000 < pr.income <= 20_000_000:
        warnings.append("В 2027 году лимит ПСН — 15 млн (в 2026 было 20 млн).")
    if best and best.code.startswith("ausn") and pr.ausn_region is None:
        warnings.append("АУСН выгоднее всего, но она действует не во всех регионах — проверьте свой регион. "
                        "Если АУСН нет, ориентируйтесь на следующий режим в списке.")
    savings = None
    if best and len(ranked) > 1:
        worst = max(ranked, key=lambda r: r.total)
        savings = worst.total - best.total
    return {
        "year": pr.year,
        "input": asdict(pr),
        "results": [r.to_dict() for r in results],
        "ranking": [r.code for r in ranked],
        "best": best.to_dict() if best else None,
        "savings_vs_worst": round(savings) if savings is not None else None,
        "warnings": warnings,
        "disclaimer": "Оценка за полный год для сравнения режимов. Не является налоговой "
                      "консультацией; перед выбором сверьтесь с ФНС (8-800-222-22-22).",
    }


def summary_text(res: dict) -> str:
    """Человекочитаемое резюме результата сравнения."""
    lines = [f"Сравнение режимов на {res['year']} год (оценка годовой нагрузки):"]
    by_code = {r["code"]: r for r in res["results"]}
    for code in res["ranking"]:
        r = by_code[code]
        lines.append(f"• {r['name']}: {fmt(r['total'])}")
    for r in res["results"]:
        if r["available"] and r["total"] is None:
            lines.append(f"• {r['name']}: нужна стоимость патента — " + "; ".join(r["notes"][:1]))
    unavailable = [r for r in res["results"] if not r["available"]]
    if unavailable:
        lines.append("Недоступны:")
        for r in unavailable:
            lines.append(f"  – {r['name']}: {', '.join(r['reasons'])}")
    if res["best"]:
        lines.append(f"Выгоднее всего: {res['best']['name']}.")
        if res["savings_vs_worst"]:
            lines.append(f"Разница с самым дорогим доступным режимом: {fmt(res['savings_vs_worst'])} в год.")
    for w in res["warnings"]:
        lines.append(f"⚠ {w}")
    return "\n".join(lines)
