"""
지도에 마커 여러 개를 보여주기 위한 테스트 매장 추가
"""
import os
from dotenv import load_dotenv
from google.cloud import firestore

load_dotenv()
db = firestore.Client(
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)

stores = [
    {
        "id": "store_test_starbucks_gangnam",
        "name": "스타벅스 강남역점",
        "brand": "STARBUCKS",
        "lat": 37.4979, "lng": 127.0276, "radius_m": 50,
    },
    {
        "id": "store_test_starbucks_2",
        "name": "스타벅스 강남역2호점",
        "brand": "STARBUCKS",
        "lat": 37.4985, "lng": 127.0265, "radius_m": 50,
    },
    {
        "id": "store_test_paris_baguette",
        "name": "파리바게뜨 강남점",
        "brand": "PARIS_BAGUETTE",
        "lat": 37.4972, "lng": 127.0283, "radius_m": 50,
    },
]

for s in stores:
    doc_id = s.pop("id")
    db.collection("stores").document(doc_id).set(s)
    print(f"[OK] stores/{doc_id} 저장 완료")
