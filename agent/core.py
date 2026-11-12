"""Ядро ИИ-агента «Мой старт ИП».

Агент ведёт диалог, собирает профиль будущего бизнеса и вызывает инструменты:
  compare_regimes    — сравнение налоговых режимов (детерминированный калькулятор),
  search_okved       — подбор кодов ОКВЭД,
  registration_plan  — пошаговый план регистрации и выбора режима,
  precheck           — предпроверка данных для Р21001 на основания для отказа.

Режимы работы:
  * LLM-режим: модель ведёт разговор и решает, какой инструмент вызвать
    (протокол вызова — JSON в ответе модели, работает с любой LLM);
  * офлайн-режим «мастер»: если LLM не настроена или недоступна,
    агент сам извлекает параметры из текста и даёт результат по тем же инструментам.
Все числа в ответах берутся только из инструментов, а не «из головы» модели.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from . import checklist, okved, rules
from .llm import LLMError, from_env

# ---------------------------------------------------------------------------
# Извлечение параметров из текста (используется в обоих режимах)
# ---------------------------------------------------------------------------
_NUM = r"(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)"
_MONEY = re.compile(
    _NUM + r"\s*(млрд|миллиард\w*|млн|миллион\w*|мл?н?\.?|тыс\w*|т\.р\.?|тр|k|к)?\s*"
    r"(?:руб\w*|₽|р\.?)?\s*(в\s*мес\w*|/\s*мес\w*|ежемесячно|за\s*месяц|в\s*год|/\s*год|за\s*год|ежегодно)?",
    re.I,
)
_INCOME_KW = re.compile(r"доход|выручк|зарабат|оборот|получа|продаж\w* на|заработ", re.I)
_EXP_KW = re.compile(r"расход|затрат|трат|закуп|аренд\w* обходит|себестоим", re.I)
_PAYROLL_KW = re.compile(r"зарплат|фот\b|оплат\w* труд", re.I)


def _to_number(num: str, unit: str | None) -> float:
    v = float(num.replace(" ", "").replace(" ", "").replace(",", "."))
    u = (unit or "").lower()
    if u.startswith(("млрд", "миллиард")):
        v *= 1e9
    elif u.startswith(("млн", "миллион", "мн", "м")):
        v *= 1e6
    elif u.startswith(("тыс", "т.р", "тр", "k", "к")):
        v *= 1e3
    return v


def extract_profile(text: str, prof: dict) -> dict:
    """Дополняет профиль сведениями из реплики пользователя."""
    t = text.lower().replace("ё", "е")
    upd = {}
    for m in _MONEY.finditer(t):
        num, unit, period = m.group(1), m.group(2), m.group(3)
        if not unit and float(num.replace(" ", "").replace(",", ".")) < 1000:
            continue  # «2 сотрудника», «6%» и т.п.
        val = _to_number(num, unit)
        if period and "мес" in period:
            val *= 12
        before = t[max(0, m.start() - 45): m.start()]
        if _PAYROLL_KW.search(before):
            upd["payroll"] = val
        elif _EXP_KW.search(before):
            upd["expenses"] = val
        elif _INCOME_KW.search(before) or ("income" not in prof and "income" not in upd):
            upd["income"] = val
        elif re.search(r"патент\w* (стоит|стоимост)", before):
            upd["patent_cost"] = val
    m = re.search(r"(\d+)\s*(сотрудник|работник|человек|наемн|чел\b)", t)
    if m:
        upd["employees"] = int(m.group(1))
    elif re.search(r"(без|нет|не будет|не планиру\w*)\s*(сотрудник|работник|наем)|один работаю|работаю один|сам(а)? по себе|сам(а)? работаю", t):
        upd["employees"] = 0
    if re.search(r"физ\w*\s*лиц|частн\w* клиент|людям|населени", t) and not re.search(r"юр\w*\s*лиц|компани|организац|бизнес\w* клиент", t):
        upd["b2c_share"] = 1.0
    elif re.search(r"юр\w*\s*лиц|компани|организац|бизнес\w* клиент|b2b", t) and not re.search(r"физ\w*\s*лиц|частн", t):
        upd["b2c_share"] = 0.0
    elif re.search(r"и (физ|юр)\w*", t):
        upd["b2c_share"] = 0.5
    if re.search(r"перепрод|закупа\w* и прода|оптом", t):
        upd["resale"] = True
    m = re.search(r"(20\d\d)\s*год", t)
    if m and int(m.group(1)) in rules.PARAMS:
        upd["year"] = int(m.group(1))
    prof.update(upd)
    return upd


# ---------------------------------------------------------------------------
# Инструменты
# ---------------------------------------------------------------------------
def tool_compare_regimes(args: dict, state: dict) -> dict:
    prof = {**state["profile"], **{k: v for k, v in args.items() if v is not None}}
    if not prof.get("income"):
        return {"error": "Не указан ожидаемый доход за год (income)."}
    if prof.get("resale") is None and state.get("okved"):
        prof["resale"] = not state["okved"][0]["npd_ok"]
    if prof.get("psn_allowed") is None and state.get("okved"):
        if not state["okved"][0]["psn_typical"]:
            prof["psn_allowed"] = False
    state["profile"].update({k: v for k, v in args.items() if v is not None})
    res = rules.compare_regimes(prof)
    state["calc"] = res
    return {"summary": rules.summary_text(res), "best": res["best"]["code"] if res["best"] else None}


def tool_search_okved(args: dict, state: dict) -> dict:
    q = args.get("query") or state["profile"].get("activity", "")
    found = okved.search_okved(q)
    if found:
        state["okved"] = found
        state["profile"]["activity"] = q
    return {"codes": found, "hint": "Основной код — первый подходящий; добавьте 2–5 смежных. "
                                     "Полный классификатор: ОК 029-2014."}


def tool_registration_plan(args: dict, state: dict) -> dict:
    regime = args.get("regime") or (state.get("calc", {}).get("best") or {}).get("code") or "usn6"
    plan = checklist.registration_plan(regime, args.get("channel", "gosuslugi"), bool(args.get("foreign")))
    state["plan"] = {**plan, "regime": regime}
    return plan


def tool_precheck(args: dict, state: dict) -> dict:
    if not args.get("okved") and state.get("okved"):
        args["okved"] = [c["code"] for c in state["okved"][:3]]
    res = checklist.precheck(args)
    state["precheck"] = res
    regime = (state.get("calc", {}).get("best") or {}).get("code", "usn6")
    state["draft"] = checklist.r21001_draft(args, regime)
    return res


TOOLS = {
    "compare_regimes": tool_compare_regimes,
    "search_okved": tool_search_okved,
    "registration_plan": tool_registration_plan,
    "precheck": tool_precheck,
}

SYSTEM_PROMPT = """Ты — ИИ-агент «Мой старт ИП», помощник по государственной услуге \
«Государственная регистрация физического лица в качестве индивидуального предпринимателя» \
и выбору налогового режима. Говоришь по-русски, коротко и по делу, без канцелярита.

