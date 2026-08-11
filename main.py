"""
실시간 파이프라인
POST /api/match      : 매장 진입 시 적용 가능 혜택 + Gemini 추천 문구 반환 (사용 이력 반영)
POST /api/mark_used   : 혜택/쿠폰을 사용 완료로 표시
POST /api/onboarding  : 온보딩 체크리스트 저장
POST /api/coupons     : 쿠폰 이미지 등록
GET  /health          : 상태 확인

로컬 실행: uvicorn main:app --reload
"""
import os
import json
import re
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.cloud import firestore

load_dotenv()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GCP_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]

app = FastAPI(title="혜택레이더 매칭 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
db = firestore.Client(project=GCP_PROJECT, database=os.environ.get("FIRESTORE_DATABASE", "(default)"))
genai_client = genai.Client(api_key=GEMINI_API_KEY)

RECOMMEND_PROMPT = """역할: 당신은 계산된 혜택 목록을 사용자에게 친절하게 설명하는 어시스턴트입니다.
목적: 아래 JSON은 이미 계산이 끝난, 사용자가 지금 이 매장에서 아직 사용하지 않은 혜택 목록입니다. 이 중 할인액이 가장 큰 것을 추천하고, 왜 그것이 더 유리한지 한 문장으로 설명하세요.
입력: {data}
규칙: 목록에 없는 혜택이나 수치를 새로 만들어내지 마세요. 계산은 이미 끝난 상태이며, 당신의 역할은 요약·설명뿐입니다.
출력: 자연어 문장 1~2개만 출력하세요."""


class MatchRequest(BaseModel):
    user_id: str
    store_id: str
    trigger_type: str = "manual"  # "manual": 사용자가 앱을 켜고 직접 확인 / "auto": 위치 진입 자동 트리거


class MarkUsedRequest(BaseModel):
    user_id: str
    key: str          # benefit_key(provider_brand) 또는 coupon_id
    kind: str          # "benefit" | "coupon"


class OnboardingRequest(BaseModel):
    user_id: str
    carrier_provider: str
    carrier_tier: str = "일반"
    cards: list[str] = []
    memberships: list[str] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/stores")
def list_stores():
    """지도에 표시할 매장 목록 조회"""
    docs = db.collection("stores").stream()
    stores = []
    for d in docs:
        s = d.to_dict()
        s["store_id"] = d.id
        stores.append(s)
    return {"stores": stores}


@app.post("/api/onboarding")
def onboarding(req: OnboardingRequest):
    """F-01: 온보딩 체크리스트 등록 -> Firestore users/{user_id} 저장"""
    db.collection("users").document(req.user_id).set({
        "carrier": {"provider": req.carrier_provider, "tier": req.carrier_tier},
        "cards": req.cards,
        "memberships": req.memberships,
    })
    return {"status": "saved", "user_id": req.user_id}


COUPON_PARSE_PROMPT = """역할: 당신은 쿠폰·기프티콘 이미지에서 정보를 추출하는 어시스턴트입니다.
목적: 이미지에서 아래 필드를 추출하세요.
- brand: 매장명을 영문 대문자 코드로 (예: 스타벅스 로고나 텍스트가 보이면 -> STARBUCKS). 로고나 색상·디자인만으로도 브랜드를 알 수 있다면 유추해서 채우세요.
- discount_type: PERCENT|AMOUNT|FREE_ITEM 중 하나. 무료 음료/상품 교환권이면 FREE_ITEM.
- discount_value: 숫자만. FREE_ITEM이면 null.
- expires_at: YYYY-MM-DD. 이미지에 유효기간이 안 보이면 null.
규칙: brand는 로고/디자인으로 유추 가능하면 유추하되, discount_value나 expires_at처럼 구체적인 숫자·날짜는 이미지에 실제로 적혀 있을 때만 채우고 추측하지 마세요. 반드시 JSON만 출력하고 다른 설명은 하지 마세요."""


@app.post("/api/coupons")
async def register_coupon(user_id: str = Form(...), file: UploadFile = File(...)):
    """F-05: 쿠폰 사진 등록 -> Gemini 파싱 -> 메타데이터만 Firestore 저장 (원본 이미지 미보관)"""
    image_bytes = await file.read()

    mime_type = file.content_type
    if not mime_type or mime_type == "application/octet-stream":
        name = (file.filename or "").lower()
        mime_type = "image/png" if name.endswith(".png") else "image/jpeg"

    response = genai_client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            COUPON_PARSE_PROMPT,
        ],
    )
    text = re.sub(r"^```json\s*|\s*```$", "", response.text.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="쿠폰 이미지를 인식하지 못했습니다. 다시 촬영해주세요.")

    coupon_id = str(uuid.uuid4())[:8]
    db.collection("users").document(user_id).collection("coupons").document(coupon_id).set(parsed)
    return {"coupon_id": coupon_id, "parsed": parsed}


def get_eligible_benefits(user_id: str, brand: str):
    """benefits를 document ID로 직접 조회, benefit_key(=문서ID)를 함께 반환"""
    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="user not found")
    user = user_doc.to_dict()

    providers = [user["carrier"]["provider"]] + user.get("cards", []) + user.get("memberships", [])
    eligible = []
    for provider in providers:
        key = f"{provider}_{brand}"
        doc = db.collection("benefits").document(key).get()
        if not doc.exists:
            continue
        benefit = doc.to_dict()
        tiers = benefit.get("eligible_tiers")
        if tiers and provider == user["carrier"]["provider"]:
            if user["carrier"].get("tier") not in tiers:
                continue
        benefit["benefit_key"] = key
        eligible.append(benefit)
    return eligible


