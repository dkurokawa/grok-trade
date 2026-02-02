"""Production E2E tests - Real HTTP requests to production environment

These tests make actual HTTP requests to the production API.
Run with: pytest tests/test_production_e2e.py -v

Environment variables required:
- PRODUCTION_API_URL: The production API URL (e.g., https://your-app.railway.app)

Schedule: Avoid market open rush hours
- US Market opens: 9:30 AM ET = 14:30 UTC
- Avoid: 14:00-15:00 UTC (30 min before/after open)
- Best times: 16:00-20:00 UTC (mid-session) or 00:00-13:00 UTC (pre-market)
"""
import pytest
import os
import httpx
from datetime import datetime, timezone

# Production API URL from environment
PRODUCTION_URL = os.getenv("PRODUCTION_API_URL", "")

# Skip all tests if no production URL configured
pytestmark = pytest.mark.skipif(
    not PRODUCTION_URL,
    reason="PRODUCTION_API_URL not set"
)


def is_market_open_rush_hour() -> bool:
    """Check if current time is during market open rush (14:00-15:00 UTC)"""
    now = datetime.now(timezone.utc)
    # Market open rush: 14:00-15:00 UTC (9:00-10:00 AM ET)
    return 14 <= now.hour < 15


class TestProductionHealth:
    """Production health check tests"""

    @pytest.fixture
    def client(self):
        """HTTP client with timeout"""
        return httpx.Client(timeout=30.0)

    def test_health_endpoint_responds(self, client):
        """Test /health endpoint returns 200"""
        response = client.get(f"{PRODUCTION_URL}/health")
        assert response.status_code == 200, f"Health check failed: {response.text}"

    def test_health_returns_valid_json(self, client):
        """Test /health returns valid JSON structure"""
        response = client.get(f"{PRODUCTION_URL}/health")
        data = response.json()

        assert "status" in data, "Missing 'status' field"
        assert data["status"] == "ok", f"Unexpected status: {data['status']}"
        assert "timestamp" in data, "Missing 'timestamp' field"
        assert "scheduler_running" in data, "Missing 'scheduler_running' field"

    def test_health_response_time(self, client):
        """Test /health responds within acceptable time"""
        import time
        start = time.time()
        response = client.get(f"{PRODUCTION_URL}/health")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 5.0, f"Response too slow: {elapsed:.2f}s"


