"""
배치 파이프라인 (Day1 오전 작업)
정책 원문 텍스트 -> Gemini 구조화 추출 -> Firestore benefits/{provider}_{brand} 저장

실행 전:
1) pip install -r requirements.txt
2) .env.example을 .env로 복사하고 GEMINI_API_KEY, GOOGLE_CLOUD_PROJECT 채우기
3) gcloud auth application-default login (로컬에서 Firestore 접근하려면 필요)
4) python seed_benefits.py
"""
import os
import json
import re
from dotenv import load_dotenv
from google import genai
from google.cloud import firestore

load_dotenv()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GCP_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]

client = genai.Client(api_key=GEMINI_API_KEY)
db = firestore.Client(project=GCP_PROJECT, database=os.environ.get("FIRESTORE_DATABASE", "(default)"))

# 문서 Section 8.1의 "핵심 프롬프트(정책 구조화)"를 그대로 사용
EXTRACTION_PROMPT = """역할: 당신은 한국 통신사/카드사 혜택 정책 문서를 구조화된 JSON으로 변환하는 어시스턴트입니다.
목적: 아래 정책 텍스트에서 provider, brand, discount_type(PERCENT|AMOUNT), discount_value, max_discount, eligible_tiers, conditions(monthly_limit, minimum_previous_spend, stackable), valid_from, valid_to를 추출하세요.
입력: {policy_text}
규칙: 텍스트에 명시되지 않은 필드는 null로 표기하고 추측하지 마세요. 반드시 JSON만 출력하고 다른 설명은 하지 마세요.
불확실성 처리: 조건이 모호하면 conditions.notes 필드에 원문을 그대로 남기세요."""

# 실제 공개 정책 페이지에서 가져온 원문 (문서 Section 7 데이터 출처와 동일)
# MVP 범위: 3~5개 provider x brand 조합으로 시작
POLICY_SOURCES = [
    {
        "provider": "SKT",
        "brand": "STARBUCKS",
        "source_url": "https://news.sktelecom.com/213810",
        "raw_text": (
            "SKT 고객들은 8월 1일부터 10일까지 스타벅스 톨사이즈 카페 아메리카노 음료 1잔을 "
            "무료로 받을 수 있다. T 멤버십 앱을 통해 무료 쿠폰을 다운로드 받을 수 있으며, "
            "사용 기한은 8월 1일부터 9월 30일까지다. SKT 고객들은 각 제휴사별 1회씩, "
            "한 달에 총 3회 멤버십 제휴 혜택을 이용할 수 있다."
        ),
    },
    {
        "provider": "SKT",
        "brand": "PARIS_BAGUETTE",
        "source_url": "https://news.sktelecom.com/213810",
        "raw_text": (
            "SKT는 8월 11일부터 20일까지 파리바게뜨 전 제품 50% 할인을 진행한다. "
            "정가 기준 20,000원 한도 내에서 최대 10,000원까지 할인되며, "
            "T 멤버십 앱 내 이벤트 페이지에서 쿠폰을 다운로드해 사용할 수 있다. "
            "제휴사별 월 1회, 총 월 3회까지 이용 가능하다."
        ),
    },
    {
        "provider": "SHINHAN",
        "brand": "STARBUCKS",
        "source_url": "https://www.card-gorilla.com (커피 할인카드 정리)",
        "raw_text": (
            "신한카드에 커피전문점, 베이커리 업종으로 등록된 모든 가맹점에 사용 가능하며 "
            "일 1회, 월 8회, 건당 최대 3천 원까지 할인 혜택을 받을 수 있다. "
            "즉 최대 월 2만 4천원까지 할인을 받을 수 있는 커피 할인 카드다. "
            "전월 실적에 따라 최대 혜택 한도가 달라진다."
        ),
    },
]


def extract_benefit(policy_text: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(policy_text=policy_text)
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )
    text = response.text.strip()
    # 혹시 코드블록으로 감싸서 나오면 벗겨내기
    text = re.sub(r"^```json\s*|\s*```$", "", text.strip())
    return json.loads(text)


def upsert_benefit(provider: str, brand: str, source_url: str, parsed: dict):
    doc_id = f"{provider}_{brand}"
    parsed["provider"] = provider
    parsed["brand"] = brand
    parsed["source_url"] = source_url
    db.collection("benefits").document(doc_id).set(parsed, merge=True)
    print(f"[OK] benefits/{doc_id} 저장 완료")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))


def main():
    for src in POLICY_SOURCES:
        print(f"\n--- {src['provider']} x {src['brand']} 구조화 중 ---")
        try:
            parsed = extract_benefit(src["raw_text"])
            upsert_benefit(src["provider"], src["brand"], src["source_url"], parsed)
        except json.JSONDecodeError:
            print(f"[FAIL] {src['provider']}_{src['brand']}: Gemini 응답이 유효한 JSON이 아님. 건너뜀.")
        except Exception as e:
            print(f"[FAIL] {src['provider']}_{src['brand']}: {e}")


if __name__ == "__main__":
    main()