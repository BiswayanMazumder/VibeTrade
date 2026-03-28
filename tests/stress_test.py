from locust import HttpUser, task, between

class VantedgeStressTest(HttpUser):
    # Short wait time to simulate "aggressive" users
    wait_time = between(0.1, 0.5)

    @task(5)
    def stress_api_stream(self):
        """Heavy task: Fetching live financial data."""
        self.client.get("/api/stream/TSLA?period=1d")

    @task(2)
    def stress_search(self):
        """Medium task: Searching the database/API."""
        self.client.get("/api/search/apple")

    @task(1)
    def stress_home(self):
        """Light task: Loading the static frontend."""
        self.client.get("/")