def get_owned_coupons(user_id: str, brand: str):
    coupons_ref = db.collection("users").document(user_id).collection("coupons")
    docs = coupons_ref.where("brand", "==", brand).stream()
    result = []
    for d in docs:
        c = d.to_dict()
        c["coupon_id"] = d.id
        result.append(c)
    return result


def is_used_this_month(user_id: str, key: str) -> bool:
    """혜택 사용 이력 체크 (MVP 단순화: 이번 달 안에 사용 기록이 있으면 '사용됨'으로 처리)"""
    doc = db.collection("users").document(user_id).collection("usage").document(key).get()
    if not doc.exists:
        return False
    used_at = doc.to_dict().get("used_at")
    if not used_at:
        return False
    now = datetime.now(timezone.utc)
    return used_at.year == now.year and used_at.month == now.month


def attach_usage_flags(user_id: str, eligible_benefits: list, owned_coupons: list):
    for b in eligible_benefits:
        b["used"] = is_used_this_month(user_id, b["benefit_key"])
    for c in owned_coupons:
        c["used"] = is_used_this_month(user_id, c["coupon_id"])


@app.post("/api/mark_used")
def mark_used(req: MarkUsedRequest):
    """혜택/쿠폰을 '사용 완료'로 표시. 사용자가 매장에서 실제로 혜택을 적용한 뒤 앱에서 누르는 액션."""
    db.collection("users").document(req.user_id).collection("usage").document(req.key).set({
        "used_at": datetime.now(timezone.utc),
        "kind": req.kind,
    })
    return {"status": "marked_used", "key": req.key}


def is_within_valid_period(valid_from, valid_to) -> bool:
    """유효기간(YYYY-MM-DD) 안에 오늘 날짜가 포함되는지 확인. 필드가 없으면 무기한으로 간주."""
    today = datetime.now(timezone.utc).date().isoformat()
    if valid_from and today < valid_from:
        return False
    if valid_to and today > valid_to:
        return False
    return True


def rule_engine(eligible_benefits: list, owned_coupons: list):
    """적용가능·중복가능·할인액 계산 + 사용 여부(used)·유효기간에 따라 후보군 분리 (코드 기반)"""
    candidates = []
    for b in eligible_benefits:
        if not is_within_valid_period(b.get("valid_from"), b.get("valid_to")):
            continue
        candidates.append({
            "source": "benefit",
            "key": b["benefit_key"],
            "provider": b.get("provider"),
            "discount_type": b.get("discount_type"),
            "discount_value": b.get("discount_value"),
            "used": b.get("used", False),
        })
    for c in owned_coupons:
        if not is_within_valid_period(None, c.get("expires_at")):
            continue
        candidates.append({
            "source": "coupon",
            "key": c["coupon_id"],
            "provider": c.get("provider", "OWNED_COUPON"),
            "discount_type": c.get("discount_type"),
            "discount_value": c.get("discount_value"),
            "used": c.get("used", False),
        })
    candidates.sort(key=lambda x: x.get("discount_value") or 0, reverse=True)
    return candidates


def generate_recommendation(not_used_candidates: list) -> str:
    """Gemini가 '아직 안 쓴' 혜택 중에서만 결과를 자연어로 요약 (RAG 아님)"""
    if not not_used_candidates:
        return "이미 이 매장의 혜택을 모두 사용하셨어요. 다음 기회에 다시 안내해드릴게요."
    prompt = RECOMMEND_PROMPT.format(data=not_used_candidates)
    response = genai_client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )
    return response.text.strip()


def was_notified_today(user_id: str, store_id: str) -> bool:
    """같은 매장에 대한 알림을 오늘 이미 보냈는지 확인 (혜택 사용 여부와는 별개로 매일 초기화)"""
    doc = db.collection("users").document(user_id).collection("notified").document(store_id).get()
    if not doc.exists:
        return False
    notified_date = doc.to_dict().get("date")
    return notified_date == datetime.now(timezone.utc).date().isoformat()


def mark_notified_today(user_id: str, store_id: str):
    db.collection("users").document(user_id).collection("notified").document(store_id).set({
        "date": datetime.now(timezone.utc).date().isoformat(),
    })


@app.post("/api/match")
def match(req: MatchRequest):
    store_doc = db.collection("stores").document(req.store_id).get()
    if not store_doc.exists:
        raise HTTPException(status_code=404, detail="store not found")
    brand = store_doc.to_dict()["brand"]

    eligible_benefits = get_eligible_benefits(req.user_id, brand)
    owned_coupons = get_owned_coupons(req.user_id, brand)
    attach_usage_flags(req.user_id, eligible_benefits, owned_coupons)

    candidates = rule_engine(eligible_benefits, owned_coupons)
    not_used = [c for c in candidates if not c["used"]]
    recommendation = generate_recommendation(not_used)

    # auto(자동 위치 트리거)일 때만 하루 1회 알림 제한을 적용. manual(수동 확인)은 항상 전체 정보 표시.
    should_notify = len(not_used) > 0
    if req.trigger_type == "auto":
        if was_notified_today(req.user_id, req.store_id):
            should_notify = False
        elif should_notify:
            mark_notified_today(req.user_id, req.store_id)

    return {
        "eligible_benefits": eligible_benefits,
        "owned_coupons": owned_coupons,
        "recommendation": recommendation,
        "should_notify": should_notify,
        "trigger_type": req.trigger_type,
    }