Цель: чтобы человек подал заявление Р21001 без ошибок с первого раза, выбрал коды ОКВЭД \
и налоговый режим, который ему выгоден, и не пропустил сроки (уведомление об УСН — 30 дней).

У тебя есть инструменты. Чтобы вызвать инструмент, ответь ТОЛЬКО JSON-объектом без текста вокруг:
{"tool": "<имя>", "args": {...}}

Инструменты:
1. search_okved — подобрать коды ОКВЭД. args: {"query": "описание деятельности своими словами"}
2. compare_regimes — сравнить налоговые режимы (НПД, УСН 6%, УСН 15%, ПСН, АУСН, ОСНО) по деньгам.
   args: {"income": доход за год в рублях, "expenses": расходы за год, "employees": число работников,
          "payroll": ФОТ за год, "b2c_share": доля выручки от физлиц 0..1, "resale": true/false,
          "patent_cost": стоимость патента за год, "psn_allowed": true/false, "ausn_region": true/false,
          "year": 2026 или 2027}
   Обязателен только income, остальное — если знаешь.
3. registration_plan — пошаговый план регистрации. args: {"regime": "npd|usn6|usn15|psn|ausn8|ausn20|osno",
   "channel": "gosuslugi|fns|bank|mfc|ifns", "foreign": true/false}
4. precheck — проверить данные заявителя на ошибки, из-за которых откажут.
   args: {"fio", "birth_date": "ГГГГ-ММ-ДД", "passport", "inn", "okved": [коды], "email",
          "channel", "already_ip", "court_ban", "bankrupt_recent"}

Правила:
- Никогда не придумывай суммы налогов, ставки и лимиты — бери их только из результатов инструментов.
- Если не знаешь вид деятельности — спроси. Если не знаешь примерный доход — спроси (можно в месяц).
- Сначала search_okved, потом compare_regimes, потом предложи план (registration_plan) и проверку данных.
- После результата инструмента объясни человеку вывод простыми словами: что выбрать, почему, \
что сделать дальше и в какие сроки. Упомяни важные оговорки (НДС > 20 млн, лимит ПСН, регион для АУСН/ПСН).
- Не проси паспортные данные без необходимости; предупреждай, что данные не сохраняются.
- Ты не заменяешь налоговую консультацию; при сложных случаях советуй ФНС (8-800-222-22-22).
- Не отвечай на темы, не связанные с регистрацией бизнеса и налогами ИП; вежливо возвращай к теме.

