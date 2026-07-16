"""
Integration tests for ChatTheme API endpoints.

Tests:
- Create theme (authenticated)
- List themes (with filtering)
- Get theme by ID
- Update theme (authorization checks)
- Delete theme (authorization checks)
- Theme CRUD operations with proper auth
"""

import uuid

import pytest
from sqlalchemy import text
from starlette import status


@pytest.fixture(autouse=True)
def cleanup_theme_data(session):
    """Clean up theme data before and after each test.

    Uses synchronous session since tests use synchronous client fixture.
    """
    session.rollback()

    try:
        session.execute(text("DELETE FROM chat_themes"))
        session.commit()
    except Exception:
        session.rollback()

    yield

    session.rollback()
    try:
        session.execute(text("DELETE FROM chat_themes"))
        session.commit()
    except Exception:
        session.rollback()


class TestThemeCRUD:
    """Test basic theme CRUD operations."""

    def test_create_theme_minimal(self, client, test_wrivetedadmin_account_headers):
        """Test creating a minimal theme."""
        theme_data = {
            "name": "Minimal Theme",
            "description": "A minimal theme for testing",
            "config": {
                "colors": {"primary": "#1890ff", "background": "#ffffff"},
                "bot": {"name": "Huey"},
            },
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "Minimal Theme"
        assert data["config"]["colors"]["primary"] == "#1890ff"
        assert data["is_active"] is True
        assert "id" in data

    def test_create_theme_full_config(self, client, test_wrivetedadmin_account_headers):
        """Test creating a theme with full configuration."""
        theme_data = {
            "name": "Complete Theme",
            "description": "A theme with all configuration options",
            "config": {
                "colors": {
                    "primary": "#1890ff",
                    "secondary": "#52c41a",
                    "background": "#ffffff",
                    "userBubble": "#e6f7ff",
                    "botBubble": "#f0f0f0",
                },
                "typography": {
                    "fontFamily": "system-ui, sans-serif",
                    "fontSize": {"small": "12px", "medium": "14px", "large": "16px"},
                },
                "bot": {
                    "name": "Huey",
                    "avatar": "https://example.com/avatar.png",
                    "typingIndicator": "dots",
                },
                "layout": {
                    "position": "bottom-right",
                    "width": 400,
                    "height": 600,
                },
            },
            "logo_url": "https://example.com/logo.png",
            "avatar_url": "https://example.com/avatar.png",
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "Complete Theme"
        assert data["logo_url"] == "https://example.com/logo.png"
        assert "typography" in data["config"]

    def test_get_theme_by_id(self, client, test_wrivetedadmin_account_headers):
        """Test retrieving a theme by ID."""
        theme_data = {
            "name": "Test Theme",
            "config": {
                "colors": {"primary": "#000000"},
                "bot": {"name": "Test"},
            },
        }

        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        response = client.get(
            f"/v1/cms/themes/{theme_id}", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == theme_id
        assert data["name"] == "Test Theme"

    def test_get_nonexistent_theme(self, client, test_wrivetedadmin_account_headers):
        """Test retrieving non-existent theme returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"/v1/cms/themes/{fake_id}", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_theme(self, client, test_wrivetedadmin_account_headers):
        """Test updating a theme."""
        theme_data = {
            "name": "Original Theme",
            "config": {
                "colors": {"primary": "#000000"},
                "bot": {"name": "Original"},
            },
        }

        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        update_data = {
            "name": "Updated Theme",
            "config": {
                "colors": {"primary": "#ff0000"},
                "bot": {"name": "Updated"},
            },
        }

        response = client.put(
            f"/v1/cms/themes/{theme_id}",
            json=update_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "Updated Theme"
        assert data["config"]["colors"]["primary"] == "#ff0000"

    def test_delete_theme(self, client, test_wrivetedadmin_account_headers):
        """Test deleting a theme."""
        theme_data = {
            "name": "Theme to Delete",
            "config": {
                "colors": {"primary": "#000000"},
                "bot": {"name": "Test"},
            },
        }

        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        response = client.delete(
            f"/v1/cms/themes/{theme_id}", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Soft delete means theme is still accessible but is_active=False
        get_response = client.get(
            f"/v1/cms/themes/{theme_id}", headers=test_wrivetedadmin_account_headers
        )
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["is_active"] is False


class TestThemeListing:
    """Test theme listing and filtering."""

    def test_list_themes_empty(self, client, test_wrivetedadmin_account_headers):
        """Test listing themes when none exist."""
        response = client.get(
            "/v1/cms/themes", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["pagination"]["total"] == 0
        assert len(data["data"]) == 0

    def test_list_themes_multiple(self, client, test_wrivetedadmin_account_headers):
        """Test listing multiple themes."""
        for i in range(3):
            theme_data = {
                "name": f"Theme {i}",
                "config": {
                    "colors": {"primary": f"#00000{i}"},
                    "bot": {"name": f"Bot {i}"},
                },
            }
            client.post(
                "/v1/cms/themes",
                json=theme_data,
                headers=test_wrivetedadmin_account_headers,
            )

        response = client.get(
            "/v1/cms/themes", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["pagination"]["total"] == 3
        assert len(data["data"]) == 3

    def test_list_themes_with_pagination(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test theme listing with pagination."""
        for i in range(5):
            theme_data = {
                "name": f"Theme {i}",
                "config": {
                    "colors": {"primary": "#000000"},
                    "bot": {"name": f"Bot {i}"},
                },
            }
            client.post(
                "/v1/cms/themes",
                json=theme_data,
                headers=test_wrivetedadmin_account_headers,
            )

        response = client.get(
            "/v1/cms/themes?limit=2&skip=0", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["data"]) == 2
        assert data["pagination"]["total"] == 5

    def test_list_themes_filter_by_active(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test filtering themes by active status."""
        theme_data = {
            "name": "Active Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
            "is_active": True,
        }
        client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        inactive_data = {
            "name": "Inactive Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
            "is_active": False,
        }
        client.post(
            "/v1/cms/themes",
            json=inactive_data,
            headers=test_wrivetedadmin_account_headers,
        )

        response = client.get(
            "/v1/cms/themes?active=true", headers=test_wrivetedadmin_account_headers
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert all(theme["is_active"] for theme in data["data"])


class TestThemeAuthentication:
    """Test theme API authentication and authorization."""

    def test_create_theme_requires_auth(self, client):
        """Test creating theme without authentication fails."""
        theme_data = {
            "name": "Unauthorized Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }

        response = client.post("/v1/cms/themes", json=theme_data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_theme_requires_auth(self, client):
        """Test getting theme without authentication fails."""
        response = client.get(f"/v1/cms/themes/{uuid.uuid4()}")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_theme_requires_auth(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test updating theme without authentication fails."""
        theme_data = {
            "name": "Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }
        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        update_data = {"name": "Updated Name"}
        response = client.put(f"/v1/cms/themes/{theme_id}", json=update_data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_theme_requires_auth(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test deleting theme without authentication fails."""
        theme_data = {
            "name": "Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }
        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        response = client.delete(f"/v1/cms/themes/{theme_id}")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThemeSchoolAssociation:
    """Test theme association with schools."""

    def test_create_theme_for_school(
        self, client, test_wrivetedadmin_account_headers, test_school
    ):
        """Test creating a theme associated with a school."""
        theme_data = {
            "name": "School Theme",
            "description": "Theme for specific school",
            "school_id": str(test_school.wriveted_identifier),
            "config": {
                "colors": {"primary": "#2E7D32", "secondary": "#FFA000"},
                "bot": {"name": "School Bot"},
            },
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["school_id"] == str(test_school.wriveted_identifier)

    def test_list_themes_filter_by_school(
        self, client, test_wrivetedadmin_account_headers, test_school
    ):
        """Test filtering themes by school."""
        school_theme = {
            "name": "School Theme",
            "school_id": str(test_school.wriveted_identifier),
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }
        client.post(
            "/v1/cms/themes",
            json=school_theme,
            headers=test_wrivetedadmin_account_headers,
        )

        global_theme = {
            "name": "Global Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }
        client.post(
            "/v1/cms/themes",
            json=global_theme,
            headers=test_wrivetedadmin_account_headers,
        )

        response = client.get(
            f"/v1/cms/themes?school_id={test_school.wriveted_identifier}",
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert all(
            theme["school_id"] == str(test_school.wriveted_identifier)
            for theme in data["data"]
            if theme.get("school_id")
        )


class TestThemeValidation:
    """Test theme configuration validation."""

    def test_create_theme_invalid_config(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test creating theme with invalid config structure."""
        # Config with invalid nested structure - colors.primary must be a hex string
        theme_data = {
            "name": "Invalid Theme",
            "config": {
                "colors": {"primary": "not-a-hex-color"},
                "bot": {"name": "Test"},
            },
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ]

    def test_create_theme_missing_required_fields(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test creating theme without required fields."""
        theme_data = {
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_update_theme_partial_config(
        self, client, test_wrivetedadmin_account_headers
    ):
        """Test partial update of theme config."""
        theme_data = {
            "name": "Theme",
            "config": {
                "colors": {"primary": "#000000", "secondary": "#ffffff"},
                "bot": {"name": "Original"},
            },
        }
        create_response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )
        theme_id = create_response.json()["id"]

        update_data = {
            "config": {"colors": {"primary": "#ff0000"}},
        }

        response = client.put(
            f"/v1/cms/themes/{theme_id}",
            json=update_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["config"]["colors"]["primary"] == "#ff0000"


class TestThemeDefaults:
    """Test theme default settings."""

    def test_create_theme_defaults(self, client, test_wrivetedadmin_account_headers):
        """Test that theme gets proper default values."""
        theme_data = {
            "name": "Theme with Defaults",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["is_active"] is True
        assert data["is_default"] is False
        assert data["version"] == "1.0"

    def test_set_theme_as_default(self, client, test_wrivetedadmin_account_headers):
        """Test marking a theme as default."""
        theme_data = {
            "name": "Default Theme",
            "config": {"colors": {"primary": "#000000"}, "bot": {"name": "Test"}},
            "is_default": True,
        }

        response = client.post(
            "/v1/cms/themes",
            json=theme_data,
            headers=test_wrivetedadmin_account_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["is_default"] is True
