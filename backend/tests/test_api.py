"""
Backend API Tests for NMTS Application
Tests: Auth, Users, Profile, Brands/Groups, Products, Notifications
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://parts-tracker-80.preview.emergentagent.com')

# Test credentials
ADMIN_EMAIL = "admin@sleepingstock.in"
ADMIN_PASSWORD = "admin123"


class TestAuth:
    """Authentication endpoint tests"""
    
    def test_login_success(self):
        """Test successful login with admin credentials"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        
        data = response.json()
        assert "access_token" in data
        assert "user" in data
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["role"] == "master"
        assert "id" in data["user"]
        print(f"✓ Login successful for {ADMIN_EMAIL}")
    
    def test_login_invalid_credentials(self):
        """Test login with wrong credentials"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "wrong@example.com",
            "password": "wrongpass"
        })
        assert response.status_code == 401
        print("✓ Invalid credentials correctly rejected")
    
    def test_auth_me_with_token(self):
        """Test /auth/me endpoint with valid token"""
        # First login
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = login_res.json()["access_token"]
        
        # Get current user
        response = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == ADMIN_EMAIL
        print("✓ /auth/me returns correct user data")
    
    def test_auth_me_without_token(self):
        """Test /auth/me endpoint without token"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code in [401, 403]
        print("✓ /auth/me correctly rejects unauthenticated requests")


