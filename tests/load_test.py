from locust import HttpUser, task, between, stats

class VantedgeLoadTester(HttpUser):
    # Simulates a user waiting 1-2 seconds between actions
    wait_time = between(1, 2)
    
    @task(3)
    def test_ticker_stream(self):
        """Simulates users fetching live data for different tickers."""
        tickers = ["AAPL", "TSLA", "BTC-USD", "NVDA", "MSFT"]
        for ticker in tickers:
            with self.client.get(f"/api/stream/{ticker}?period=1d", catch_response=True) as response:
                if response.status_code == 200:
                    response.success()
                elif response.status_code == 429:
                    response.failure("Rate Limited (429)")
                else:
                    response.failure(f"Failed with status: {response.status_code}")

    @task(1)
    def test_search_api(self):
        """Simulates users typing in the search bar."""
        query = "Apple"
        self.client.get(f"/api/search/{query}")

    @task(1)
    def test_context_api(self):
        """Simulates users loading the trending list."""
        self.client.get("/api/context")