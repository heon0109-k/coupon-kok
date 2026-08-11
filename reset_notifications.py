"""
테스트 중 반복적으로 알림을 확인하고 싶을 때, '오늘 이미 알림 보냄' 기록을 초기화합니다.
"""
import os
from dotenv import load_dotenv
from google.cloud import firestore

load_dotenv()
db = firestore.Client(
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)

user_id = "user_test_01"
docs = db.collection("users").document(user_id).collection("notified").stream()
count = 0
for d in docs:
    d.reference.delete()
    count += 1

print(f"[OK] {count}개의 알림 기록을 초기화했습니다. 다시 테스트 가능합니다.")