class TestUsers:
    """User management endpoint tests"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return response.json()["access_token"]
    
    def test_get_users_list(self, auth_token):
        """Test getting users list"""
        response = requests.get(
            f"{BASE_URL}/api/users",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1  # At least master admin exists
        
        # Check user structure
        user = data[0]
        assert "id" in user
        assert "username" in user
        assert "email" in user
        assert "role" in user
        assert "brand" in user
        assert "group" in user
        assert "location" in user
        assert "status" in user
        print(f"✓ Users list returned {len(data)} users with correct structure")
    
    def test_create_user(self, auth_token):
        """Test creating a new user"""
        test_user = {
            "username": "TEST_User_Create",
            "email": "TEST_create@example.com",
            "password": "testpass123",
            "role": "user",
            "phone": "+91 9876543210",
            "brand": "Test Brand",
            "group": "Test Group",
            "location": "Test Location"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/users",
            headers={"Authorization": f"Bearer {auth_token}"},
            json=test_user
        )
        
        # May fail if user already exists
        if response.status_code == 400 and "already exists" in response.text:
            print("✓ User creation test skipped (user already exists)")
            return
        
        assert response.status_code == 200, f"Create user failed: {response.text}"
        data = response.json()
        assert data["username"] == test_user["username"]
        assert data["email"] == test_user["email"]
        assert data["role"] == test_user["role"]
        print(f"✓ User created successfully: {data['username']}")
    
    def test_get_users_unauthorized(self):
        """Test getting users without auth"""
        response = requests.get(f"{BASE_URL}/api/users")
        assert response.status_code in [401, 403]
        print("✓ Users list correctly rejects unauthenticated requests")


class TestProfile:
    """Profile endpoint tests"""
    
    @pytest.fixture
    def auth_data(self):
        """Get authentication token and user data"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        data = response.json()
        return {
            "token": data["access_token"],
            "user_id": data["user"]["id"]
        }
    
    def test_get_profile(self, auth_data):
        """Test getting user profile"""
        response = requests.get(
            f"{BASE_URL}/api/profile/{auth_data['user_id']}",
            headers={"Authorization": f"Bearer {auth_data['token']}"}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify profile structure - should have all required fields
        assert "id" in data
        assert "username" in data
        assert "email" in data
        assert "role" in data
        assert "brand" in data
        assert "group" in data
        assert "location" in data
        assert "status" in data
        assert "last_login" in data
        assert "created_at" in data
        print(f"✓ Profile retrieved with all fields: {data['username']}")
    
    def test_get_activity_logs(self, auth_data):
        """Test getting activity logs"""
        response = requests.get(
            f"{BASE_URL}/api/profile/{auth_data['user_id']}/activity-logs",
            headers={"Authorization": f"Bearer {auth_data['token']}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✓ Activity logs retrieved: {len(data)} entries")
    
    def test_update_profile(self, auth_data):
        """Test updating profile"""
        update_data = {
            "phone": "+91 1234567890"
        }
        response = requests.put(
            f"{BASE_URL}/api/profile/{auth_data['user_id']}",
            headers={"Authorization": f"Bearer {auth_data['token']}"},
            json=update_data
        )
        assert response.status_code == 200
        data = response.json()
        assert data["phone"] == update_data["phone"]
        print("✓ Profile updated successfully")


class TestBrandsGroups:
    """Brand and Group management tests"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return response.json()["access_token"]
    
    def test_get_brands_groups(self, auth_token):
        """Test getting brands and groups list"""
        response = requests.get(
            f"{BASE_URL}/api/brands-groups",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "brands" in data
        assert "groups" in data
        assert isinstance(data["brands"], list)
        assert isinstance(data["groups"], list)
        print(f"✓ Brands/Groups retrieved: {len(data['brands'])} brands, {len(data['groups'])} groups")
    
    def test_create_brand(self, auth_token):
        """Test creating a brand"""
        response = requests.post(
            f"{BASE_URL}/api/brands",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"name": "TEST_Brand_API"}
        )
        
        if response.status_code == 400 and "already exists" in response.text:
            print("✓ Brand creation test skipped (brand already exists)")
            return
        
        assert response.status_code == 200, f"Create brand failed: {response.text}"
        print("✓ Brand created successfully")
    
    def test_create_group(self, auth_token):
        """Test creating a group"""
        response = requests.post(
            f"{BASE_URL}/api/groups",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"name": "TEST_Group_API"}
        )
        
        if response.status_code == 400 and "already exists" in response.text:
            print("✓ Group creation test skipped (group already exists)")
            return
        
        assert response.status_code == 200, f"Create group failed: {response.text}"
        print("✓ Group created successfully")


class TestNotifications:
    """Notification endpoint tests"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return response.json()["access_token"]
    
    def test_get_notifications(self, auth_token):
        """Test getting notifications"""
        response = requests.get(
            f"{BASE_URL}/api/notifications",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✓ Notifications retrieved: {len(data)} items")
    
    def test_get_unread_count(self, auth_token):
        """Test getting unread notification count"""
        response = requests.get(
            f"{BASE_URL}/api/notifications/unread-count",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert isinstance(data["count"], int)
        print(f"✓ Unread count: {data['count']}")


class TestProducts:
    """Product endpoint tests"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return response.json()["access_token"]
    
    def test_get_products(self, auth_token):
        """Test getting products list"""
        response = requests.get(
            f"{BASE_URL}/api/products",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✓ Products retrieved: {len(data)} items")
    
    def test_create_product(self, auth_token):
        """Test creating a product"""
        product_data = {
            "item_name": "TEST_Product_API",
            "part_number": "TEST_PN_001",
            "quantity": 10.0,
            "price": 99.99,
            "category": "Test Category"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/products",
            headers={"Authorization": f"Bearer {auth_token}"},
            json=product_data
        )
        
        if response.status_code == 400 and "already exists" in response.text:
            print("✓ Product creation test skipped (product already exists)")
            return
        
        assert response.status_code == 200, f"Create product failed: {response.text}"
        data = response.json()
        assert data["item_name"] == product_data["item_name"]
        assert data["part_number"] == product_data["part_number"]
        print(f"✓ Product created: {data['item_name']}")


class TestDashboard:
    """Dashboard metrics tests"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return response.json()["access_token"]
    
    def test_get_dashboard_metrics(self, auth_token):
        """Test getting dashboard metrics"""
        response = requests.get(
            f"{BASE_URL}/api/dashboard/metrics",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_products" in data
        assert "low_stock_count" in data
        assert "pending_orders" in data
        assert "sleeping_stock_count" in data
        print(f"✓ Dashboard metrics: {data}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
