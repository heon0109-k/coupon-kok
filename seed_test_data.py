"""
테스트 사용자 1명 + 테스트 매장 데이터를 Firestore에 등록
seed_benefits.py 실행 후, 매칭 API(/api/match)를 테스트하기 전에 실행하세요.
"""
import os
from dotenv import load_dotenv
from google.cloud import firestore

load_dotenv()
db = firestore.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"], database=os.environ.get("FIRESTORE_DATABASE", "(default)"))

# 문서 3.1 페르소나(김민지)를 기반으로 한 테스트 사용자
db.collection("users").document("user_test_01").set({
    "carrier": {"provider": "SKT", "tier": "VIP"},
    "cards": ["SHINHAN"],
    "memberships": [],
})
print("[OK] users/user_test_01 저장")

# 테스트 매장: 강남역 스타벅스 (좌표는 예시)
db.collection("stores").document("store_test_starbucks_gangnam").set({
    "name": "스타벅스 강남역점",
    "brand": "STARBUCKS",
    "lat": 37.4979,
    "lng": 127.0276,
    "radius_m": 50,
})
print("[OK] stores/store_test_starbucks_gangnam 저장")

print("\n테스트 방법:")
print('curl -X POST http://localhost:8000/api/match -H "Content-Type: application/json" \\')
print('  -d \'{"user_id":"user_test_01","store_id":"store_test_starbucks_gangnam"}\'')