class TestProductionStatus:
    """Production status endpoint tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_status_endpoint_responds(self, client):
        """Test /status endpoint returns 200"""
        response = client.get(f"{PRODUCTION_URL}/status")
        assert response.status_code == 200, f"Status check failed: {response.text}"

    def test_status_returns_account_info(self, client):
        """Test /status returns account information"""
        response = client.get(f"{PRODUCTION_URL}/status")
        data = response.json()

        assert "account" in data, "Missing 'account' field"
        account = data["account"]
        assert "cash" in account, "Missing 'cash' in account"
        assert "portfolio_value" in account, "Missing 'portfolio_value' in account"

    def test_status_returns_positions(self, client):
        """Test /status returns positions array"""
        response = client.get(f"{PRODUCTION_URL}/status")
        data = response.json()

        assert "positions" in data, "Missing 'positions' field"
        assert isinstance(data["positions"], list), "Positions should be a list"

    def test_status_returns_scheduler_state(self, client):
        """Test /status returns scheduler state"""
        response = client.get(f"{PRODUCTION_URL}/status")
        data = response.json()

        assert "scheduler_running" in data, "Missing 'scheduler_running' field"
        assert isinstance(data["scheduler_running"], bool), "scheduler_running should be boolean"


class TestProductionDataEndpoints:
    """Production data endpoint tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_trades_endpoint_responds(self, client):
        """Test /trades endpoint returns 200"""
        response = client.get(f"{PRODUCTION_URL}/trades")
        assert response.status_code == 200

    def test_trades_returns_list(self, client):
        """Test /trades returns trades list"""
        response = client.get(f"{PRODUCTION_URL}/trades")
        data = response.json()

        assert "trades" in data, "Missing 'trades' field"
        assert isinstance(data["trades"], list), "Trades should be a list"

    def test_trades_with_limit(self, client):
        """Test /trades respects limit parameter"""
        response = client.get(f"{PRODUCTION_URL}/trades?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["trades"]) <= 5

    def test_decisions_endpoint_responds(self, client):
        """Test /decisions endpoint returns 200"""
        response = client.get(f"{PRODUCTION_URL}/decisions")
        assert response.status_code == 200

    def test_decisions_returns_list(self, client):
        """Test /decisions returns decisions list"""
        response = client.get(f"{PRODUCTION_URL}/decisions")
        data = response.json()

        assert "decisions" in data, "Missing 'decisions' field"
        assert isinstance(data["decisions"], list), "Decisions should be a list"


class TestProductionAvoidRushHour:
    """Tests that should avoid market open rush hour"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    @pytest.mark.skipif(
        is_market_open_rush_hour(),
        reason="Skipping during market open rush hour (14:00-15:00 UTC)"
    )
    def test_full_status_during_off_peak(self, client):
        """Full status check during off-peak hours"""
        response = client.get(f"{PRODUCTION_URL}/status")
        assert response.status_code == 200

        data = response.json()
        # Verify complete data structure
        assert "account" in data
        assert "positions" in data
        assert "scheduler_running" in data

        # Log current state for monitoring
        print(f"\n📊 Production Status:")
        print(f"  Cash: ${data['account'].get('cash', 0):,.2f}")
        print(f"  Portfolio: ${data['account'].get('portfolio_value', 0):,.2f}")
        print(f"  Positions: {len(data['positions'])}")
        print(f"  Scheduler: {'Running' if data['scheduler_running'] else 'Stopped'}")


class TestProductionErrorHandling:
    """Test production error handling"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_invalid_endpoint_returns_404(self, client):
        """Test invalid endpoint returns 404"""
        response = client.get(f"{PRODUCTION_URL}/invalid-endpoint-xyz")
        assert response.status_code == 404

    def test_invalid_limit_returns_422(self, client):
        """Test invalid query parameter returns 422"""
        response = client.get(f"{PRODUCTION_URL}/trades?limit=invalid")
        assert response.status_code == 422


class TestProductionSmokeTest:
    """Quick smoke test for all critical endpoints"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_all_critical_endpoints(self, client):
        """Smoke test all critical endpoints in one test"""
        endpoints = [
            ("/health", 200),
            ("/status", 200),
            ("/trades", 200),
            ("/decisions", 200),
        ]

        results = []
        for endpoint, expected_status in endpoints:
            try:
                response = client.get(f"{PRODUCTION_URL}{endpoint}")
                success = response.status_code == expected_status
                results.append((endpoint, success, response.status_code))
            except Exception as e:
                results.append((endpoint, False, str(e)))

        # Report results
        print("\n🔍 Smoke Test Results:")
        all_passed = True
        for endpoint, success, status in results:
            icon = "✅" if success else "❌"
            print(f"  {icon} {endpoint}: {status}")
            if not success:
                all_passed = False

        assert all_passed, "Some endpoints failed smoke test"


class TestProductionSchedulerControl:
    """Production scheduler control tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_scheduler_status_in_health(self, client):
        """Test scheduler status is reported in health endpoint"""
        response = client.get(f"{PRODUCTION_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert "scheduler_running" in data
        assert isinstance(data["scheduler_running"], bool)

    def test_scheduler_status_in_status(self, client):
        """Test scheduler status is reported in status endpoint"""
        response = client.get(f"{PRODUCTION_URL}/status")
        assert response.status_code == 200
        data = response.json()
        assert "scheduler_running" in data


class TestProductionAccountData:
    """Production account data validation tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_account_has_required_fields(self, client):
        """Test account data has all required fields"""
        response = client.get(f"{PRODUCTION_URL}/status")
        assert response.status_code == 200
        data = response.json()

        account = data.get("account", {})
        required_fields = ["cash", "portfolio_value", "buying_power", "equity", "daily_pnl"]

        for field in required_fields:
            assert field in account, f"Missing account field: {field}"

    def test_account_values_are_numeric(self, client):
        """Test account values are numeric types"""
        response = client.get(f"{PRODUCTION_URL}/status")
        data = response.json()

        account = data.get("account", {})
        for key, value in account.items():
            if key in ["cash", "portfolio_value", "buying_power", "equity", "daily_pnl"]:
                assert isinstance(value, (int, float)), f"{key} should be numeric"

    def test_positions_are_list(self, client):
        """Test positions is a list"""
        response = client.get(f"{PRODUCTION_URL}/status")
        data = response.json()

        assert "positions" in data
        assert isinstance(data["positions"], list)


class TestProductionTradeHistory:
    """Production trade history tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_trades_structure(self, client):
        """Test trades response structure"""
        response = client.get(f"{PRODUCTION_URL}/trades?limit=10")
        assert response.status_code == 200
        data = response.json()

        assert "trades" in data
        if len(data["trades"]) > 0:
            trade = data["trades"][0]
            expected_fields = ["id", "timestamp", "symbol", "action", "quantity", "price"]
            for field in expected_fields:
                assert field in trade, f"Missing trade field: {field}"

    def test_decisions_structure(self, client):
        """Test decisions response structure"""
        response = client.get(f"{PRODUCTION_URL}/decisions?limit=10")
        assert response.status_code == 200
        data = response.json()

        assert "decisions" in data
        if len(data["decisions"]) > 0:
            decision = data["decisions"][0]
            expected_fields = ["id", "timestamp", "parsed_action", "executed"]
            for field in expected_fields:
                assert field in decision, f"Missing decision field: {field}"

    def test_trades_limit_works(self, client):
        """Test trades limit parameter works correctly"""
        response1 = client.get(f"{PRODUCTION_URL}/trades?limit=1")
        response2 = client.get(f"{PRODUCTION_URL}/trades?limit=5")

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()

        assert len(data1["trades"]) <= 1
        assert len(data2["trades"]) <= 5


class TestProductionPerformance:
    """Production performance tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_health_response_time_under_1s(self, client):
        """Test health endpoint responds under 1 second"""
        import time
        start = time.time()
        response = client.get(f"{PRODUCTION_URL}/health")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 1.0, f"Health check too slow: {elapsed:.2f}s"

    def test_status_response_time_under_5s(self, client):
        """Test status endpoint responds under 5 seconds"""
        import time
        start = time.time()
        response = client.get(f"{PRODUCTION_URL}/status")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 5.0, f"Status check too slow: {elapsed:.2f}s"

    def test_trades_response_time_under_5s(self, client):
        """Test trades endpoint responds under 5 seconds"""
        import time
        start = time.time()
        response = client.get(f"{PRODUCTION_URL}/trades?limit=50")
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 5.0, f"Trades fetch too slow: {elapsed:.2f}s"


class TestProductionResilience:
    """Production resilience and stability tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_multiple_rapid_requests(self, client):
        """Test system handles multiple rapid requests"""
        results = []
        for _ in range(5):
            response = client.get(f"{PRODUCTION_URL}/health")
            results.append(response.status_code)

        assert all(status == 200 for status in results), "Some rapid requests failed"

    def test_large_limit_parameter(self, client):
        """Test system handles large limit parameter"""
        response = client.get(f"{PRODUCTION_URL}/trades?limit=1000")
        assert response.status_code == 200

    def test_concurrent_endpoints(self, client):
        """Test accessing different endpoints in sequence"""
        endpoints = ["/health", "/status", "/trades", "/decisions", "/health"]
        for endpoint in endpoints:
            response = client.get(f"{PRODUCTION_URL}{endpoint}")
            assert response.status_code == 200, f"Failed on {endpoint}"


class TestProductionDebugEndpoint:
    """Production debug endpoint tests"""

    @pytest.fixture
    def client(self):
        return httpx.Client(timeout=30.0)

    def test_debug_db_endpoint_exists(self, client):
        """Test debug/db endpoint exists and responds"""
        response = client.get(f"{PRODUCTION_URL}/debug/db")
        assert response.status_code == 200
        data = response.json()

        # Should have database status info
        assert "database_url_set" in data
        assert "engine_exists" in data

    def test_debug_db_shows_connected(self, client):
        """Test debug/db shows database is configured"""
        response = client.get(f"{PRODUCTION_URL}/debug/db")
        data = response.json()

        # In production, database should be configured
        assert data["database_url_set"] is True
        assert data["engine_exists"] is True