Известно о пользователе сейчас: {profile}
"""


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------
class Sessions:
    def __init__(self, ttl: int = 3600):
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.ttl = ttl

    def get(self, sid: str | None) -> tuple[str, dict]:
        with self._lock:
            now = time.time()
            for k in [k for k, v in self._d.items() if now - v["ts"] > self.ttl]:
                del self._d[k]
            if not sid or sid not in self._d:
                sid = sid or uuid.uuid4().hex
                self._d[sid] = {"profile": {}, "history": [], "ts": now}
            self._d[sid]["ts"] = now
            return sid, self._d[sid]

    def reset(self, sid: str):
        with self._lock:
            self._d.pop(sid, None)


def _parse_tool_call(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m or '"tool"' not in m.group(0):
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if obj.get("tool") in TOOLS:
        return obj["tool"], obj.get("args") or {}
    return None


class Agent:
    MAX_STEPS = 5

    def __init__(self, llm="auto"):
        self.llm = from_env() if llm == "auto" else llm
        self.sessions = Sessions()

    @property
    def mode(self) -> str:
        return f"LLM: {self.llm.name}" if self.llm else "офлайн-мастер (без LLM)"

    # -- публичный метод -----------------------------------------------------
    def handle(self, sid: str | None, message: str) -> dict:
        sid, st = self.sessions.get(sid)
        msg = (message or "").strip()
        if msg.lower() in ("/start", "заново", "сначала", "сброс", "/reset"):
            self.sessions.reset(sid)
            sid, st = self.sessions.get(sid)
            return self._pack(sid, st, GREETING, [])
        # новый вид деятельности в том же чате — начинаем профиль заново
        found = okved.search_okved(msg)
        if found and found[0]["score"] >= 2 and st.get("okved") and \
                found[0]["code"] not in [c["code"] for c in st["okved"][:2]]:
            st["profile"], st["okved"] = {}, None
            for k in ("calc", "plan", "precheck", "draft"):
                st.pop(k, None)
        extract_profile(msg, st["profile"])
        trace = []
        if self.llm:
            try:
                reply = self._llm_turn(st, msg, trace)
                return self._pack(sid, st, reply, trace)
            except LLMError as e:
                trace.append({"error": f"LLM недоступна: {e}. Переключаюсь на офлайн-режим."})
        reply = self._offline_turn(st, msg, trace)
        return self._pack(sid, st, reply, trace)

    def _pack(self, sid, st, reply, trace):
        st["history"].append({"role": "assistant", "content": reply})
        return {
            "session_id": sid, "reply": reply, "mode": self.mode, "trace": trace,
            "profile": st["profile"], "okved": st.get("okved"), "calc": st.get("calc"),
            "plan": st.get("plan"), "precheck": st.get("precheck"), "draft": st.get("draft"),
        }

    # -- LLM-режим ----------------------------------------------------------
    def _llm_turn(self, st: dict, msg: str, trace: list) -> str:
        st["history"].append({"role": "user", "content": msg})
        work = list(st["history"][-16:])
        for _ in range(self.MAX_STEPS):
            sys = SYSTEM_PROMPT.replace("{profile}", json.dumps(st["profile"], ensure_ascii=False))
            out = self.llm.chat([{"role": "system", "content": sys}] + work)
            call = _parse_tool_call(out)
            if not call:
                return out.strip()
            name, args = call
            try:
                result = TOOLS[name](dict(args), st)
            except Exception as e:  # ошибки инструментов не должны ронять диалог
                result = {"error": str(e)}
            trace.append({"tool": name, "args": args})
            work.append({"role": "assistant", "content": out})
            work.append({"role": "user", "content":
                         f"РЕЗУЛЬТАТ ИНСТРУМЕНТА {name}:\n{json.dumps(result, ensure_ascii=False)[:6000]}\n"
                         "Продолжи: вызови следующий инструмент или ответь пользователю."})
        return "Я собрал данные — посмотрите карточки с расчётом справа. Что уточнить?"

    # -- офлайн-режим -------------------------------------------------------
    def _offline_turn(self, st: dict, msg: str, trace: list) -> str:
        st["history"].append({"role": "user", "content": msg})
        prof = st["profile"]
        low = msg.lower()

        faq = _faq(low)
        if faq and (prof.get("income") or not _looks_like_profile(low)):
            return faq

        if not st.get("okved") and len(msg) > 3 and not _only_numbers(low):
            found = tool_search_okved({"query": msg}, st)
            trace.append({"tool": "search_okved", "args": {"query": msg}})
            if not found["codes"]:
                return ("Расскажите своими словами, чем будете заниматься — например, «делаю сайты для "
                        "компаний» или «маникюр на дому». По описанию я подберу коды ОКВЭД.")
        if not st.get("okved"):
            return GREETING
        if not prof.get("income"):
            top = st["okved"][0]
            return (f"Похоже, ваш основной код ОКВЭД — {top['code']} «{top['title']}».\n"
                    "Сколько примерно планируете получать: в месяц или в год? "
                    "И будут ли сотрудники? Например: «150 тысяч в месяц, без сотрудников, клиенты — физлица».")

        tool_compare_regimes({}, st)
        trace.append({"tool": "compare_regimes", "args": {k: prof.get(k) for k in ("income", "expenses", "employees")}})
        best = st["calc"]["best"]["code"] if st["calc"]["best"] else "usn6"
        tool_registration_plan({"regime": best}, st)
        trace.append({"tool": "registration_plan", "args": {"regime": best}})

        codes = st["okved"]
        lines = ["Коды ОКВЭД для заявления:"]
        lines += [f"• {c['code']} — {c['title']}" for c in codes[:3]]
        lines.append("")
        lines.append(rules.summary_text(st["calc"]))
        lines.append("")
        lines.append("Что сделать дальше:")
        lines += [f"{i}. {s}" for i, s in enumerate(st["plan"]["steps"][3:7], 1)]
        lines.append("")
        lines.append("Хотите, проверю данные для заявления Р21001? Нажмите «Проверить заявление» в панели справа. "
                     "Можно уточнить расходы, число сотрудников или стоимость патента — пересчитаю.")
        return "\n".join(lines)


def _only_numbers(t: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,кkмлнтысруб₽/вмесяцгодежно]+", t))


def _looks_like_profile(t: str) -> bool:
    return bool(_MONEY.search(t)) or bool(re.search(r"занима|делаю|открыть|хочу|буду", t))


def _faq(t: str) -> str | None:
    if re.search(r"взнос", t):
        p26, p27 = rules.PARAMS[2026], rules.PARAMS[2027]
        return (f"Взносы ИП «за себя»: фиксированные {rules.fmt(p26['fixed_contrib'])} за 2026 год и "
                f"{rules.fmt(p27['fixed_contrib'])} за 2027 год (срок — до 28 декабря), плюс 1% с дохода свыше "
                "300 тыс. ₽ (срок — до 1 июля следующего года). На НПД и АУСН взносы за себя не платятся. "
                "На УСН «Доходы» и патенте налог уменьшается на взносы.")
    if re.search(r"\bндс\b", t):
        return ("С 2026 года упрощенцы платят НДС, если доход за год больше 20 млн ₽. Этот порог "
                "закреплён до 2029 года (228-ФЗ). Можно выбрать 5% без вычетов (доход до 272,5 млн ₽) "
                "или 22% с вычетами. На АУСН НДС нет при доходе до 60 млн ₽.")
    if re.search(r"самозанят|\bнпд\b", t):
        return ("ИП может работать как самозанятый (НПД): 4% с физлиц, 6% с компаний, без взносов и деклараций. "
                "Условия: доход до 2,4 млн ₽ в год, нет работников по трудовым договорам, нет перепродажи. "
                "Расскажите про свой бизнес — посчитаю, выгоднее ли это УСН.")
    if re.search(r"патент|\bпсн\b", t) and not re.search(r"стоит|стоимост", t):
        return ("Патент (ПСН) покупается на вид деятельности в конкретном регионе. Лимит дохода: 20 млн ₽ в 2026 году "
                "и 15 млн ₽ в 2027-м, до 15 работников. Стоимость — в калькуляторе patent.nalog.gov.ru. "
                "Напишите стоимость патента, например «патент стоит 60 тысяч», — сравню с УСН.")
    if re.search(r"госпошлин|сколько стоит регистрац", t):
        return "Если подавать документы электронно или через МФЦ, госпошлину платить не нужно. Регистрация занимает 3 рабочих дня."
    if re.search(r"отказ", t):
        return ("Частые причины отказа в регистрации ИП: неполный комплект документов, ошибки в Р21001 "
                "(ФИО не как в паспорте, нет кода ОКВЭД или он короче 4 знаков), действующий статус ИП, "
                "запрет суда, недавнее банкротство. Я могу проверить ваши данные — нажмите «Проверить заявление».")
    return None


GREETING = ("Здравствуйте! Я помогу зарегистрировать ИП без ошибок: подберу коды ОКВЭД, сравню налоговые режимы "
            "в рублях и составлю план подачи документов.\n\n"
            "Расскажите в двух словах: чем будете заниматься и сколько примерно планируете зарабатывать? "
            "Например: «Делаю маникюр на дому, около 120 тысяч в месяц, без сотрудников».")
