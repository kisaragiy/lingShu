"""Load testing for LingShu Agent API.

Usage:
    pip install locust
    locust -f locustfile.py --host http://127.0.0.1:8788
"""
from locust import HttpUser, task, between
import json


class LingShuUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def health_check(self):
        self.client.get("/health")

    @task(2)
    def cs_demo_page(self):
        self.client.get("/cs-demo")

    @task(1)
    def main_page(self):
        self.client.get("/")

    @task(5)
    def cs_chat(self):
        self.client.post(
            "/v1/cs/chat",
            json={"message": "查一下我的订单"},
            headers={"Content-Type": "application/json"},
        )

    @task(2)
    def cs_chat_stream(self):
        self.client.post(
            "/v1/cs/chat/stream",
            json={"message": "推荐一款耳机"},
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    def knowledge_qa(self):
        self.client.get("/knowledge-qa")
