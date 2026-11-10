"""Bounded, read-only question interpretation; no source facts or calculations.

This initial rule router selects allowed domain reads (ENG-H07), not arbitrary
tools or SQL. The caller must validate references and quantity assertions against
its authorized, versioned snapshot. A clarification/unsupported plan must not be
executed, even when some recognizable intents remain in the plan.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Literal
import unicodedata


QueryIntent = Literal["fulfillment", "production", "materials", "inventory", "delivery", "ontology"]
QuantityRole = Literal["target_good", "planned_input", "delivery", "unspecified"]
QuantityReference = Literal[
    "remaining_promise", "good_qty", "arrived_qty", "promised_qty",
    "planned_input", "production_remaining_qty",
]


@dataclass(frozen=True, slots=True)
class QuantityMention:
    quantity: Decimal
    unit: str
    role: QuantityRole = "unspecified"
    reference: QuantityReference | None = None


@dataclass(frozen=True, slots=True)
class QueryPlan:
    intents: tuple[QueryIntent, ...] = ()
    yield_rate: Decimal | None = None
    target_good_qty: int | None = None
    item_ids: tuple[str, ...] = ()
    target_references: tuple[str, ...] = ()
    clarification: str | None = None
    unsupported: bool = False
    is_what_if: bool = False
    explicit_production: bool = False
    quantity_mentions: tuple[QuantityMention, ...] = ()


_NUMBER = r"[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_PERCENT = re.compile(rf"(?<![a-z0-9_.+-])({_NUMBER})\s*(%|퍼센트|프로)", re.IGNORECASE)
_QUANTITY = re.compile(
    rf"(?<![a-z0-9_.])({_NUMBER})\s*(대|개|품목|행|kg|mg|g|ml|l|ea|pcs|pieces?)?",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"(?<![a-z0-9])(?:IM|ORD|JOB|CFG)\s*-\s*[a-z0-9]+(?:\s*-\s*[a-z0-9]+)*", re.IGNORECASE)
_DATES = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/]\d{1,2}(?!\d)|"
    r"(?:\d{4}\s*년\s*)?\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?|"
    r"\d+\s*(?:일|주|개월|시간|분)(?:\s*(?:안|내|후|뒤|전))?|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"
)


def _normalize(question: str) -> str:
    text = unicodedata.normalize("NFKC", question).casefold()
    text = text.translate(str.maketrans({"−": "-", "–": "-", "—": "-", "‑": "-"}))
    # Invisible spacing must not change a recognized identifier or deny pattern.
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return re.sub(r"\s+", " ", text).strip()


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _production_requested(compact: str) -> bool:
    return _has(compact, ("생산", "양품", "수율", "투입", "만들", "만드", "제작", "제조", "찍어", "몇개넣", "몇대넣"))


def _write_requested(compact: str, text: str) -> bool:
    # A read of an approval state is allowed; an approval instruction is not.
    return bool(re.search(
        r"(?:승인|반영|저장|등록|삭제|초기화|리셋|수정|변경|발주|발송|취소|차감|증가|감소)"
        r"(?:을|를)?(?:해|시켜|하(?:세요|십시오|자|라)|진행|처리)|"
        r"(?:생산|출하)(?:해(?:줘|주세|라)|하(?:세요|십시오|자|라))|"
        r"바꿔|보내(?:줘|주세|라)|만들어(?:줘|주세|라)|"
        r"(?:삭제|초기화|리셋|차감|발주)(?:해줘)?[.!?]*$",
        compact,
    )) or bool(re.search(r"\b(?:delete|drop|truncate|update|insert|approve|commit)\b", text))


def _intents(compact: str) -> tuple[QueryIntent, ...]:
    intents: set[QueryIntent] = set()
    material = _has(compact, ("bom", "소요", "부족", "모자", "모잘", "자재", "부품", "재료", "딸리", "딸려", "병목"))
    inventory = _has(compact, ("재고", "예약", "가용", "보류", "창고", "보유")) or bool(re.search(r"\blot\b", compact))
    production = _production_requested(compact)
    delivery_terms = _has(compact, ("납품", "도착", "출하", "이행", "약속", "배송", "고객", "보내", "보낼", "수주", "주문"))
    deadline = _has(compact, ("납기", "기한", "마감"))
    timing = _has(compact, ("언제", "일정", "내일", "모레", "이번주", "다음주", "주말", "월말", "며칠", "몇일", "몇시간", "몇분", "몇주", "몇달", "기간안에", "기간내", "완료날짜", "도착날짜", "출하날짜", "배송날짜", "생산날짜"))
    timing = timing or bool(re.search(
        r"(?:오늘|[일주월달])(?:안에|까지)|(?:이번|다음)(?:달|월|분기|해|년)|"
        r"\d+(?:일|주|개월|시간|분)(?:이면|안|내|후|뒤|만에)|"
        r"(?:하루|이틀|사흘)(?:면|이면|안|내|만에|뒤|후)", compact,
    ))
    if timing and (production or material or inventory or delivery_terms):
        deadline = True
    if production and _has(compact, ("걸려", "걸리", "소요시간")):
        deadline = True
    delivery_feasibility = delivery_terms and _has(compact, ("가능", "될까", "되나", "될지", "할수", "맞춰", "맞출", "지킬", "예상", "예측", "다보내", "다도착"))
    if deadline or delivery_feasibility:
        intents.add("delivery")
    if delivery_terms or deadline:
        intents.add("fulfillment")
    if production:
        intents.add("production")
    if material or "생산가능" in compact or "제조가능" in compact:
        intents.add("materials")
    if inventory:
        intents.add("inventory")
    if _has(compact, ("온톨로지", "클래스", "스키마", "t-box", "tbox", "관계정의", "타입정의")):
        intents.add("ontology")
    remaining = _has(compact, ("남았", "남은", "남아", "잔여", "현황"))
    if remaining and not (material or inventory or production):
        intents.update(("fulfillment", "production"))
    # A numerical comparison needs the ledger to establish what the numbers mean.
    if not intents and _has(compact, ("왜", "차이", "다른", "다르")) and len(re.findall(_NUMBER, compact)) >= 2:
        intents.update(("fulfillment", "production"))
    # A deadline cannot be answered by falling through to a materials-only branch.
    order: tuple[QueryIntent, ...] = ("delivery", "fulfillment", "production", "materials", "inventory", "ontology")
    return tuple(intent for intent in order if intent in intents)


def _yield(text: str, compact: str, intents: tuple[QueryIntent, ...]) -> tuple[Decimal | None, list[tuple[int, int]], str | None]:
    if re.search(
        rf"수율(?:적용|반영|보정|계산|영향)?(?:은|는|을|를|이|가)?"
        rf"(?:{_NUMBER}(?:%|퍼센트|프로)?(?:은|는|을|를|이|가|로|으로)?)?"
        r"(?:계산에서)?(?:없이|빼고|제외|미적용|무시|적용하지|반영하지|고려하지|적용안|반영안|안적용|안반영|안고려)",
        compact,
    ):
        return None, [], "수율 미반영 요청에는 기본 수율이나 100%를 자동 적용하지 않습니다. 계산에 사용할 수율을 명시해 주세요."
    percentages = list(_PERCENT.finditer(text))
    has_yield = "수율" in compact
    percentage_words = _has(compact, ("%", "퍼센트", "프로"))
    if percentage_words and not percentages:
        return None, [], "수율은 0보다 크고 100 이하인 백분율로 적어 주세요."
    if percentages:
        spans = [match.span() for match in percentages]
        rest = _blank_spans(text, spans)
        if len(percentages) != 1 or _has(rest, ("%", "퍼센트", "프로")):
            return None, spans, "계산에 적용할 수율 한 가지를 명시해 주세요."
        if not has_yield and (_has(compact, ("불량률", "할인율", "가동률", "실장률", "가능성", "확률")) or not _has(compact, ("투입", "몇개넣", "몇대넣"))):
            return None, spans, "이 백분율이 계획 수율인지 확인해 주세요. 다른 비율을 수율로 대신 적용하지 않습니다."
        percent = Decimal(percentages[0].group(1).replace(",", ""))
        sign, digits, exponent = percent.as_tuple()
        # Decimal division applies context precision; changing the exponent
        # preserves a supplied near-100% value exactly instead of rounding to 1.
        value = Decimal((sign, digits, exponent - 2))
        if not Decimal(0) < value <= Decimal(1):
            return None, spans, "수율은 0보다 크고 100% 이하여야 합니다."
        # A separate ratio and percentage in one question are ambiguous.
        if re.search(rf"수율\s*[이가은는을를]?\s*{_NUMBER}", rest):
            return None, spans, "계산에 적용할 수율 한 가지를 명시해 주세요."
        return value, spans, None
    if not has_yield:
        return None, [], None
    ratios = list(re.finditer(rf"수율\s*[이가은는을를]?\s*({_NUMBER})", text))
    if len(ratios) > 1:
        return None, [], "계산에 적용할 수율 한 가지를 명시해 주세요."
    if ratios:
        value = Decimal(ratios[0].group(1).replace(",", ""))
        if not Decimal(0) < value <= Decimal(1):
            return None, [ratios[0].span(1)], "수율에 %를 붙이거나 0보다 크고 1 이하인 비율을 적어 주세요."
        return value, [ratios[0].span(1)], None
    if re.search(r"수율[이가은는을를]?(?:nan|inf|infinity|무한|구십|백퍼)", compact):
        return None, [], "계산에 사용할 수율을 숫자와 %로 명시해 주세요."
    return None, [], None


def _target_match(before: str, after: str) -> bool:
    """Only an explicit hypothetical good-output target can override the ledger."""
    goal_before = bool(re.search(r"(?:양품|양품목표|생산목표|추가생산목표|목표양품)(?:은|는|을|를|이|가|수량)?$", before))
    goal_after = bool(re.match(r"(?:가|를|을|의|로|으로)?(?:양품)?(?:생산)?목표(?:면|라면|라|로|일때|라고|로가정)", after))
    make_after = bool(re.match(r"(?:를|을|의)?(?:더)?(?:생산하려면|생산하면|생산한다면|생산시|생산할때|만들려면|만들면|만든다면|만든다고|제작하려면)", after))
    hypothetical_after = bool(re.match(r"(?:이|가|를|을|로|으로)?(?:면|라면|라고가정|가정|목표면|목표라면|목표일때)", after))
    # Yield numbers are already excluded from the quantity text. An explicit
    # shared assumption can bind both the good-output target and that yield,
    # without promoting an ordinary delivery count into a production target.
    conjunction_assumption = bool(re.match(
        r"(?:와|과|및|이고)(?:계획)?수율(?:을|를|이|가|은|는|로|으로)?(?:함께)?가정", after,
    ))
    return make_after or goal_after or (goal_before and (hypothetical_after or conjunction_assumption))


def _quantity_reference(before: str, after: str, role: QuantityRole) -> QuantityReference | None:
    """Bind explicit fact claims to their field, not any coincidentally equal count."""
    if role == "target_good":
        return None
    if role == "planned_input":
        return "planned_input"
    before = re.split(r"[,.;!?]|인데|지만|그리고|반면", before)[-1]
    particles = r"(?:수량|량)?(?:이|가|은|는|을|를)?"
    # Remaining manufacturing output is distinct from the delivery obligation.
    production_before = bool(re.search(
        rf"(?:추가양품|추가생산|생산목표|양품목표|생산잔여|잔여생산|남은생산|남은양품){particles}$|"
        rf"(?:생산|양품)(?:은|는)?(?:남은|목표|잔여){particles}$", before,
    ))
    production_after = bool(re.match(
        r"(?:를|을|가|이|는|은)?(?:더)?(?:만들어야|만들필요|생산해야|생산필요|추가생산|생산목표|양품목표)|"
        r"(?:의)?양품(?:이|가)?필요", after,
    ))
    if production_before or production_after:
        return "production_remaining_qty"
    remaining_before = bool(re.search(rf"(?:남은|잔여|미이행|미도착)(?:고객|약속|납품|도착)?{particles}$", before))
    remaining_after = bool(re.match(r"(?:가|이|는|은|만)?(?:남았|남은|남아|잔여)", after))
    if remaining_before or remaining_after:
        if _has(before, ("생산", "양품")):
            return "production_remaining_qty"
        return "remaining_promise"
    future_arrival = bool(re.match(r"(?:가|이|는|은|를|을)?(?:언제|도착가능|도착예정|도착할|도착해야)", after))
    if not future_arrival and (re.search(rf"(?:고객도착|도착완료|기존도착|누적도착|도착){particles}$", before) or re.match(r"(?:가|이|는|은)?(?:고객에게)?도착(?:했|한|완료|맞)", after)):
        return "arrived_qty"
    if re.search(rf"(?:고객약속|납품약속|도착약속|총약속|약속총량|약속|주문량|총주문){particles}$", before) or re.match(r"(?:를|을|가|이|는|은)?(?:약속했|약속한|약속수량|총약속)", after):
        return "promised_qty"
    if re.search(rf"(?:생산완료양품|완료양품|누적양품|기생산양품|양품실적|만든양품|양품){particles}$", before) or re.match(r"(?:가|이|는|은)?(?:양품실적|양품생산완료|양품완료)", after):
        return "good_qty"
    return None


def interpret_question(question: str) -> QueryPlan:
    """Return a conservative plan, never facts, answers, dates or authorization."""
    text = _normalize(question)
    compact = text.replace(" ", "")
    intents = _intents(compact)
    ids = list(_IDENTIFIER.finditer(text))
    canonical_ids = tuple(dict.fromkeys(re.sub(r"\s+", "", match.group()).upper() for match in ids))
    item_ids = tuple(identifier for identifier in canonical_ids if identifier.startswith("IM-"))
    references = tuple(identifier for identifier in canonical_ids if not identifier.startswith("IM-"))
    clarification: str | None = None
    if _write_requested(compact, text):
        clarification = "이 질문 기능은 조회와 가정 계산만 지원합니다. 승인·반영·재고 변경 등 쓰기는 해당 검토 절차에서 진행해 주세요."
    elif _has(compact, ("과거", "어제", "지난", "작년", "이전회차", "다른회차", "다음회차", "과거버전", "이전버전")):
        clarification = "현재 조회 스냅샷 외의 과거 시점·다른 회차는 자동으로 선택하지 않습니다. 조회할 회차와 원장 버전을 명시해 주세요."
    elif re.search(r"\d+(?:번)?(?:회차|차회차)|(?<!\d)\d+차(?!원)", compact) or re.search(r"(?:원장|스냅샷|ledger)(?:버전|version|v)?\d+", compact):
        clarification = "질문 속 회차 번호나 버전만으로 조회 대상을 바꾸지 않습니다. 화면에서 정확한 회차·버전을 선택해 주세요."
    elif re.search(r"(?<![a-z0-9])(?:im|ord|job|cfg)\s*\d+", text) or _has(compact, ("다른제품", "다른모델", "다른구성", "wroom", "wrover")):
        clarification = "정확한 품목·주문·작업·구성 ID로 조회 대상을 명시해 주세요. 이름만으로 현재 대상과 같은 것으로 간주하지 않습니다."

    yield_rate, yield_spans, yield_error = _yield(text, compact, intents)
    clarification = clarification or yield_error
    excluded_spans = [match.span() for match in ids] + yield_spans + [match.span() for match in _DATES.finditer(text)]
    quantity_text = _blank_spans(text, excluded_spans)
    mentions: list[QuantityMention] = []
    targets: list[int] = []
    for match in _QUANTITY.finditer(quantity_text):
        try:
            quantity = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            clarification = clarification or "계산할 수량을 숫자와 단위로 명시해 주세요."
            continue
        unit = (match.group(2) or "").casefold()
        before = quantity_text[:match.start()].replace(" ", "")[-24:]
        after = quantity_text[match.end():].replace(" ", "")[:30]
        role: QuantityRole = "unspecified"
        if _target_match(before, after):
            role = "target_good"
            if unit not in ("대", "개", "ea", "pcs", "piece", "pieces"):
                clarification = clarification or "가정 생산 목표에는 양품 수량과 개·대 단위를 명시해 주세요."
            elif quantity < 0 or quantity != quantity.to_integral_value():
                clarification = clarification or "양품 생산 목표는 0 이상의 정수 수량이어야 합니다."
            else:
                targets.append(int(quantity))
        elif re.search(r"(?:투입|넣을|넣는)(?:량|수량|은|는|이|가)?$", before) or re.match(r"(?:를|을)?(?:투입|넣)", after):
            role = "planned_input"
        elif re.search(r"(?:납품|도착|출하|약속|배송)(?:량|수량|은|는|이|가)?$", before) or re.match(r"(?:를|을|가|이)?(?:언제|전량|모두|다보내|납품|도착|출하|보내|배송)", after):
            role = "delivery"
        elif unit in ("대", "개") and "delivery" in intents and "production" not in intents:
            role = "delivery"
        mentions.append(QuantityMention(quantity=quantity, unit=unit, role=role, reference=_quantity_reference(before, after, role)))

    if len(targets) > 1:
        clarification = clarification or "가정 계산에 적용할 양품 생산 목표 한 가지를 명시해 주세요."
    target = targets[0] if len(targets) == 1 else None
    if target is not None and "production" not in intents:
        intents = tuple(intent for intent in ("delivery", "fulfillment", "production", "materials", "inventory", "ontology") if intent == "production" or intent in intents)

    # These questions introduce other unapproved assumptions; never ignore them
    # while calculating against the current stock/BOM or a guessed target.
    if mentions and re.search(r"(?:재고|예약|보류|가용|대당|품목당|수량을).{0,16}\d.{0,12}(?:이면|라면|가정|바꾸면|늘리면|줄이면|으로|로)", compact):
        clarification = clarification or "재고·예약·보류·대당 수량을 임의로 바꾸는 계산은 지원하지 않습니다. 현재 원장 기준 또는 양품 목표·수율 가정만 지정해 주세요."
    if target is None and any(re.match(r"(?:이|가)?(?:면|라면)", quantity_text[match.end():].replace(" ", "")) for match in _QUANTITY.finditer(quantity_text)):
        clarification = clarification or "질문의 수량이 현재 원장 수량인지 가정 양품 생산 목표인지 명시해 주세요."
    if re.search(r"\d,\d{1,2}(?!\d)|\d\.\d+\.", quantity_text):
        clarification = clarification or "수량의 숫자 표기를 확인해 주세요."
    return QueryPlan(
        intents=intents,
        yield_rate=yield_rate,
        target_good_qty=target,
        item_ids=item_ids,
        target_references=references,
        clarification=clarification,
        unsupported=not bool(intents),
        is_what_if=yield_rate is not None or target is not None,
        explicit_production=_production_requested(compact),
        quantity_mentions=tuple(mentions),
    )
