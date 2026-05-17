"""
Unit tests for GitLab Client.

Tests the GitLabClient class which wraps python-gitlab library and handles
authentication, error conversion, and basic API operations.
"""

from unittest.mock import Mock, patch

import pytest
from gitlab import GitlabAuthenticationError, GitlabGetError, GitlabHttpError
from gitlab.exceptions import GitlabError

from gitlab_mcp.client.exceptions import (
    AuthenticationError,
    GitLabAPIError,
    GitLabServerError,
    NotFoundError,
    PermissionError,
    RateLimitError,
)
from gitlab_mcp.client.gitlab_client import GitLabClient
from gitlab_mcp.config.settings import GitLabConfig


class TestGitLabClientInitialization:
    """Test GitLabClient initialization and lazy connection."""

    def test_client_initialization(self):
        """Test that client initializes with a GitLabConfig."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )
        client = GitLabClient(config)

        assert client.config == config
        assert client._gitlab is None  # Lazy connection - not connected yet

    def test_lazy_connection(self):
        """Test that client doesn't connect to GitLab on __init__."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Should not make any API calls during __init__
        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab:
            _ = GitLabClient(config)
            mock_gitlab.assert_not_called()


class TestGitLabClientAuthentication:
    """Test authentication and connection to GitLab."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_authenticate_success(self, mock_gitlab_class):
        """Test successful authentication to GitLab."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock the Gitlab instance
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.auth.return_value = None
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client.authenticate()

        # Verify Gitlab was instantiated correctly
        mock_gitlab_class.assert_called_once_with(
            url="https://gitlab.example.com",
            private_token="test-token-123",
            timeout=30,  # Default timeout
        )

        # Verify auth was called
        mock_gitlab_instance.auth.assert_called_once()

        # Verify client is connected
        assert client._gitlab is not None

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_authenticate_invalid_token(self, mock_gitlab_class):
        """Test that invalid token raises AuthenticationError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="invalid-token",
        )

        # Mock authentication failure
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.auth.side_effect = GitlabAuthenticationError()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)

        with pytest.raises(AuthenticationError) as exc_info:
            client.authenticate()

        assert "authentication failed" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_authenticate_network_error(self, mock_gitlab_class):
        """Test that network errors during auth raise GitLabAPIError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock network error
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.auth.side_effect = ConnectionError("Network unreachable")
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)

        with pytest.raises(GitLabAPIError) as exc_info:
            client.authenticate()

        assert "network unreachable" in str(exc_info.value).lower()


class TestGitLabClientOperations:
    """Test basic GitLab API operations."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_current_user_success(self, mock_gitlab_class):
        """Test getting current authenticated user."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock the user object
        mock_user = Mock()
        mock_user.username = "testuser"
        mock_user.email = "testuser@example.com"
        mock_user.id = 42

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.user = mock_user
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()  # Skip actual auth
        client._gitlab = mock_gitlab_instance

        user = client.get_current_user()

        assert user.username == "testuser"
        assert user.email == "testuser@example.com"
        assert user.id == 42

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_version_success(self, mock_gitlab_class):
        """Test getting GitLab server version."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock version info
        mock_version = {"version": "16.5.0", "revision": "abc123"}

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.return_value = mock_version
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        version = client.get_version()

        assert version["version"] == "16.5.0"
        assert version["revision"] == "abc123"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_health_check_success(self, mock_gitlab_class):
        """Test health check returns True for healthy server."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.return_value = {"version": "16.5.0"}
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        is_healthy = client.health_check()

        assert is_healthy is True

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_health_check_failure(self, mock_gitlab_class):
        """Test health check returns False on error."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabError("Server error")
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        is_healthy = client.health_check()

        assert is_healthy is False


class TestGitLabClientErrorHandling:
    """Test conversion of python-gitlab errors to custom exceptions."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_handle_401_error(self, mock_gitlab_class):
        """Test that 401 errors are converted to AuthenticationError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Create a mock response with 401 status
        mock_response = Mock()
        mock_response.status_code = 401

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabAuthenticationError(response_code=401)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(AuthenticationError):
            client.get_version()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_handle_403_error(self, mock_gitlab_class):
        """Test that 403 errors are converted to PermissionError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabHttpError(
            response_code=403, response_body=b"Forbidden"
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(PermissionError):
            client.get_version()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_handle_404_error(self, mock_gitlab_class):
        """Test that 404 errors are converted to NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabGetError(
            response_code=404, response_body=b"Not Found"
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.get_version()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_handle_429_rate_limit(self, mock_gitlab_class):
        """Test that 429 errors are converted to RateLimitError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabHttpError(
            response_code=429, response_body=b"Rate limit exceeded"
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(RateLimitError):
            client.get_version()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_handle_500_server_error(self, mock_gitlab_class):
        """Test that 5xx errors are converted to GitLabServerError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.side_effect = GitlabHttpError(
            response_code=500, response_body=b"Internal Server Error"
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(GitLabServerError):
            client.get_version()


class TestGitLabClientInstanceInfo:
    """Test getting GitLab instance information."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_instance_info_success(self, mock_gitlab_class):
        """Test getting GitLab instance information."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock version info
        mock_version = {"version": "16.5.0", "revision": "abc123"}

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.version.return_value = mock_version
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        instance_info = client.get_instance_info()

        assert "url" in instance_info
        assert "version" in instance_info
        assert instance_info["url"] == "https://gitlab.example.com"
        assert instance_info["version"] == "16.5.0"


class TestGitLabClientListProjects:
    """Test listing projects."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_projects_success(self, mock_gitlab_class):
        """Test listing user's accessible projects."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project data
        mock_project1 = Mock()
        mock_project1.id = 1
        mock_project1.name = "Project One"
        mock_project1.path = "project-one"
        mock_project1.visibility = "private"
        mock_project1.web_url = "https://gitlab.example.com/group/project-one"
        mock_project1.description = "Test project 1"

        mock_project2 = Mock()
        mock_project2.id = 2
        mock_project2.name = "Project Two"
        mock_project2.path = "project-two"
        mock_project2.visibility = "public"
        mock_project2.web_url = "https://gitlab.example.com/group/project-two"
        mock_project2.description = "Test project 2"

        mock_projects_manager = Mock()
        mock_projects_manager.list.return_value = [mock_project1, mock_project2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_projects()

        assert "projects" in result
        assert "total" in result
        assert "page" in result
        assert "per_page" in result
        assert len(result["projects"]) == 2
        assert result["projects"][0]["name"] == "Project One"
        assert result["projects"][1]["name"] == "Project Two"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_projects_with_visibility_filter(self, mock_gitlab_class):
        """Test listing projects with visibility filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.id = 1
        mock_project.name = "Public Project"
        mock_project.visibility = "public"

        mock_projects_manager = Mock()
        mock_projects_manager.list.return_value = [mock_project]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_projects(visibility="public")

        mock_projects_manager.list.assert_called_once_with(visibility="public", page=1, per_page=20)
        assert len(result["projects"]) == 1
        assert result["projects"][0]["visibility"] == "public"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_projects_with_pagination(self, mock_gitlab_class):
        """Test listing projects with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_projects(page=2, per_page=10)

        mock_projects_manager.list.assert_called_once_with(visibility=None, page=2, per_page=10)
        assert result["page"] == 2
        assert result["per_page"] == 10


class TestGitLabClientEnsureAuthenticated:
    """Test the _ensure_authenticated helper method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_ensure_authenticated_when_not_connected(self, mock_gitlab_class):
        """Test _ensure_authenticated calls authenticate if not connected."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.auth.return_value = None
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)

        # Should not be authenticated initially
        assert client._gitlab is None

        # Call ensure_authenticated
        client._ensure_authenticated()

        # Should now be authenticated
        assert client._gitlab is not None
        mock_gitlab_instance.auth.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_ensure_authenticated_when_already_connected(self, mock_gitlab_class):
        """Test _ensure_authenticated skips auth if already connected."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.auth.return_value = None
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client.authenticate()  # Connect once

        # Reset the mock to check it's not called again
        mock_gitlab_instance.auth.reset_mock()

        # Call ensure_authenticated again
        client._ensure_authenticated()

        # Should NOT call auth again
        mock_gitlab_instance.auth.assert_not_called()


class TestGitLabClientGetProject:
    """Test getting project details."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_by_id_returns_details(self, mock_gitlab_class):
        """Test getting project by numeric ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project object
        mock_project = Mock()
        mock_project.asdict.return_value = {
            "id": 123,
            "name": "Test Project",
            "path": "test-project",
            "path_with_namespace": "group/test-project",
            "description": "A test project",
            "visibility": "private",
            "web_url": "https://gitlab.example.com/group/test-project",
            "default_branch": "main",
            "created_at": "2025-01-01T00:00:00Z",
            "last_activity_at": "2025-10-23T00:00:00Z",
            "star_count": 5,
            "forks_count": 2,
        }

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        project = client.get_project(123)

        mock_projects_manager.get.assert_called_once_with(123)
        assert project["id"] == 123
        assert project["name"] == "Test Project"
        assert project["path_with_namespace"] == "group/test-project"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_by_path_returns_details(self, mock_gitlab_class):
        """Test getting project by path (namespace/project)."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.asdict.return_value = {
            "id": 456,
            "name": "Another Project",
            "path_with_namespace": "mygroup/myproject",
        }

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        project = client.get_project("mygroup/myproject")

        mock_projects_manager.get.assert_called_once_with("mygroup/myproject")
        assert project["id"] == 456
        assert project["path_with_namespace"] == "mygroup/myproject"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_not_found(self, mock_gitlab_class):
        """Test that getting non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"Project not found"
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_project(999)

        assert "not found" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_permission_denied(self, mock_gitlab_class):
        """Test that permission denied raises PermissionError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabHttpError(
            response_code=403, response_body=b"Forbidden"
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(PermissionError) as exc_info:
            client.get_project(123)

        assert (
            "permission" in str(exc_info.value).lower()
            or "forbidden" in str(exc_info.value).lower()
        )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_auth_error(self, mock_gitlab_class):
        """Test that authentication error raises AuthenticationError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError(response_code=401)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(AuthenticationError):
            client.get_project(123)


class TestGitLabClientListBranches:
    """Test listing repository branches."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_returns_all_branches(self, mock_gitlab_class):
        """Test listing all branches in a repository."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock branch objects
        mock_branch1 = Mock()
        mock_branch1.name = "main"
        mock_branch1.commit = {"id": "abc123", "short_id": "abc123"}
        mock_branch1.protected = True
        mock_branch1.default = True
        mock_branch1.merged = False

        mock_branch2 = Mock()
        mock_branch2.name = "feature/new-feature"
        mock_branch2.commit = {"id": "def456", "short_id": "def456"}
        mock_branch2.protected = False
        mock_branch2.default = False
        mock_branch2.merged = False

        # Mock project and branches manager
        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.list.return_value = [mock_branch1, mock_branch2]
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branches = client.list_branches(123)

        mock_projects_manager.get.assert_called_once_with(123)
        mock_branches_manager.list.assert_called_once_with(search=None, page=1, per_page=20)
        assert len(branches) == 2
        assert branches[0].name == "main"
        assert branches[1].name == "feature/new-feature"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_with_search_filter(self, mock_gitlab_class):
        """Test listing branches with search filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "feature/search-me"
        mock_branch.commit = {"id": "abc123"}
        mock_branch.protected = False
        mock_branch.default = False

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.list.return_value = [mock_branch]
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branches = client.list_branches(123, search="feature")

        mock_branches_manager.list.assert_called_once_with(search="feature", page=1, per_page=20)
        assert len(branches) == 1
        assert "feature" in branches[0].name

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_pagination(self, mock_gitlab_class):
        """Test listing branches with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.list.return_value = []
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_branches(123, page=2, per_page=10)

        mock_branches_manager.list.assert_called_once_with(search=None, page=2, per_page=10)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_identifies_default_branch(self, mock_gitlab_class):
        """Test that default branch is correctly identified."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "main"
        mock_branch.commit = {"id": "abc123"}
        mock_branch.protected = True
        mock_branch.default = True
        mock_branch.merged = False

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.list.return_value = [mock_branch]
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branches = client.list_branches(123)

        assert len(branches) == 1
        assert branches[0].default is True
        assert branches[0].name == "main"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_empty_repository(self, mock_gitlab_class):
        """Test handling repositories with no branches."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.list.return_value = []
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branches = client.list_branches(123)

        assert branches == []
        assert len(branches) == 0

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_branches_not_found(self, mock_gitlab_class):
        """Test that 404 errors raise NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"Project not found"
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_branches(999)

        assert "not found" in str(exc_info.value).lower()


class TestGitLabClientGetBranch:
    """Test getting specific branch details."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_branch_returns_branch_details(self, mock_gitlab_class):
        """Test getting specific branch by name."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock branch object
        mock_branch = Mock()
        mock_branch.name = "main"
        mock_branch.commit = {
            "id": "abc123def456",
            "short_id": "abc123",
            "title": "Initial commit",
            "author_name": "Test Author",
            "author_email": "author@example.com",
            "created_at": "2025-01-01T00:00:00Z",
        }
        mock_branch.protected = True
        mock_branch.default = True
        mock_branch.merged = False
        mock_branch.can_push = True
        mock_branch.developers_can_push = False
        mock_branch.developers_can_merge = False

        # Mock project and branch manager
        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.get.return_value = mock_branch
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branch = client.get_branch(123, "main")

        mock_projects_manager.get.assert_called_once_with(123)
        mock_branches_manager.get.assert_called_once_with("main")
        assert branch.name == "main"
        assert branch.protected is True
        assert branch.default is True

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_branch_by_project_path(self, mock_gitlab_class):
        """Test getting branch using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "develop"
        mock_branch.commit = {"id": "xyz789"}
        mock_branch.protected = False
        mock_branch.default = False

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.get.return_value = mock_branch
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branch = client.get_branch("mygroup/myproject", "develop")

        mock_projects_manager.get.assert_called_once_with("mygroup/myproject")
        mock_branches_manager.get.assert_called_once_with("develop")
        assert branch.name == "develop"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_branch_not_found(self, mock_gitlab_class):
        """Test that getting non-existent branch raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"Branch not found"
        )
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_branch(123, "nonexistent-branch")

        assert "not found" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_branch_project_not_found(self, mock_gitlab_class):
        """Test that getting branch from non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"Project not found"
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_branch(999, "main")

        assert "not found" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_branch_includes_commit_details(self, mock_gitlab_class):
        """Test that get_branch returns full commit information."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "feature/test"
        mock_branch.commit = {
            "id": "full-sha-123456789",
            "short_id": "full-sha",
            "title": "Add new feature",
            "author_name": "Jane Doe",
            "author_email": "jane@example.com",
            "created_at": "2025-10-23T10:00:00Z",
            "message": "Add new feature\n\nDetailed description here",
        }
        mock_branch.protected = False
        mock_branch.default = False

        mock_project = Mock()
        mock_branches_manager = Mock()
        mock_branches_manager.get.return_value = mock_branch
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        branch = client.get_branch(123, "feature/test")

        assert branch.commit["id"] == "full-sha-123456789"
        assert branch.commit["title"] == "Add new feature"
        assert branch.commit["author_name"] == "Jane Doe"


class TestGitLabClientGetFileContent:
    """Test getting file contents from repository."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_from_default_branch(self, mock_gitlab_class):
        """Test getting file content from default branch."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock file object
        mock_file = Mock()
        mock_file.file_path = "README.md"
        mock_file.file_name = "README.md"
        mock_file.size = 1024
        mock_file.encoding = "base64"
        mock_file.content = "IyBSRUFETUUKClRoaXMgaXMgYSB0ZXN0IGZpbGUu"  # base64 of "# README\n\nThis is a test file."
        mock_file.content_sha256 = "abc123sha"
        mock_file.ref = "main"
        mock_file.blob_id = "blob123"
        mock_file.commit_id = "commit123"
        mock_file.last_commit_id = "commit123"

        # Mock project and repository files manager
        mock_project = Mock()
        mock_project.default_branch = "main"  # Set default branch
        mock_files_manager = Mock()
        mock_files_manager.get.return_value = mock_file
        mock_project.files = mock_files_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        file = client.get_file_content(123, "README.md")

        mock_projects_manager.get.assert_called_once_with(123)
        mock_files_manager.get.assert_called_once_with(file_path="README.md", ref="main")
        assert file.file_path == "README.md"
        assert file.size == 1024
        assert file.content == "IyBSRUFETUUKClRoaXMgaXMgYSB0ZXN0IGZpbGUu"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_from_specific_branch(self, mock_gitlab_class):
        """Test getting file content from a specific branch."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "src/main.py"
        mock_file.file_name = "main.py"
        mock_file.content = "cHJpbnQoImhlbGxvIik="  # base64 of "print("hello")"
        mock_file.ref = "develop"

        mock_project = Mock()
        mock_files_manager = Mock()
        mock_files_manager.get.return_value = mock_file
        mock_project.files = mock_files_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        file = client.get_file_content(123, "src/main.py", ref="develop")

        mock_files_manager.get.assert_called_once_with(file_path="src/main.py", ref="develop")
        assert file.file_path == "src/main.py"
        assert file.ref == "develop"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_from_commit_sha(self, mock_gitlab_class):
        """Test getting file content from specific commit SHA."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "config.json"
        mock_file.content = "eyJrZXkiOiAidmFsdWUifQ=="  # base64 of '{"key": "value"}'
        mock_file.ref = "abc123def456"

        mock_project = Mock()
        mock_files_manager = Mock()
        mock_files_manager.get.return_value = mock_file
        mock_project.files = mock_files_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        file = client.get_file_content(123, "config.json", ref="abc123def456")

        mock_files_manager.get.assert_called_once_with(file_path="config.json", ref="abc123def456")
        assert file.ref == "abc123def456"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_not_found(self, mock_gitlab_class):
        """Test that getting non-existent file raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_files_manager = Mock()
        mock_files_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"File not found"
        )
        mock_project.files = mock_files_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_file_content(123, "nonexistent.txt")

        assert "not found" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_project_not_found(self, mock_gitlab_class):
        """Test that getting file from non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(
            response_code=404, response_body=b"Project not found"
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_file_content(999, "README.md")

        assert "not found" in str(exc_info.value).lower()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_file_content_uses_project_default_branch(self, mock_gitlab_class):
        """Test that get_file_content uses project default branch when ref not specified."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "test.txt"
        mock_file.ref = "main"

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_files_manager = Mock()
        mock_files_manager.get.return_value = mock_file
        mock_project.files = mock_files_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_file_content(123, "test.txt")

        # Should use project's default branch
        mock_files_manager.get.assert_called_once_with(file_path="test.txt", ref="main")


class TestGitLabClientGetRepositoryTree:
    """Test GitLabClient.get_repository_tree() method."""

    def test_get_repository_tree_root_directory(self):
        """Test getting repository tree for root directory."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project and tree response
        mock_tree_data = [
            {
                "id": "1",
                "name": "README.md",
                "type": "blob",
                "path": "README.md",
                "mode": "100644",
            },
            {
                "id": "2",
                "name": "src",
                "type": "tree",
                "path": "src",
                "mode": "040000",
            },
        ]

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.repository_tree.return_value = mock_tree_data

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123)

            assert result == mock_tree_data
            mock_project.repository_tree.assert_called_once_with(
                path="", ref="main", recursive=False, page=1, per_page=20
            )

    def test_get_repository_tree_subdirectory(self):
        """Test getting repository tree for specific subdirectory."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tree_data = [
            {
                "id": "3",
                "name": "main.py",
                "type": "blob",
                "path": "src/main.py",
                "mode": "100644",
            }
        ]

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.repository_tree.return_value = mock_tree_data

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123, path="src")

            assert result == mock_tree_data
            mock_project.repository_tree.assert_called_once_with(
                path="src", ref="main", recursive=False, page=1, per_page=20
            )

    def test_get_repository_tree_with_ref(self):
        """Test getting repository tree at specific ref (branch/tag)."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tree_data = [{"id": "1", "name": "test.py", "type": "blob"}]

        mock_project = Mock()
        mock_project.repository_tree.return_value = mock_tree_data

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123, ref="develop")

            assert result == mock_tree_data
            # Should use specified ref, not default branch
            mock_project.repository_tree.assert_called_once_with(
                path="", ref="develop", recursive=False, page=1, per_page=20
            )

    def test_get_repository_tree_recursive(self):
        """Test getting recursive repository tree."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tree_data = [
            {"id": "1", "name": "README.md", "type": "blob", "path": "README.md"},
            {"id": "2", "name": "main.py", "type": "blob", "path": "src/main.py"},
            {"id": "3", "name": "test.py", "type": "blob", "path": "tests/test.py"},
        ]

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.repository_tree.return_value = mock_tree_data

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123, recursive=True)

            assert result == mock_tree_data
            mock_project.repository_tree.assert_called_once_with(
                path="", ref="main", recursive=True, page=1, per_page=20
            )

    def test_get_repository_tree_with_pagination(self):
        """Test getting repository tree with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tree_data = [{"id": str(i), "name": f"file{i}.py"} for i in range(50)]

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.repository_tree.return_value = mock_tree_data

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123, page=2, per_page=50)

            assert result == mock_tree_data
            mock_project.repository_tree.assert_called_once_with(
                path="", ref="main", recursive=False, page=2, per_page=50
            )

    def test_get_repository_tree_empty_directory(self):
        """Test getting repository tree for empty directory."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.repository_tree.return_value = []

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_repository_tree(123, path="empty")

            assert result == []

    def test_get_repository_tree_not_found(self):
        """Test getting repository tree with invalid path raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.default_branch = "main"
        # Simulate 404 error
        mock_project.repository_tree.side_effect = GitlabGetError("Not found", 404)

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_repository_tree(123, path="nonexistent")


class TestGitLabClientGetCommit:
    """Test GitLabClient.get_commit() method."""

    def test_get_commit_by_sha(self):
        """Test getting commit by SHA."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commit = Mock()
        mock_commit.id = "abc123def456"
        mock_commit.short_id = "abc123d"
        mock_commit.title = "Add new feature"
        mock_commit.message = "Add new feature\n\nDetailed description here"
        mock_commit.author_name = "John Doe"
        mock_commit.author_email = "john@example.com"
        mock_commit.authored_date = "2025-10-23T10:00:00Z"
        mock_commit.committer_name = "John Doe"
        mock_commit.committer_email = "john@example.com"
        mock_commit.committed_date = "2025-10-23T10:00:00Z"
        mock_commit.created_at = "2025-10-23T10:00:00Z"
        mock_commit.parent_ids = ["parent123"]
        mock_commit.web_url = "https://gitlab.example.com/project/commit/abc123"

        mock_commits_manager = Mock()
        mock_commits_manager.get.return_value = mock_commit

        mock_project = Mock()
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_commit(123, "abc123def456")

            assert result == mock_commit
            mock_commits_manager.get.assert_called_once_with("abc123def456")

    def test_get_commit_not_found(self):
        """Test getting commit with invalid SHA raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.get.side_effect = GitlabGetError("Not found", 404)

        mock_project = Mock()
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_commit(123, "invalidsha")

    def test_get_commit_by_project_path(self):
        """Test getting commit by project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commit = Mock()
        mock_commit.id = "abc123"

        mock_commits_manager = Mock()
        mock_commits_manager.get.return_value = mock_commit

        mock_project = Mock()
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.get_commit("group/project", "abc123")

            assert result == mock_commit
            mock_projects_manager.get.assert_called_once_with("group/project")


class TestGitLabClientListCommits:
    """Test GitLabClient.list_commits() method."""

    def test_list_commits_default_branch(self):
        """Test listing commits from default branch when no ref specified."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commit1 = Mock()
        mock_commit1.id = "abc123"
        mock_commit1.title = "First commit"

        mock_commit2 = Mock()
        mock_commit2.id = "def456"
        mock_commit2.title = "Second commit"

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = [mock_commit1, mock_commit2]

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.list_commits(123)

            assert len(result) == 2
            assert result[0] == mock_commit1
            assert result[1] == mock_commit2
            mock_commits_manager.list.assert_called_once_with(ref_name="main", page=1, per_page=20)

    def test_list_commits_specific_ref(self):
        """Test listing commits from a specific branch or tag."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commit = Mock()
        mock_commit.id = "abc123"

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = [mock_commit]

        mock_project = Mock()
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.list_commits(123, ref="feature-branch")

            assert len(result) == 1
            mock_commits_manager.list.assert_called_once_with(
                ref_name="feature-branch", page=1, per_page=20
            )

    def test_list_commits_with_pagination(self):
        """Test listing commits with custom pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = []

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_commits(123, page=3, per_page=50)

            mock_commits_manager.list.assert_called_once_with(ref_name="main", page=3, per_page=50)

    def test_list_commits_with_since_date(self):
        """Test listing commits with since date filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = []

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_commits(123, since="2024-01-01T00:00:00Z")

            mock_commits_manager.list.assert_called_once_with(
                ref_name="main",
                page=1,
                per_page=20,
                since="2024-01-01T00:00:00Z",
            )

    def test_list_commits_with_until_date(self):
        """Test listing commits with until date filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = []

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_commits(123, until="2024-12-31T23:59:59Z")

            mock_commits_manager.list.assert_called_once_with(
                ref_name="main",
                page=1,
                per_page=20,
                until="2024-12-31T23:59:59Z",
            )

    def test_list_commits_with_path_filter(self):
        """Test listing commits that affect a specific file path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = []

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_commits(123, path="src/main.py")

            mock_commits_manager.list.assert_called_once_with(
                ref_name="main", page=1, per_page=20, path="src/main.py"
            )

    def test_list_commits_empty_result(self):
        """Test listing commits returns empty list when no commits found."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_commits_manager = Mock()
        mock_commits_manager.list.return_value = []

        mock_project = Mock()
        mock_project.default_branch = "main"
        mock_project.commits = mock_commits_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.list_commits(123)

            assert result == []

    def test_list_commits_not_found(self):
        """Test listing commits raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.list_commits(999999)


class TestGitLabClientCompareBranches:
    """Test GitLabClient.compare_branches() method."""

    def test_compare_branches_basic(self):
        """Test comparing two branches returns comparison object."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock comparison object
        mock_comparison = Mock()
        mock_comparison.commits = []
        mock_comparison.diffs = []

        mock_project = Mock()
        mock_project.repository_compare.return_value = mock_comparison

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.compare_branches(123, "main", "develop")

            mock_projects_manager.get.assert_called_once_with(123)
            mock_project.repository_compare.assert_called_once_with(
                "main", "develop", straight=False
            )
            assert result == mock_comparison

    def test_compare_branches_with_straight(self):
        """Test comparing branches with straight=True parameter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_comparison = Mock()
        mock_project = Mock()
        mock_project.repository_compare.return_value = mock_comparison

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.compare_branches(123, "feature", "main", straight=True)

            mock_project.repository_compare.assert_called_once_with(
                "feature", "main", straight=True
            )
            assert result == mock_comparison

    def test_compare_branches_commits_and_diffs(self):
        """Test comparison includes commits and diffs."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock commits
        mock_commit1 = Mock()
        mock_commit1.id = "abc123"
        mock_commit1.title = "Test commit"

        # Mock comparison with commits and diffs
        mock_comparison = Mock()
        mock_comparison.commits = [mock_commit1]
        mock_comparison.diffs = [{"old_path": "file.py", "new_path": "file.py"}]

        mock_project = Mock()
        mock_project.repository_compare.return_value = mock_comparison

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.compare_branches(123, "main", "develop")

            assert len(result.commits) == 1
            assert len(result.diffs) == 1
            assert result.commits[0].id == "abc123"

    def test_compare_branches_by_project_path(self):
        """Test comparing branches using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_comparison = Mock()
        mock_project = Mock()
        mock_project.repository_compare.return_value = mock_comparison

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.compare_branches("group/project", "main", "develop")

            mock_projects_manager.get.assert_called_once_with("group/project")
            assert result == mock_comparison

    def test_compare_branches_same_refs(self):
        """Test comparing same ref to itself returns empty comparison."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Empty comparison when refs are the same
        mock_comparison = Mock()
        mock_comparison.commits = []
        mock_comparison.diffs = []

        mock_project = Mock()
        mock_project.repository_compare.return_value = mock_comparison

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.compare_branches(123, "main", "main")

            assert result == mock_comparison
            assert len(result.commits) == 0
            assert len(result.diffs) == 0

    def test_compare_branches_not_found(self):
        """Test comparing branches raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.compare_branches(999999, "main", "develop")

    def test_compare_branches_invalid_ref(self):
        """Test comparing branches raises NotFoundError for invalid ref."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.repository_compare.side_effect = GitlabGetError("Not found", 404)

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.compare_branches(123, "invalid-branch", "main")


class TestGitLabClientCreateBranch:
    """Test GitLabClient create_branch method."""

    def test_create_branch_from_ref(self):
        """Test creating a branch from a specific ref (branch/tag/commit)."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "feature-123"
        mock_branch.commit = {
            "id": "abc123def456",
            "short_id": "abc123d",
            "title": "Initial commit",
            "message": "Initial commit\n\nDetails here",
            "author_name": "John Doe",
            "created_at": "2024-01-01T00:00:00Z",
        }
        mock_branch.protected = False
        mock_branch.developers_can_push = True
        mock_branch.developers_can_merge = True
        mock_branch.can_push = True
        mock_branch.web_url = "https://gitlab.example.com/owner/repo/-/tree/feature-123"

        mock_branches_manager = Mock()
        mock_branches_manager.create.return_value = mock_branch

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.create_branch(123, "feature-123", "main")

            assert result.name == "feature-123"
            assert result.commit["id"] == "abc123def456"
            mock_branches_manager.create.assert_called_once_with(
                {"branch": "feature-123", "ref": "main"}
            )

    def test_create_branch_from_commit_sha(self):
        """Test creating a branch from a commit SHA."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "hotfix-456"
        mock_branch.commit = {"id": "xyz789abc123"}
        mock_branch.protected = False
        mock_branch.developers_can_push = True
        mock_branch.developers_can_merge = True
        mock_branch.can_push = True
        mock_branch.web_url = "https://gitlab.example.com/owner/repo/-/tree/hotfix-456"

        mock_branches_manager = Mock()
        mock_branches_manager.create.return_value = mock_branch

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.create_branch(123, "hotfix-456", "abc123def456")

            assert result.name == "hotfix-456"
            mock_branches_manager.create.assert_called_once_with(
                {"branch": "hotfix-456", "ref": "abc123def456"}
            )

    def test_create_branch_already_exists(self):
        """Test creating a branch that already exists raises ValidationError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.create.side_effect = GitlabHttpError("Branch already exists", 400)

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(GitLabAPIError):
                client.create_branch(123, "existing-branch", "main")

    def test_create_branch_invalid_ref(self):
        """Test creating a branch from invalid ref raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.create.side_effect = GitlabGetError("Invalid ref", 404)

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.create_branch(123, "new-branch", "invalid-ref")

    def test_create_branch_project_not_found(self):
        """Test creating a branch in non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.create_branch(999999, "new-branch", "main")

    def test_create_branch_by_project_path(self):
        """Test creating a branch using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branch = Mock()
        mock_branch.name = "feature-path"
        mock_branch.commit = {"id": "abc123"}
        mock_branch.protected = False
        mock_branch.developers_can_push = True
        mock_branch.developers_can_merge = True
        mock_branch.can_push = True
        mock_branch.web_url = "https://gitlab.example.com/owner/repo/-/tree/feature-path"

        mock_branches_manager = Mock()
        mock_branches_manager.create.return_value = mock_branch

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.create_branch("owner/repo", "feature-path", "main")

            assert result.name == "feature-path"
            mock_projects_manager.get.assert_called_once_with("owner/repo")

    def test_create_branch_requires_authentication(self):
        """Test creating a branch requires authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError("Authentication required")

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(AuthenticationError):
                client.create_branch(123, "new-branch", "main")


class TestGitLabClientDeleteBranch:
    """Test GitLabClient delete_branch method."""

    def test_delete_branch_success(self):
        """Test deleting a non-protected branch successfully."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.delete = Mock(return_value=None)

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            # Should not raise any exception
            client.delete_branch(123, "feature-branch")

            mock_branches_manager.delete.assert_called_once_with("feature-branch")

    def test_delete_branch_not_found(self):
        """Test deleting a non-existent branch raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.delete.side_effect = GitlabGetError("Branch not found", 404)

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.delete_branch(123, "non-existent-branch")

    def test_delete_branch_protected(self):
        """Test deleting a protected branch raises PermissionError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.delete.side_effect = GitlabHttpError(
            "Protected branch cannot be deleted", 403
        )

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(PermissionError):
                client.delete_branch(123, "protected-branch")

    def test_delete_branch_project_not_found(self):
        """Test deleting branch from non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Project not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.delete_branch(999999, "feature-branch")

    def test_delete_branch_by_project_path(self):
        """Test deleting a branch using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_branches_manager = Mock()
        mock_branches_manager.delete = Mock(return_value=None)

        mock_project = Mock()
        mock_project.branches = mock_branches_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.delete_branch("owner/repo", "feature-branch")

            mock_projects_manager.get.assert_called_once_with("owner/repo")
            mock_branches_manager.delete.assert_called_once_with("feature-branch")

    def test_delete_branch_requires_authentication(self):
        """Test deleting a branch requires authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError("Authentication required")

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(AuthenticationError):
                client.delete_branch(123, "feature-branch")


class TestGitLabClientListTags:
    """Test GitLabClient.list_tags() method."""

    def test_list_tags_returns_all_tags(self):
        """Test listing all tags from a project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock tag objects
        mock_tag1 = Mock()
        mock_tag1.name = "v1.0.0"
        mock_tag1.message = "Release 1.0.0"
        mock_tag1.target = "abc123"
        mock_tag1.commit = {"id": "abc123", "title": "Initial release"}

        mock_tag2 = Mock()
        mock_tag2.name = "v1.1.0"
        mock_tag2.message = "Release 1.1.0"
        mock_tag2.target = "def456"
        mock_tag2.commit = {"id": "def456", "title": "Bug fixes"}

        mock_tags_manager = Mock()
        mock_tags_manager.list.return_value = [mock_tag1, mock_tag2]

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tags = client.list_tags(123)

            assert len(tags) == 2
            assert tags[0].name == "v1.0.0"
            assert tags[1].name == "v1.1.0"
            mock_projects_manager.get.assert_called_once_with(123)
            mock_tags_manager.list.assert_called_once_with(page=1, per_page=20)

    def test_list_tags_with_search_filter(self):
        """Test listing tags with search filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.0.0"
        mock_tag.message = "Release 1.0.0"
        mock_tag.target = "abc123"
        mock_tag.commit = {"id": "abc123", "title": "Initial release"}

        mock_tags_manager = Mock()
        mock_tags_manager.list.return_value = [mock_tag]

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tags = client.list_tags(123, search="v1.0")

            assert len(tags) == 1
            assert tags[0].name == "v1.0.0"
            mock_tags_manager.list.assert_called_once_with(page=1, per_page=20, search="v1.0")

    def test_list_tags_pagination(self):
        """Test listing tags with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tags_manager = Mock()
        mock_tags_manager.list.return_value = []

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_tags(123, page=2, per_page=50)

            mock_tags_manager.list.assert_called_once_with(page=2, per_page=50)

    def test_list_tags_empty_repository(self):
        """Test listing tags returns empty list for repository with no tags."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tags_manager = Mock()
        mock_tags_manager.list.return_value = []

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tags = client.list_tags(123)

            assert tags == []
            mock_tags_manager.list.assert_called_once()

    def test_list_tags_not_found(self):
        """Test listing tags raises NotFoundError for non-existent project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Project not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.list_tags(999999)

    def test_list_tags_by_project_path(self):
        """Test listing tags using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.0.0"

        mock_tags_manager = Mock()
        mock_tags_manager.list.return_value = [mock_tag]

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tags = client.list_tags("owner/repo")

            assert len(tags) == 1
            mock_projects_manager.get.assert_called_once_with("owner/repo")


class TestGitLabClientGetTag:
    """Test GitLabClient.get_tag() method."""

    def test_get_tag_returns_tag_details(self):
        """Test getting a specific tag returns full details."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.0.0"
        mock_tag.message = "Release 1.0.0"
        mock_tag.target = "abc123"
        mock_tag.commit = {
            "id": "abc123def456",
            "short_id": "abc123d",
            "title": "Initial release",
            "message": "Initial release with core features",
            "author_name": "John Doe",
            "created_at": "2024-01-15T10:00:00Z",
        }
        mock_tag.protected = False

        mock_tags_manager = Mock()
        mock_tags_manager.get.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tag = client.get_tag(123, "v1.0.0")

            assert tag.name == "v1.0.0"
            assert tag.message == "Release 1.0.0"
            assert tag.commit["id"] == "abc123def456"
            mock_projects_manager.get.assert_called_once_with(123)
            mock_tags_manager.get.assert_called_once_with("v1.0.0")

    def test_get_tag_by_project_path(self):
        """Test getting a tag using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v2.0.0"

        mock_tags_manager = Mock()
        mock_tags_manager.get.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tag = client.get_tag("owner/repo", "v2.0.0")

            assert tag.name == "v2.0.0"
            mock_projects_manager.get.assert_called_once_with("owner/repo")

    def test_get_tag_not_found(self):
        """Test getting a non-existent tag raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tags_manager = Mock()
        mock_tags_manager.get.side_effect = GitlabGetError("Tag not found", 404)

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_tag(123, "non-existent-tag")

    def test_get_tag_project_not_found(self):
        """Test getting a tag from non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Project not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_tag(999999, "v1.0.0")

    def test_get_tag_includes_commit_details(self):
        """Test that get_tag returns tag with full commit information."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.5.0"
        mock_tag.message = "Feature release"
        mock_tag.target = "xyz789"
        mock_tag.commit = {
            "id": "xyz789abc123",
            "short_id": "xyz789a",
            "title": "Add new features",
            "message": "Add new features\n\nDetailed description here",
            "author_name": "Jane Doe",
            "author_email": "jane@example.com",
            "created_at": "2024-03-20T15:45:00Z",
        }
        mock_tag.protected = True

        mock_tags_manager = Mock()
        mock_tags_manager.get.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tag = client.get_tag(123, "v1.5.0")

            assert tag.commit["id"] == "xyz789abc123"
            assert tag.commit["title"] == "Add new features"
            assert tag.commit["author_name"] == "Jane Doe"
            assert tag.protected is True


class TestGitLabClientCreateTag:
    """Test GitLabClient.create_tag() method."""

    def test_create_tag_from_ref(self):
        """Test creating a tag from a reference."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.0.0"
        mock_tag.message = "Release 1.0.0"
        mock_tag.target = "abc123"
        mock_tag.commit = {
            "id": "abc123def456",
            "title": "Initial release",
        }

        mock_tags_manager = Mock()
        mock_tags_manager.create.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tag = client.create_tag(123, "v1.0.0", "main", "Release 1.0.0")

            assert tag.name == "v1.0.0"
            mock_projects_manager.get.assert_called_once_with(123)
            mock_tags_manager.create.assert_called_once_with(
                {"tag_name": "v1.0.0", "ref": "main", "message": "Release 1.0.0"}
            )

    def test_create_tag_with_message(self):
        """Test creating an annotated tag with a message."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v2.0.0"
        mock_tag.message = "Major release"

        mock_tags_manager = Mock()
        mock_tags_manager.create.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.create_tag(123, "v2.0.0", "develop", "Major release")

            mock_tags_manager.create.assert_called_once_with(
                {"tag_name": "v2.0.0", "ref": "develop", "message": "Major release"}
            )

    def test_create_tag_already_exists(self):
        """Test creating a tag that already exists raises error."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tags_manager = Mock()
        mock_tags_manager.create.side_effect = GitlabHttpError("Tag already exists", 400)

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(GitLabAPIError):
                client.create_tag(123, "v1.0.0", "main")

    def test_create_tag_invalid_ref(self):
        """Test creating a tag from invalid ref raises error."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tags_manager = Mock()
        mock_tags_manager.create.side_effect = GitlabGetError("Ref not found", 404)

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.create_tag(123, "v1.0.0", "non-existent-ref")

    def test_create_tag_not_found(self):
        """Test creating a tag in non-existent project raises NotFoundError."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Project not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.create_tag(999999, "v1.0.0", "main")

    def test_create_tag_by_project_path(self):
        """Test creating a tag using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_tag = Mock()
        mock_tag.name = "v1.0.0"

        mock_tags_manager = Mock()
        mock_tags_manager.create.return_value = mock_tag

        mock_project = Mock()
        mock_project.tags = mock_tags_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            tag = client.create_tag("owner/repo", "v1.0.0", "main")

            assert tag.name == "v1.0.0"
            mock_projects_manager.get.assert_called_once_with("owner/repo")


class TestGitLabClientSearchCode:
    """Test code search functionality."""

    def test_search_code_global_search_returns_results(self):
        """Test global code search returns results."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock search results
        mock_result1 = {
            "basename": "README",
            "data": "def search_code():\n    pass",
            "path": "README.md",
            "filename": "README.md",
            "ref": "main",
            "startline": 10,
            "project_id": 1,
        }
        mock_result2 = {
            "basename": "utils",
            "data": "def search_code():\n    return True",
            "path": "src/utils.py",
            "filename": "src/utils.py",
            "ref": "main",
            "startline": 25,
            "project_id": 2,
        }

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.search.return_value = [mock_result1, mock_result2]
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            results = client.search_code("search_code")

            assert len(results) == 2
            assert results[0]["path"] == "README.md"
            assert results[0]["startline"] == 10
            assert results[1]["path"] == "src/utils.py"
            mock_gitlab_instance.search.assert_called_once()

    def test_search_code_with_project_id_searches_project_scope(self):
        """Test code search within a specific project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_result = {
            "basename": "main",
            "data": "if __name__ == '__main__':",
            "path": "src/main.py",
            "filename": "src/main.py",
            "ref": "main",
            "startline": 1,
            "project_id": 123,
        }

        mock_project = Mock()
        mock_project.search.return_value = [mock_result]

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            results = client.search_code("__main__", project_id=123)

            assert len(results) == 1
            assert results[0]["path"] == "src/main.py"
            mock_projects_manager.get.assert_called_once_with(123)
            mock_project.search.assert_called_once()

    def test_search_code_with_pagination(self):
        """Test code search with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_results = [
            {
                "basename": f"file{i}",
                "data": "test data",
                "path": f"test{i}.py",
                "filename": f"test{i}.py",
                "ref": "main",
                "startline": i,
                "project_id": 1,
            }
            for i in range(5)
        ]

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.search.return_value = mock_results
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            results = client.search_code("test", page=2, per_page=5)

            assert len(results) == 5
            # Verify search was called with pagination parameters
            call_args = mock_gitlab_instance.search.call_args
            assert call_args[1]["page"] == 2
            assert call_args[1]["per_page"] == 5

    def test_search_code_no_results(self):
        """Test code search with no results returns empty list."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.search.return_value = []
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            results = client.search_code("nonexistent_code_xyz123")

            assert results == []
            mock_gitlab_instance.search.assert_called_once()

    def test_search_code_requires_authentication(self):
        """Test that search_code requires authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        # Mock _ensure_authenticated to raise AuthenticationError
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.search_code("test")

    def test_search_code_handles_api_error(self):
        """Test that search_code handles GitLab API errors."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.search.side_effect = GitlabHttpError("API Error", 500)
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(GitLabServerError):
                client.search_code("test")

    def test_search_code_project_not_found(self):
        """Test that search_code raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.search_code("test", project_id=999999)


class TestGitLabClientListIssues:
    """Test GitLabClient.list_issues() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_returns_issues(self, mock_gitlab_class):
        """Test that list_issues returns a list of issues."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock issue objects
        mock_issue1 = Mock()
        mock_issue1.iid = 1
        mock_issue1.title = "First issue"
        mock_issue1.state = "opened"
        mock_issue1.labels = ["bug"]

        mock_issue2 = Mock()
        mock_issue2.iid = 2
        mock_issue2.title = "Second issue"
        mock_issue2.state = "opened"
        mock_issue2.labels = ["feature"]

        # Mock project and issues manager
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = [mock_issue1, mock_issue2]
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            issues = client.list_issues(project_id=123)

            assert len(issues) == 2
            assert issues[0].iid == 1
            assert issues[0].title == "First issue"
            assert issues[1].iid == 2
            assert issues[1].title == "Second issue"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_with_state_filter(self, mock_gitlab_class):
        """Test listing issues filtered by state."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.iid = 1
        mock_issue.state = "closed"

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = [mock_issue]
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            _ = client.list_issues(project_id=123, state="closed")

            # Verify state filter was passed
            mock_issues_manager.list.assert_called_once()
            call_kwargs = mock_issues_manager.list.call_args[1]
            assert call_kwargs.get("state") == "closed"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_with_labels_filter(self, mock_gitlab_class):
        """Test listing issues filtered by labels."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.labels = ["bug", "critical"]

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = [mock_issue]
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            _ = client.list_issues(project_id=123, labels=["bug", "critical"])

            # Verify labels filter was passed
            call_kwargs = mock_issues_manager.list.call_args[1]
            assert call_kwargs.get("labels") == ["bug", "critical"]

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_with_milestone_filter(self, mock_gitlab_class):
        """Test listing issues filtered by milestone."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.milestone = Mock(title="v1.0")

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = [mock_issue]
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            _ = client.list_issues(project_id=123, milestone="v1.0")

            # Verify milestone filter was passed
            call_kwargs = mock_issues_manager.list.call_args[1]
            assert call_kwargs.get("milestone") == "v1.0"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_with_pagination(self, mock_gitlab_class):
        """Test listing issues with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = []
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.list_issues(project_id=123, page=2, per_page=50)

            # Verify pagination parameters
            call_kwargs = mock_issues_manager.list.call_args[1]
            assert call_kwargs.get("page") == 2
            assert call_kwargs.get("per_page") == 50

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_empty_results(self, mock_gitlab_class):
        """Test that list_issues handles empty results."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = []
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            issues = client.list_issues(project_id=123)

            assert issues == []

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_by_project_path(self, mock_gitlab_class):
        """Test listing issues using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.iid = 1

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = [mock_issue]
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            _ = client.list_issues(project_id="mygroup/myproject")

            # Verify project was fetched by path
            mock_projects_manager.get.assert_called_once_with("mygroup/myproject")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_requires_authentication(self, mock_gitlab_class):
        """Test that list_issues triggers authentication if not authenticated."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)

        # Mock _ensure_authenticated to track if it's called
        client._ensure_authenticated = Mock()
        client._gitlab = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.list.return_value = []
        mock_project.issues = mock_issues_manager
        client._gitlab.projects.get.return_value = mock_project

        client.list_issues(project_id=123)

        # Verify authentication was checked
        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issues_project_not_found(self, mock_gitlab_class):
        """Test that list_issues raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.list_issues(project_id=999999)


class TestGitLabClientGetIssue:
    """Test GitLabClient.get_issue() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_issue_returns_issue(self, mock_gitlab_class):
        """Test that get_issue returns a specific issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock issue object
        mock_issue = Mock()
        mock_issue.iid = 42
        mock_issue.title = "Test issue"
        mock_issue.description = "This is a test issue"
        mock_issue.state = "opened"
        mock_issue.labels = ["bug", "critical"]
        mock_issue.author = Mock(username="testuser")

        # Mock project and issues manager
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            issue = client.get_issue(project_id=123, issue_iid=42)

            assert issue.iid == 42
            assert issue.title == "Test issue"
            assert issue.state == "opened"
            mock_issues_manager.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_issue_by_project_path(self, mock_gitlab_class):
        """Test getting issue using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.iid = 1

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            _ = client.get_issue(project_id="mygroup/myproject", issue_iid=1)

            # Verify project was fetched by path
            mock_projects_manager.get.assert_called_once_with("mygroup/myproject")
            mock_issues_manager.get.assert_called_once_with(1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_issue_requires_authentication(self, mock_gitlab_class):
        """Test that get_issue triggers authentication if not authenticated."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)

        # Mock _ensure_authenticated to track if it's called
        client._ensure_authenticated = Mock()
        client._gitlab = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = Mock()
        mock_project.issues = mock_issues_manager
        client._gitlab.projects.get.return_value = mock_project

        client.get_issue(project_id=123, issue_iid=42)

        # Verify authentication was checked
        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_issue_project_not_found(self, mock_gitlab_class):
        """Test that get_issue raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_issue(project_id=999999, issue_iid=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_issue_not_found(self, mock_gitlab_class):
        """Test that get_issue raises NotFoundError for invalid issue IID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.side_effect = GitlabGetError("Not found", 404)
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError):
                client.get_issue(project_id=123, issue_iid=999999)


class TestGitLabClientCreateIssue:
    """Test GitLabClient.create_issue() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_returns_issue(self, mock_gitlab_class):
        """Test that create_issue returns created issue object."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock created issue
        mock_issue = Mock()
        mock_issue.iid = 1
        mock_issue.title = "Test Issue"
        mock_issue.description = "Test description"
        mock_issue.state = "opened"

        # Mock project and issues manager
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Create issue with minimal fields
        result = client.create_issue(project_id=123, title="Test Issue")

        # Verify issue created
        assert result == mock_issue
        mock_issues_manager.create.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_with_all_fields(self, mock_gitlab_class):
        """Test create_issue with all optional fields."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.iid = 1

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Create issue with all fields
        client.create_issue(
            project_id=123,
            title="Test Issue",
            description="Test description",
            labels=["bug", "frontend"],
            assignee_ids=[10, 20],
            milestone_id=5,
        )

        # Verify correct parameters passed
        mock_issues_manager.create.assert_called_once()
        call_args = mock_issues_manager.create.call_args[0][0]
        assert call_args["title"] == "Test Issue"
        assert call_args["description"] == "Test description"
        assert call_args["labels"] == ["bug", "frontend"]
        assert call_args["assignee_ids"] == [10, 20]
        assert call_args["milestone_id"] == 5

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_with_labels(self, mock_gitlab_class):
        """Test create_issue with labels."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_issue(project_id=123, title="Test", labels=["bug", "critical"])

        call_args = mock_issues_manager.create.call_args[0][0]
        assert call_args["labels"] == ["bug", "critical"]

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_with_assignees(self, mock_gitlab_class):
        """Test create_issue with assignees."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_issue(project_id=123, title="Test", assignee_ids=[1, 2, 3])

        call_args = mock_issues_manager.create.call_args[0][0]
        assert call_args["assignee_ids"] == [1, 2, 3]

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_with_milestone(self, mock_gitlab_class):
        """Test create_issue with milestone."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_issue(project_id=123, title="Test", milestone_id=7)

        call_args = mock_issues_manager.create.call_args[0][0]
        assert call_args["milestone_id"] == 7

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_by_project_path(self, mock_gitlab_class):
        """Test create_issue using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_issue(project_id="group/project", title="Test Issue")

        # Verify project fetched with path
        mock_projects_manager.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_requires_authentication(self, mock_gitlab_class):
        """Test that create_issue checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.create.return_value = Mock()
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_issue(project_id=123, title="Test")

        # Verify authentication was checked
        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_issue_project_not_found(self, mock_gitlab_class):
        """Test that create_issue raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.create_issue(project_id=999999, title="Test")


class TestGitLabClientUpdateIssue:
    """Test GitLabClient.update_issue() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_title(self, mock_gitlab_class):
        """Test updating issue title."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock issue
        mock_issue = Mock()
        mock_issue.iid = 1
        mock_issue.title = "Updated Title"
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_issue(project_id=123, issue_iid=1, title="Updated Title")

        # Verify issue fetched and saved
        mock_issues_manager.get.assert_called_once_with(1)
        assert mock_issue.title == "Updated Title"
        mock_issue.save.assert_called_once()
        assert result == mock_issue

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_description(self, mock_gitlab_class):
        """Test updating issue description."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_issue(project_id=123, issue_iid=1, description="New description")

        assert mock_issue.description == "New description"
        mock_issue.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_labels(self, mock_gitlab_class):
        """Test updating issue labels."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_issue(project_id=123, issue_iid=1, labels=["bug", "urgent"])

        assert mock_issue.labels == ["bug", "urgent"]
        mock_issue.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_multiple_fields(self, mock_gitlab_class):
        """Test updating multiple issue fields at once."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_issue(
            project_id=123,
            issue_iid=1,
            title="New Title",
            description="New desc",
            labels=["test"],
            assignee_ids=[10],
        )

        assert mock_issue.title == "New Title"
        assert mock_issue.description == "New desc"
        assert mock_issue.labels == ["test"]
        assert mock_issue.assignee_ids == [10]
        mock_issue.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_requires_authentication(self, mock_gitlab_class):
        """Test that update_issue checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_issue(project_id=123, issue_iid=1, title="Test")

        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_issue_not_found(self, mock_gitlab_class):
        """Test that update_issue raises NotFoundError for invalid issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.side_effect = GitlabGetError("Not found", 404)
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.update_issue(project_id=123, issue_iid=999999, title="Test")


class TestGitLabClientCloseIssue:
    """Test GitLabClient.close_issue() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_issue_sets_state_event(self, mock_gitlab_class):
        """Test that close_issue sets state_event to 'close'."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.close_issue(project_id=123, issue_iid=1)

        # Verify state_event set to 'close'
        assert mock_issue.state_event == "close"
        mock_issue.save.assert_called_once()
        assert result == mock_issue

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_issue_by_project_path(self, mock_gitlab_class):
        """Test close_issue using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.close_issue(project_id="group/project", issue_iid=1)

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_issue_requires_authentication(self, mock_gitlab_class):
        """Test that close_issue checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.close_issue(project_id=123, issue_iid=1)

        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_issue_not_found(self, mock_gitlab_class):
        """Test that close_issue raises NotFoundError for invalid issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.side_effect = GitlabGetError("Not found", 404)
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.close_issue(project_id=123, issue_iid=999999)


class TestGitLabClientReopenIssue:
    """Test GitLabClient.reopen_issue() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_issue_sets_state_event(self, mock_gitlab_class):
        """Test that reopen_issue sets state_event to 'reopen'."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.return_value = mock_issue
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.reopen_issue(project_id=123, issue_iid=1)

        # Verify state_event set to 'reopen'
        assert mock_issue.state_event == "reopen"
        mock_issue.save.assert_called_once()
        assert result == mock_issue

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_issue_by_project_path(self, mock_gitlab_class):
        """Test reopen_issue using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.reopen_issue(project_id="group/project", issue_iid=1)

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_issue_requires_authentication(self, mock_gitlab_class):
        """Test that reopen_issue checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_issue = Mock()
        mock_issue.save = Mock()

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.reopen_issue(project_id=123, issue_iid=1)

        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_issue_not_found(self, mock_gitlab_class):
        """Test that reopen_issue raises NotFoundError for invalid issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_issues_manager = Mock()
        mock_issues_manager.get.side_effect = GitlabGetError("Not found", 404)
        mock_project.issues = mock_issues_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.reopen_issue(project_id=123, issue_iid=999999)


class TestGitLabClientAddIssueComment:
    """Test GitLabClient.add_issue_comment() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_issue_comment_returns_note(self, mock_gitlab_class):
        """Test that add_issue_comment creates and returns a note."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note = Mock()
        mock_note.id = 100
        mock_note.body = "Test comment"

        mock_notes_manager = Mock()
        mock_notes_manager.create.return_value = mock_note

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.add_issue_comment(project_id=123, issue_iid=1, body="Test comment")

        # Verify note created with correct body
        mock_notes_manager.create.assert_called_once_with({"body": "Test comment"})
        assert result == mock_note

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_issue_comment_by_project_path(self, mock_gitlab_class):
        """Test add_issue_comment using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note = Mock()
        mock_notes_manager = Mock()
        mock_notes_manager.create.return_value = mock_note

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.add_issue_comment(project_id="group/project", issue_iid=1, body="Comment")

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_issue_comment_requires_authentication(self, mock_gitlab_class):
        """Test that add_issue_comment checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note = Mock()
        mock_notes_manager = Mock()
        mock_notes_manager.create.return_value = mock_note

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.add_issue_comment(project_id=123, issue_iid=1, body="Comment")

        client._ensure_authenticated.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_issue_comment_issue_not_found(self, mock_gitlab_class):
        """Test that add_issue_comment raises NotFoundError for invalid issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.issues.get.side_effect = GitlabGetError("Not found", 404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.add_issue_comment(project_id=123, issue_iid=999999, body="Comment")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_issue_comment_empty_body_raises_error(self, mock_gitlab_class):
        """Test that add_issue_comment validates empty body."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError, match="Comment body cannot be empty"):
            client.add_issue_comment(project_id=123, issue_iid=1, body="")


class TestGitLabClientListIssueComments:
    """Test GitLabClient.list_issue_comments() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issue_comments_returns_notes(self, mock_gitlab_class):
        """Test that list_issue_comments returns list of notes."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note1 = Mock()
        mock_note1.id = 100
        mock_note1.body = "First comment"

        mock_note2 = Mock()
        mock_note2.id = 101
        mock_note2.body = "Second comment"

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = [mock_note1, mock_note2]

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_issue_comments(project_id=123, issue_iid=1)

        # Verify notes listed with default pagination
        mock_notes_manager.list.assert_called_once_with(page=1, per_page=20)
        assert len(result) == 2
        assert result[0] == mock_note1
        assert result[1] == mock_note2

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issue_comments_empty(self, mock_gitlab_class):
        """Test list_issue_comments when issue has no comments."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = []

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_issue_comments(project_id=123, issue_iid=1)

        assert result == []

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issue_comments_pagination(self, mock_gitlab_class):
        """Test list_issue_comments with custom pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = []

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_issue_comments(project_id=123, issue_iid=1, page=2, per_page=50)

        # Verify custom pagination used
        mock_notes_manager.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issue_comments_by_project_path(self, mock_gitlab_class):
        """Test list_issue_comments using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = []

        mock_issue = Mock()
        mock_issue.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.issues.get.return_value = mock_issue

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_issue_comments(project_id="group/project", issue_iid=1)

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_issue_comments_issue_not_found(self, mock_gitlab_class):
        """Test that list_issue_comments raises NotFoundError for invalid issue."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.issues.get.side_effect = GitlabGetError("Not found", 404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.list_issue_comments(project_id=123, issue_iid=999999)

    # =============================================================================
    # Repository Files - create_file
    # =============================================================================

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_returns_file(self, mock_gitlab_class):
        """Test that create_file creates a file and returns file object."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "new_file.txt"
        mock_file.content = "SGVsbG8gV29ybGQ="  # "Hello World" in base64
        mock_file.commit_id = "abc123"

        mock_project = Mock()
        mock_project.id = 123
        mock_project.files.create.return_value = mock_file

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_file(
            project_id=123,
            file_path="new_file.txt",
            branch="main",
            content="Hello World",
            commit_message="Add new file",
        )

        assert result.file_path == "new_file.txt"
        assert result.content == "SGVsbG8gV29ybGQ="
        assert result.commit_id == "abc123"
        mock_project.files.create.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_with_all_params(self, mock_gitlab_class):
        """Test that create_file handles all optional parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "docs/README.md"

        mock_project = Mock()
        mock_project.files.create.return_value = mock_file

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_file(
            project_id=123,
            file_path="docs/README.md",
            branch="develop",
            content="# Documentation",
            commit_message="Add documentation",
            author_email="test@example.com",
            author_name="Test User",
            encoding="text",
        )

        assert result.file_path == "docs/README.md"
        create_args = mock_project.files.create.call_args[0][0]
        assert create_args["file_path"] == "docs/README.md"
        assert create_args["branch"] == "develop"
        assert create_args["content"] == "# Documentation"
        assert create_args["commit_message"] == "Add documentation"
        assert create_args["author_email"] == "test@example.com"
        assert create_args["author_name"] == "Test User"
        assert create_args["encoding"] == "text"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_by_project_path(self, mock_gitlab_class):
        """Test that create_file works with project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "test.txt"

        mock_project = Mock()
        mock_project.files.create.return_value = mock_file

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_file(
            project_id="group/project",
            file_path="test.txt",
            branch="main",
            content="test content",
            commit_message="Add test file",
        )

        assert result.file_path == "test.txt"
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_requires_authentication(self, mock_gitlab_class):
        """Test that create_file requires authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        mock_ensure_auth = Mock(side_effect=AuthenticationError("Not authenticated"))
        client._ensure_authenticated = mock_ensure_auth

        with pytest.raises(AuthenticationError):
            client.create_file(
                project_id=123,
                file_path="test.txt",
                branch="main",
                content="test",
                commit_message="Test",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_project_not_found(self, mock_gitlab_class):
        """Test that create_file raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError("Not found", 404)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.create_file(
                project_id=999,
                file_path="test.txt",
                branch="main",
                content="test",
                commit_message="Test",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_file_validates_required_params(self, mock_gitlab_class):
        """Test that create_file validates required parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Test empty file_path
        with pytest.raises(ValueError, match="file_path.*required"):
            client.create_file(
                project_id=123,
                file_path="",
                branch="main",
                content="test",
                commit_message="Test",
            )

        # Test empty branch
        with pytest.raises(ValueError, match="branch.*required"):
            client.create_file(
                project_id=123,
                file_path="test.txt",
                branch="",
                content="test",
                commit_message="Test",
            )

        # Test empty commit_message
        with pytest.raises(ValueError, match="commit_message.*required"):
            client.create_file(
                project_id=123,
                file_path="test.txt",
                branch="main",
                content="test",
                commit_message="",
            )

    # =============================================================================
    # Repository Files - update_file
    # =============================================================================

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_file_modifies_content(self, mock_gitlab_class):
        """Test that update_file updates file content."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "README.md"
        mock_file.content = "T2xkIGNvbnRlbnQ="  # Initial content

        mock_project = Mock()
        mock_project.files.get.return_value = mock_file
        mock_file.save.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_file(
            project_id=123,
            file_path="README.md",
            branch="main",
            content="Updated content",
            commit_message="Update README",
        )

        assert result.file_path == "README.md"
        # Content is updated to the new value
        assert result.content == "Updated content"
        mock_project.files.get.assert_called_once_with(file_path="README.md", ref="main")
        mock_file.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_file_with_all_params(self, mock_gitlab_class):
        """Test that update_file handles all optional parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "docs/guide.md"
        mock_file.content = "New content"

        mock_project = Mock()
        mock_project.files.get.return_value = mock_file
        mock_file.save.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_file(
            project_id=123,
            file_path="docs/guide.md",
            branch="develop",
            content="New content",
            commit_message="Update guide",
            author_email="test@example.com",
            author_name="Test User",
            encoding="text",
        )

        assert result.file_path == "docs/guide.md"
        save_args = mock_file.save.call_args[1]
        assert save_args["branch"] == "develop"
        assert save_args["commit_message"] == "Update guide"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_file_by_project_path(self, mock_gitlab_class):
        """Test that update_file works with project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_file = Mock()
        mock_file.file_path = "config.yml"

        mock_project = Mock()
        mock_project.files.get.return_value = mock_file
        mock_file.save.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_file(
            project_id="group/project",
            file_path="config.yml",
            branch="main",
            content="updated: true",
            commit_message="Update config",
        )

        assert result.file_path == "config.yml"
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_file_not_found(self, mock_gitlab_class):
        """Test that update_file raises NotFoundError for missing file."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.files.get.side_effect = GitlabGetError("Not found", 404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.update_file(
                project_id=123,
                file_path="missing.txt",
                branch="main",
                content="test",
                commit_message="Test",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_file_validates_params(self, mock_gitlab_class):
        """Test that update_file validates required parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Test empty file_path
        with pytest.raises(ValueError, match="file_path.*required"):
            client.update_file(
                project_id=123,
                file_path="",
                branch="main",
                content="test",
                commit_message="Test",
            )

        # Test empty branch
        with pytest.raises(ValueError, match="branch.*required"):
            client.update_file(
                project_id=123,
                file_path="test.txt",
                branch="",
                content="test",
                commit_message="Test",
            )

        # Test empty commit_message
        with pytest.raises(ValueError, match="commit_message.*required"):
            client.update_file(
                project_id=123,
                file_path="test.txt",
                branch="main",
                content="test",
                commit_message="",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_file_removes_file(self, mock_gitlab_class):
        """Test that delete_file deletes a file from repository."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.id = 123
        mock_project.files.delete.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_file(
            project_id=123,
            file_path="old_file.txt",
            branch="main",
            commit_message="Remove old file",
        )

        mock_project.files.delete.assert_called_once_with(
            file_path="old_file.txt",
            branch="main",
            commit_message="Remove old file",
        )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_file_by_project_path(self, mock_gitlab_class):
        """Test that delete_file works with project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.files.delete.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_file(
            project_id="group/project",
            file_path="test.txt",
            branch="main",
            commit_message="Remove test file",
        )

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")
        mock_project.files.delete.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_file_not_found(self, mock_gitlab_class):
        """Test that delete_file raises NotFoundError for non-existent file."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.files.delete.side_effect = GitlabGetError("File not found", 404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError):
            client.delete_file(
                project_id=123,
                file_path="nonexistent.txt",
                branch="main",
                commit_message="Remove file",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_file_validates_params(self, mock_gitlab_class):
        """Test that delete_file validates required parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Test empty file_path
        with pytest.raises(ValueError, match="file_path.*required"):
            client.delete_file(
                project_id=123,
                file_path="",
                branch="main",
                commit_message="Test",
            )

        # Test empty branch
        with pytest.raises(ValueError, match="branch.*required"):
            client.delete_file(
                project_id=123,
                file_path="test.txt",
                branch="",
                commit_message="Test",
            )

        # Test empty commit_message
        with pytest.raises(ValueError, match="commit_message.*required"):
            client.delete_file(
                project_id=123,
                file_path="test.txt",
                branch="main",
                commit_message="",
            )

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_file_with_author(self, mock_gitlab_class):
        """Test that delete_file handles optional author attribution."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.files.delete.return_value = None

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_file(
            project_id=123,
            file_path="docs/old.md",
            branch="develop",
            commit_message="Remove old documentation",
            author_email="test@example.com",
            author_name="Test User",
        )

        delete_args = mock_project.files.delete.call_args[1]
        assert delete_args["file_path"] == "docs/old.md"
        assert delete_args["branch"] == "develop"
        assert delete_args["commit_message"] == "Remove old documentation"
        assert delete_args["author_email"] == "test@example.com"
        assert delete_args["author_name"] == "Test User"


class TestGitLabClientListMergeRequests:
    """Test GitLabClient.list_merge_requests() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests_returns_merge_requests(self, mock_gitlab_class):
        """Test that list_merge_requests returns a list of merge requests."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR objects
        mock_mr1 = Mock()
        mock_mr1.asdict.return_value = {
            "iid": 1,
            "title": "Add new feature",
            "state": "opened",
            "source_branch": "feature/new-feature",
            "target_branch": "main",
        }

        mock_mr2 = Mock()
        mock_mr2.asdict.return_value = {
            "iid": 2,
            "title": "Fix bug",
            "state": "merged",
            "source_branch": "fix/bug-123",
            "target_branch": "main",
        }

        # Mock project and MRs manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.list.return_value = [mock_mr1, mock_mr2]
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            merge_requests = client.list_merge_requests(project_id=123)

            assert len(merge_requests) == 2
            assert merge_requests[0]["iid"] == 1
            assert merge_requests[0]["title"] == "Add new feature"
            assert merge_requests[1]["iid"] == 2
            assert merge_requests[1]["title"] == "Fix bug"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests_with_state_filter(self, mock_gitlab_class):
        """Test that list_merge_requests filters by state."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR object
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.title = "Opened MR"
        mock_mr.state = "opened"

        # Mock project and MRs manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.list.return_value = [mock_mr]
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            merge_requests = client.list_merge_requests(project_id=123, state="opened")

            mock_mrs_manager.list.assert_called_once_with(state="opened", page=1, per_page=20)
            assert len(merge_requests) == 1

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests_with_pagination(self, mock_gitlab_class):
        """Test that list_merge_requests supports pagination."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR objects
        mock_mrs = [Mock() for _ in range(10)]
        for i, mr in enumerate(mock_mrs):
            mr.iid = i + 1
            mr.title = f"MR {i + 1}"

        # Mock project and MRs manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.list.return_value = mock_mrs
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            merge_requests = client.list_merge_requests(project_id=123, page=2, per_page=10)

            mock_mrs_manager.list.assert_called_once_with(page=2, per_page=10)
            assert len(merge_requests) == 10

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests_project_not_found(self, mock_gitlab_class):
        """Test that list_merge_requests raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project not found
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(response_code=404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.list_merge_requests(project_id=999)

            assert "Project with ID 999 not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests_auth_error(self, mock_gitlab_class):
        """Test that list_merge_requests raises AuthenticationError on auth failure."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock authentication error
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError()

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(AuthenticationError):
                client.list_merge_requests(project_id=123)


class TestGitLabClientGetMergeRequest:
    """Test GitLabClient.get_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_returns_merge_request(self, mock_gitlab_class):
        """Test that get_merge_request returns a merge request."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR object with asdict() method
        mock_mr = Mock()
        mock_mr.asdict.return_value = {
            "iid": 42,
            "title": "Add new feature",
            "state": "opened",
            "source_branch": "feature/new-feature",
            "target_branch": "main",
            "description": "This MR adds a new feature",
            "author": {"id": 1, "username": "test_user"},
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            mr = client.get_merge_request(project_id=123, mr_iid=42)

            assert mr["iid"] == 42
            assert mr["title"] == "Add new feature"
            assert mr["state"] == "opened"
            mock_mrs_manager.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_not_found(self, mock_gitlab_class):
        """Test that get_merge_request raises NotFoundError for invalid MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR not found
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.side_effect = GitlabGetError(response_code=404)
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.get_merge_request(project_id=123, mr_iid=999)

            assert "Merge request with IID 999 not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_project_not_found(self, mock_gitlab_class):
        """Test that get_merge_request raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project not found
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(response_code=404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.get_merge_request(project_id=999, mr_iid=1)

            assert "Project with ID 999 not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_auth_error(self, mock_gitlab_class):
        """Test that get_merge_request raises AuthenticationError on auth failure."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock authentication error
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError()

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(AuthenticationError):
                client.get_merge_request(project_id=123, mr_iid=1)


class TestGitLabClientCreateMergeRequest:
    """Test GitLabClient.create_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_success(self, mock_gitlab_class):
        """Test that create_merge_request creates a new MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock created MR
        mock_mr = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "title": "Add new feature",
            "source_branch": "feature/new-feature",
            "target_branch": "main",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.create.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            mr = client.create_merge_request(
                project_id=123,
                source_branch="feature/new-feature",
                target_branch="main",
                title="Add new feature",
            )

            assert mr["iid"] == 1
            assert mr["title"] == "Add new feature"
            mock_mrs_manager.create.assert_called_once()
            create_args = mock_mrs_manager.create.call_args[0][0]
            assert create_args["source_branch"] == "feature/new-feature"
            assert create_args["target_branch"] == "main"
            assert create_args["title"] == "Add new feature"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_with_description(self, mock_gitlab_class):
        """Test that create_merge_request includes description when provided."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock created MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.description = "This is a detailed description"

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.create.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.create_merge_request(
                project_id=123,
                source_branch="feature/new",
                target_branch="main",
                title="New feature",
                description="This is a detailed description",
            )

            create_args = mock_mrs_manager.create.call_args[0][0]
            assert create_args["description"] == "This is a detailed description"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_with_assignees(self, mock_gitlab_class):
        """Test that create_merge_request includes assignee IDs when provided."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock created MR
        mock_mr = Mock()
        mock_mr.iid = 1

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.create.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            client.create_merge_request(
                project_id=123,
                source_branch="feature/new",
                target_branch="main",
                title="New feature",
                assignee_ids=[10, 20],
            )

            create_args = mock_mrs_manager.create.call_args[0][0]
            assert create_args["assignee_ids"] == [10, 20]

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_empty_title_raises_error(self, mock_gitlab_class):
        """Test that create_merge_request raises ValueError for empty title."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(ValueError) as exc_info:
                client.create_merge_request(
                    project_id=123,
                    source_branch="feature/new",
                    target_branch="main",
                    title="",
                )

            assert "title is required" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_empty_source_branch_raises_error(self, mock_gitlab_class):
        """Test that create_merge_request raises ValueError for empty source branch."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(ValueError) as exc_info:
                client.create_merge_request(
                    project_id=123,
                    source_branch="",
                    target_branch="main",
                    title="New feature",
                )

            assert "source_branch is required" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_project_not_found(self, mock_gitlab_class):
        """Test that create_merge_request raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project not found
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError(response_code=404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.create_merge_request(
                    project_id=999,
                    source_branch="feature/new",
                    target_branch="main",
                    title="New feature",
                )

            assert "Project with ID 999 not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request_auth_error(self, mock_gitlab_class):
        """Test that create_merge_request raises AuthenticationError on auth failure."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock authentication error
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabAuthenticationError()

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(AuthenticationError):
                client.create_merge_request(
                    project_id=123,
                    source_branch="feature/new",
                    target_branch="main",
                    title="New feature",
                )


class TestGitLabClientUpdateMergeRequest:
    """Test GitLabClient.update_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_title(self, mock_gitlab_class):
        """Test that update_merge_request updates MR title."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.title = "Original title"
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "title": "Updated title",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.update_merge_request(
                project_id=123,
                mr_iid=1,
                title="Updated title",
            )

            assert result["title"] == "Updated title"
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_description(self, mock_gitlab_class):
        """Test that update_merge_request updates MR description."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.description = "Original description"
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "description": "Updated description",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.update_merge_request(
                project_id=123,
                mr_iid=1,
                description="Updated description",
            )

            assert result["description"] == "Updated description"
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_labels(self, mock_gitlab_class):
        """Test that update_merge_request updates MR labels."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.labels = ["bug"]
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "labels": ["bug", "high-priority"],
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.update_merge_request(
                project_id=123,
                mr_iid=1,
                labels=["bug", "high-priority"],
            )

            assert result["labels"] == ["bug", "high-priority"]
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_assignee_ids(self, mock_gitlab_class):
        """Test that update_merge_request updates MR assignee IDs."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.assignee_ids = [10]
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "assignee_ids": [10, 20],
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.update_merge_request(
                project_id=123,
                mr_iid=1,
                assignee_ids=[10, 20],
            )

            assert result["assignee_ids"] == [10, 20]
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_partial_update(self, mock_gitlab_class):
        """Test that update_merge_request only updates provided fields."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR with original values
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.title = "Original title"
        mock_mr.description = "Original description"
        mock_mr.labels = ["bug"]
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "title": "Updated title",
            "description": "Original description",
            "labels": ["bug"],
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            # Only update title, other fields should remain unchanged
            result = client.update_merge_request(
                project_id=123,
                mr_iid=1,
                title="Updated title",
            )

            # Title should be updated
            assert result["title"] == "Updated title"
            # Description and labels should remain unchanged
            assert result["description"] == "Original description"
            assert result["labels"] == ["bug"]
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_merge_request_not_found(self, mock_gitlab_class):
        """Test that update_merge_request raises NotFoundError for invalid MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock 404 error
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.side_effect = GitlabGetError("Not found", 404)

        mock_project = Mock()
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.update_merge_request(
                    project_id=123,
                    mr_iid=999,
                    title="Updated title",
                )

            assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientMergeMergeRequest:
    """Test GitLabClient.merge_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_merge_merge_request_success(self, mock_gitlab_class):
        """Test that merge_merge_request merges an MR successfully."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.state = "merged"
        mock_mr.merge = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "state": "merged",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.merge_merge_request(
                project_id=123,
                mr_iid=1,
            )

            assert result["state"] == "merged"
            mock_mr.merge.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_merge_merge_request_with_message(self, mock_gitlab_class):
        """Test that merge_merge_request includes merge commit message."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.state = "merged"
        mock_mr.merge = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "state": "merged",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.merge_merge_request(
                project_id=123,
                mr_iid=1,
                merge_commit_message="Custom merge message",
            )

            assert result["state"] == "merged"
            mock_mr.merge.assert_called_once_with(merge_commit_message="Custom merge message")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_merge_merge_request_not_found(self, mock_gitlab_class):
        """Test that merge_merge_request raises NotFoundError for invalid MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock 404 error
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.side_effect = GitlabGetError("Not found", 404)

        mock_project = Mock()
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.merge_merge_request(
                    project_id=123,
                    mr_iid=999,
                )

            assert "Project or merge request not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_merge_merge_request_already_merged(self, mock_gitlab_class):
        """Test that merge_merge_request handles already merged MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock already merged error (405 Method Not Allowed)
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.merge.side_effect = GitlabHttpError("Method not allowed", 405)

        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(GitLabAPIError) as exc_info:
                client.merge_merge_request(
                    project_id=123,
                    mr_iid=1,
                )

            assert "Method not allowed" in str(exc_info.value) or "405" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_merge_merge_request_conflict(self, mock_gitlab_class):
        """Test that merge_merge_request handles merge conflicts."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock merge conflict error (406 Not Acceptable)
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.merge.side_effect = GitlabHttpError("Branch cannot be merged", 406)

        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(GitLabAPIError) as exc_info:
                client.merge_merge_request(
                    project_id=123,
                    mr_iid=1,
                )

            assert "Branch cannot be merged" in str(exc_info.value) or "406" in str(exc_info.value)


class TestGitLabClientCloseMergeRequest:
    """Test GitLabClient.close_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_merge_request_success(self, mock_gitlab_class):
        """Test that close_merge_request closes an MR successfully."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.state = "closed"
        mock_mr.state_event = None
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "state": "closed",
            "state_event": "close",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.close_merge_request(
                project_id=123,
                mr_iid=1,
            )

            assert result["state_event"] == "close"
            mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_merge_request_returns_closed_mr(self, mock_gitlab_class):
        """Test that close_merge_request returns the closed MR object."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR
        mock_mr = Mock()
        mock_mr.iid = 1
        mock_mr.title = "Test MR"
        mock_mr.state = "closed"
        mock_mr.state_event = None
        mock_mr.save = Mock()
        mock_mr.asdict.return_value = {
            "iid": 1,
            "title": "Test MR",
            "state": "closed",
            "state_event": "close",
        }

        # Mock project and MR manager
        mock_project = Mock()
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.return_value = mock_mr
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            result = client.close_merge_request(
                project_id=123,
                mr_iid=1,
            )

            assert result["iid"] == 1
            assert result["title"] == "Test MR"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_merge_request_not_found(self, mock_gitlab_class):
        """Test that close_merge_request raises NotFoundError for invalid MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock 404 error
        mock_mrs_manager = Mock()
        mock_mrs_manager.get.side_effect = GitlabGetError("Not found", 404)

        mock_project = Mock()
        mock_project.mergerequests = mock_mrs_manager

        mock_projects_manager = Mock()
        mock_projects_manager.get.return_value = mock_project

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.close_merge_request(
                    project_id=123,
                    mr_iid=999,
                )

            assert "Project or merge request not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_close_merge_request_project_not_found(self, mock_gitlab_class):
        """Test that close_merge_request raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project not found
        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = GitlabGetError("Not found", 404)

        with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock_gitlab_class:
            mock_gitlab_instance = Mock()
            mock_gitlab_instance.projects = mock_projects_manager
            mock_gitlab_class.return_value = mock_gitlab_instance

            client = GitLabClient(config)
            client._ensure_authenticated = Mock()
            client._gitlab = mock_gitlab_instance

            with pytest.raises(NotFoundError) as exc_info:
                client.close_merge_request(
                    project_id=999,
                    mr_iid=1,
                )

            assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientAddMrComment:
    """Test GitLabClient.add_mr_comment() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_returns_note(self, mock_gitlab_class):
        """Test that add_mr_comment creates and returns a note."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note = Mock()
        mock_note.id = 100
        mock_note.body = "Test MR comment"

        mock_notes_manager = Mock()
        mock_notes_manager.create.return_value = mock_note

        mock_mr = Mock()
        mock_mr.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.add_mr_comment(project_id=123, mr_iid=1, body="Test MR comment")

        # Verify note created with correct body
        mock_notes_manager.create.assert_called_once_with({"body": "Test MR comment"})
        assert result == mock_note

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_by_project_path(self, mock_gitlab_class):
        """Test add_mr_comment using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note = Mock()
        mock_notes_manager = Mock()
        mock_notes_manager.create.return_value = mock_note

        mock_mr = Mock()
        mock_mr.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.add_mr_comment(project_id="group/project", mr_iid=1, body="Comment")

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_requires_authentication(self, mock_gitlab_class):
        """Test that add_mr_comment checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.add_mr_comment(project_id=123, mr_iid=1, body="Comment")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_empty_body_raises_error(self, mock_gitlab_class):
        """Test that add_mr_comment validates body is not empty."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()

        with pytest.raises(ValueError) as exc_info:
            client.add_mr_comment(project_id=123, mr_iid=1, body="")

        assert "Comment body cannot be empty" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_whitespace_body_raises_error(self, mock_gitlab_class):
        """Test that add_mr_comment validates body is not just whitespace."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()

        with pytest.raises(ValueError) as exc_info:
            client.add_mr_comment(project_id=123, mr_iid=1, body="   ")

        assert "Comment body cannot be empty" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_add_mr_comment_not_found_raises_error(self, mock_gitlab_class):
        """Test that add_mr_comment raises NotFoundError for missing project or MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.add_mr_comment(project_id=999, mr_iid=1, body="Comment")

        assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientListMrComments:
    """Test GitLabClient.list_mr_comments() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_mr_comments_returns_notes(self, mock_gitlab_class):
        """Test that list_mr_comments returns list of notes."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_note1 = Mock()
        mock_note1.id = 100
        mock_note1.body = "First comment"

        mock_note2 = Mock()
        mock_note2.id = 101
        mock_note2.body = "Second comment"

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = [mock_note1, mock_note2]

        mock_mr = Mock()
        mock_mr.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_mr_comments(project_id=123, mr_iid=1)

        # Verify notes listed with default pagination
        mock_notes_manager.list.assert_called_once_with(page=1, per_page=20)
        assert len(result) == 2
        assert result[0] == mock_note1
        assert result[1] == mock_note2

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_mr_comments_empty(self, mock_gitlab_class):
        """Test list_mr_comments when MR has no comments."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = []

        mock_mr = Mock()
        mock_mr.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_mr_comments(project_id=123, mr_iid=1)

        assert result == []

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_mr_comments_pagination(self, mock_gitlab_class):
        """Test list_mr_comments with custom pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_notes_manager = Mock()
        mock_notes_manager.list.return_value = []

        mock_mr = Mock()
        mock_mr.notes = mock_notes_manager

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_mr_comments(project_id=123, mr_iid=1, page=2, per_page=50)

        # Verify custom pagination parameters used
        mock_notes_manager.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_mr_comments_not_found_raises_error(self, mock_gitlab_class):
        """Test that list_mr_comments raises NotFoundError for missing project or MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_mr_comments(project_id=999, mr_iid=1)

        assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientApproveMergeRequest:
    """Test GitLabClient.approve_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_approve_merge_request_success(self, mock_gitlab_class):
        """Test that approve_merge_request approves an MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_approval = Mock()
        mock_approval.id = 100
        mock_approval.user = {"id": 1, "username": "testuser"}

        mock_mr = Mock()
        mock_mr.approve.return_value = mock_approval

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.approve_merge_request(project_id=123, mr_iid=1)

        # Verify approve was called
        mock_mr.approve.assert_called_once()
        assert result == mock_approval

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_approve_merge_request_by_project_path(self, mock_gitlab_class):
        """Test approve_merge_request using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_approval = Mock()
        mock_mr = Mock()
        mock_mr.approve.return_value = mock_approval

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.approve_merge_request(project_id="group/project", mr_iid=1)

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_approve_merge_request_requires_authentication(self, mock_gitlab_class):
        """Test that approve_merge_request checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.approve_merge_request(project_id=123, mr_iid=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_approve_merge_request_not_found_raises_error(self, mock_gitlab_class):
        """Test that approve_merge_request raises NotFoundError for missing project or MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.approve_merge_request(project_id=999, mr_iid=1)

        assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientUnapproveMergeRequest:
    """Test GitLabClient.unapprove_merge_request() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_unapprove_merge_request_success(self, mock_gitlab_class):
        """Test that unapprove_merge_request removes approval from an MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.unapprove.return_value = None

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.unapprove_merge_request(project_id=123, mr_iid=1)

        # Verify unapprove was called
        mock_mr.unapprove.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_unapprove_merge_request_by_project_path(self, mock_gitlab_class):
        """Test unapprove_merge_request using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.unapprove.return_value = None

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.unapprove_merge_request(project_id="group/project", mr_iid=1)

        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_unapprove_merge_request_requires_authentication(self, mock_gitlab_class):
        """Test that unapprove_merge_request checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.unapprove_merge_request(project_id=123, mr_iid=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_unapprove_merge_request_not_found_raises_error(self, mock_gitlab_class):
        """Test that unapprove_merge_request raises NotFoundError for missing project or MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.unapprove_merge_request(project_id=999, mr_iid=1)

        assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientListPipelines:
    """Tests for GitLabClient.list_pipelines method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_success(self, mock_gitlab_class):
        """Test list_pipelines with basic parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock pipeline objects
        mock_pipeline1 = Mock()
        mock_pipeline1.get_id.return_value = 101
        mock_pipeline1.asdict.return_value = {
            "id": 101,
            "status": "success",
            "ref": "main",
            "sha": "abc123",
            "web_url": "https://gitlab.example.com/project/pipelines/101",
        }

        mock_pipeline2 = Mock()
        mock_pipeline2.get_id.return_value = 102
        mock_pipeline2.asdict.return_value = {
            "id": 102,
            "status": "failed",
            "ref": "develop",
            "sha": "def456",
            "web_url": "https://gitlab.example.com/project/pipelines/102",
        }

        mock_project = Mock()
        mock_project.pipelines.list.return_value = [mock_pipeline1, mock_pipeline2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_pipelines(project_id=123)

        # Verify result structure
        assert isinstance(result, dict)
        assert "pipelines" in result
        assert len(result["pipelines"]) == 2
        assert result["pipelines"][0]["id"] == 101
        assert result["pipelines"][0]["status"] == "success"
        assert result["pipelines"][1]["id"] == 102
        assert result["pipelines"][1]["status"] == "failed"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_with_pagination(self, mock_gitlab_class):
        """Test list_pipelines with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.pipelines.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipelines(project_id=123, page=2, per_page=50)

        # Verify pagination parameters were passed
        mock_project.pipelines.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_with_filters(self, mock_gitlab_class):
        """Test list_pipelines with optional filter parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.pipelines.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipelines(
            project_id=123,
            ref="main",
            status="success",
        )

        # Verify filter parameters were passed
        call_kwargs = mock_project.pipelines.list.call_args[1]
        assert call_kwargs["ref"] == "main"
        assert call_kwargs["status"] == "success"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_by_project_path(self, mock_gitlab_class):
        """Test list_pipelines using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.pipelines.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipelines(project_id="group/project")

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_requires_authentication(self, mock_gitlab_class):
        """Test that list_pipelines checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_pipelines(project_id=123)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipelines_project_not_found(self, mock_gitlab_class):
        """Test that list_pipelines raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_pipelines(project_id=999)

        assert "Project not found" in str(exc_info.value)


class TestGitLabClientGetPipeline:
    """Tests for GitLabClient.get_pipeline method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_pipeline_success(self, mock_gitlab_class):
        """Test get_pipeline with valid parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.get_id.return_value = 101
        mock_pipeline.asdict.return_value = {
            "id": 101,
            "status": "success",
            "ref": "main",
            "sha": "abc123",
            "web_url": "https://gitlab.example.com/project/pipelines/101",
            "created_at": "2024-01-15T10:00:00Z",
            "updated_at": "2024-01-15T10:05:00Z",
        }

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_pipeline(project_id=123, pipeline_id=101)

        # Verify result structure
        assert isinstance(result, dict)
        assert result["id"] == 101
        assert result["status"] == "success"
        assert result["ref"] == "main"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_pipeline_by_project_path(self, mock_gitlab_class):
        """Test get_pipeline using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.asdict.return_value = {"id": 101}

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_pipeline(project_id="group/project", pipeline_id=101)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_pipeline_requires_authentication(self, mock_gitlab_class):
        """Test that get_pipeline checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_pipeline(project_id=123, pipeline_id=101)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_pipeline_project_not_found(self, mock_gitlab_class):
        """Test that get_pipeline raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_pipeline(project_id=999, pipeline_id=101)

        assert "Project or pipeline not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_pipeline_pipeline_not_found(self, mock_gitlab_class):
        """Test that get_pipeline raises NotFoundError for invalid pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_pipeline(project_id=123, pipeline_id=999999)

        assert "Project or pipeline not found" in str(exc_info.value)


class TestGitLabClientCreatePipeline:
    """Tests for GitLabClient.create_pipeline method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_pipeline_success(self, mock_gitlab_class):
        """Test create_pipeline with valid parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.get_id.return_value = 201
        mock_pipeline.asdict.return_value = {
            "id": 201,
            "status": "pending",
            "ref": "main",
            "sha": "abc123",
            "web_url": "https://gitlab.example.com/project/pipelines/201",
        }

        mock_project = Mock()
        mock_project.pipelines.create.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_pipeline(project_id=123, ref="main")

        # Verify result structure
        assert isinstance(result, dict)
        assert result["id"] == 201
        assert result["status"] == "pending"
        assert result["ref"] == "main"

        # Verify create was called with correct params
        mock_project.pipelines.create.assert_called_once_with({"ref": "main"})

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_pipeline_with_variables(self, mock_gitlab_class):
        """Test create_pipeline with pipeline variables."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.asdict.return_value = {"id": 201}

        mock_project = Mock()
        mock_project.pipelines.create.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        variables = {"ENV": "production", "DEBUG": "false"}
        client.create_pipeline(project_id=123, ref="main", variables=variables)

        # Verify variables were passed
        call_args = mock_project.pipelines.create.call_args[0][0]
        assert call_args["ref"] == "main"
        assert call_args["variables"] == variables

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_pipeline_by_project_path(self, mock_gitlab_class):
        """Test create_pipeline using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.asdict.return_value = {"id": 201}

        mock_project = Mock()
        mock_project.pipelines.create.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_pipeline(project_id="group/project", ref="main")

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_pipeline_requires_authentication(self, mock_gitlab_class):
        """Test that create_pipeline checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.create_pipeline(project_id=123, ref="main")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_pipeline_project_not_found(self, mock_gitlab_class):
        """Test that create_pipeline raises NotFoundError for invalid project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_projects_manager = Mock()
        mock_projects_manager.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects = mock_projects_manager
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.create_pipeline(project_id=999, ref="main")

        assert "Project not found" in str(exc_info.value)


class TestGitLabClientRetryPipeline:
    """Tests for GitLabClient.retry_pipeline method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_pipeline_success(self, mock_gitlab_class):
        """Test retry_pipeline with valid parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock pipeline with retry method
        mock_pipeline = Mock()
        mock_pipeline.retry.return_value = None
        mock_pipeline.get_id.return_value = 101
        mock_pipeline.asdict.return_value = {
            "id": 101,
            "status": "pending",
            "ref": "main",
        }

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.retry_pipeline(project_id=123, pipeline_id=101)

        # Verify retry was called
        mock_pipeline.retry.assert_called_once()

        # Verify result structure
        assert isinstance(result, dict)
        assert result["id"] == 101

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_pipeline_by_project_path(self, mock_gitlab_class):
        """Test retry_pipeline using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.retry.return_value = None
        mock_pipeline.asdict.return_value = {"id": 101}

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.retry_pipeline(project_id="group/project", pipeline_id=101)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_pipeline_requires_authentication(self, mock_gitlab_class):
        """Test that retry_pipeline checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.retry_pipeline(project_id=123, pipeline_id=101)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_pipeline_not_found_raises_error(self, mock_gitlab_class):
        """Test that retry_pipeline raises NotFoundError for missing pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.retry_pipeline(project_id=123, pipeline_id=999)

        assert "Project or pipeline not found" in str(exc_info.value)


class TestGitLabClientCancelPipeline:
    """Tests for GitLabClient.cancel_pipeline method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_pipeline_success(self, mock_gitlab_class):
        """Test cancel_pipeline with valid parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock pipeline with cancel method
        mock_pipeline = Mock()
        mock_pipeline.cancel.return_value = None
        mock_pipeline.get_id.return_value = 101
        mock_pipeline.asdict.return_value = {
            "id": 101,
            "status": "canceled",
            "ref": "main",
        }

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.cancel_pipeline(project_id=123, pipeline_id=101)

        # Verify cancel was called
        mock_pipeline.cancel.assert_called_once()

        # Verify result structure
        assert isinstance(result, dict)
        assert result["id"] == 101

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_pipeline_by_project_path(self, mock_gitlab_class):
        """Test cancel_pipeline using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.cancel.return_value = None
        mock_pipeline.asdict.return_value = {"id": 101}

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.cancel_pipeline(project_id="group/project", pipeline_id=101)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_pipeline_requires_authentication(self, mock_gitlab_class):
        """Test that cancel_pipeline checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.cancel_pipeline(project_id=123, pipeline_id=101)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_pipeline_not_found_raises_error(self, mock_gitlab_class):
        """Test that cancel_pipeline raises NotFoundError for missing pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.cancel_pipeline(project_id=123, pipeline_id=999)

        assert "Project or pipeline not found" in str(exc_info.value)


class TestGitLabClientDeletePipeline:
    """Tests for GitLabClient.delete_pipeline method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_pipeline_success(self, mock_gitlab_class):
        """Test delete_pipeline with valid parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock pipeline with delete method
        mock_pipeline = Mock()
        mock_pipeline.delete.return_value = None

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.delete_pipeline(project_id=123, pipeline_id=101)

        # Verify delete was called
        mock_pipeline.delete.assert_called_once()

        # Verify result is success confirmation
        assert isinstance(result, dict)
        assert result["success"] is True

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_pipeline_by_project_path(self, mock_gitlab_class):
        """Test delete_pipeline using project path instead of ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.delete.return_value = None

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_pipeline(project_id="group/project", pipeline_id=101)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_pipeline_requires_authentication(self, mock_gitlab_class):
        """Test that delete_pipeline checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.delete_pipeline(project_id=123, pipeline_id=101)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_pipeline_not_found_raises_error(self, mock_gitlab_class):
        """Test that delete_pipeline raises NotFoundError for missing pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_pipeline(project_id=123, pipeline_id=999)

        assert "Project or pipeline not found" in str(exc_info.value)


class TestGitLabClientListPipelineJobs:
    """Tests for GitLabClient.list_pipeline_jobs method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_jobs_success(self, mock_gitlab_class):
        """Test list_pipeline_jobs returns list of jobs."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock jobs
        mock_job1 = Mock()
        mock_job1.asdict.return_value = {
            "id": 1,
            "name": "build",
            "status": "success",
            "stage": "build",
        }
        mock_job2 = Mock()
        mock_job2.asdict.return_value = {
            "id": 2,
            "name": "test",
            "status": "running",
            "stage": "test",
        }

        mock_pipeline = Mock()
        mock_pipeline.jobs.list.return_value = [mock_job1, mock_job2]

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_pipeline_jobs(project_id=123, pipeline_id=101)

        # Verify jobs were listed
        mock_pipeline.jobs.list.assert_called_once()

        # Verify result structure
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["name"] == "build"
        assert result[1]["id"] == 2
        assert result[1]["name"] == "test"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_jobs_with_pagination(self, mock_gitlab_class):
        """Test list_pipeline_jobs with pagination parameters."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.asdict.return_value = {"id": 1, "name": "build"}

        mock_pipeline = Mock()
        mock_pipeline.jobs.list.return_value = [mock_job]

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipeline_jobs(project_id=123, pipeline_id=101, page=2, per_page=50)

        # Verify pagination params passed
        mock_pipeline.jobs.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_jobs_by_project_path(self, mock_gitlab_class):
        """Test list_pipeline_jobs using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.jobs.list.return_value = []

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipeline_jobs(project_id="group/project", pipeline_id=101)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_jobs_requires_authentication(self, mock_gitlab_class):
        """Test that list_pipeline_jobs checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_pipeline_jobs(project_id=123, pipeline_id=101)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_jobs_not_found_raises_error(self, mock_gitlab_class):
        """Test that list_pipeline_jobs raises NotFoundError for missing pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_pipeline_jobs(project_id=123, pipeline_id=999)

        assert "Project or pipeline not found" in str(exc_info.value)


class TestGitLabClientGetJob:
    """Tests for GitLabClient.get_job method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_success(self, mock_gitlab_class):
        """Test get_job returns job details."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job
        mock_job = Mock()
        mock_job.asdict.return_value = {
            "id": 1,
            "name": "build",
            "status": "success",
            "stage": "build",
            "duration": 120.5,
        }

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_job(project_id=123, job_id=1)

        # Verify job was fetched
        mock_project.jobs.get.assert_called_once_with(1)

        # Verify result structure
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert result["name"] == "build"
        assert result["status"] == "success"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_by_project_path(self, mock_gitlab_class):
        """Test get_job using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.asdict.return_value = {"id": 1, "name": "build"}

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_job(project_id="group/project", job_id=1)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_requires_authentication(self, mock_gitlab_class):
        """Test that get_job checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_job(project_id=123, job_id=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_not_found_raises_error(self, mock_gitlab_class):
        """Test that get_job raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_job(project_id=123, job_id=999)

        assert "Project or job not found" in str(exc_info.value)


class TestGitLabClientGetJobTrace:
    """Tests for GitLabClient.get_job_trace method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_trace_success(self, mock_gitlab_class):
        """Test get_job_trace returns job log."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job with trace
        mock_job = Mock()
        mock_job.trace.return_value = b"Building project...\nTests passed!\n"

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_job_trace(project_id=123, job_id=1)

        # Verify trace was fetched
        mock_job.trace.assert_called_once()

        # Verify result structure
        assert isinstance(result, dict)
        assert "trace" in result
        assert result["trace"] == "Building project...\nTests passed!\n"
        assert result["job_id"] == 1

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_trace_bytes_handling(self, mock_gitlab_class):
        """Test get_job_trace handles bytes properly."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.trace.return_value = b"Build log content"

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_job_trace(project_id=123, job_id=1)

        # Verify bytes decoded to string
        assert result["trace"] == "Build log content"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_trace_by_project_path(self, mock_gitlab_class):
        """Test get_job_trace using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.trace.return_value = b"log"

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_job_trace(project_id="group/project", job_id=1)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_trace_requires_authentication(self, mock_gitlab_class):
        """Test that get_job_trace checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_job_trace(project_id=123, job_id=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_job_trace_not_found_raises_error(self, mock_gitlab_class):
        """Test that get_job_trace raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_job_trace(project_id=123, job_id=999)

        assert "Project or job not found" in str(exc_info.value)


class TestGitLabClientGetMergeRequestChanges:
    """Tests for get_merge_request_changes method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_changes_success(self, mock_gitlab_class):
        """Test get_merge_request_changes returns changes/diffs."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR changes
        mock_mr = Mock()
        mock_mr.changes.return_value = {
            "changes": [
                {
                    "old_path": "README.md",
                    "new_path": "README.md",
                    "diff": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ]
        }

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request_changes(project_id=123, merge_request_iid=10)

        # Verify MR was fetched
        mock_project.mergerequests.get.assert_called_once_with(10)
        mock_mr.changes.assert_called_once()

        # Verify result structure
        assert isinstance(result, dict)
        assert "changes" in result
        assert len(result["changes"]) == 1
        assert result["changes"][0]["old_path"] == "README.md"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_changes_by_project_path(self, mock_gitlab_class):
        """Test get_merge_request_changes using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.changes.return_value = {"changes": []}

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_merge_request_changes(project_id="group/project", merge_request_iid=10)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_changes_requires_authentication(self, mock_gitlab_class):
        """Test that get_merge_request_changes checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_merge_request_changes(project_id=123, merge_request_iid=10)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_changes_not_found_raises_error(self, mock_gitlab_class):
        """Test that get_merge_request_changes raises NotFoundError for missing MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Merge request not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.mergerequests.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_merge_request_changes(project_id=123, merge_request_iid=999)

        assert "Merge request not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_changes_api_error_raises_error(self, mock_gitlab_class):
        """Test that get_merge_request_changes raises GitLabAPIError on API failure."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_project = Mock()
        mock_project.mergerequests.get.side_effect = GitlabError("API failure")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(GitLabAPIError) as exc_info:
            client.get_merge_request_changes(project_id=123, merge_request_iid=10)

        assert "Failed to get merge request changes" in str(exc_info.value)


class TestGitLabClientGetMergeRequestCommits:
    """Tests for get_merge_request_commits method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_commits_success(self, mock_gitlab_class):
        """Test get_merge_request_commits returns list of commits."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock commit objects
        mock_commit1 = Mock()
        mock_commit1.asdict.return_value = {
            "id": "abc123",
            "short_id": "abc123",
            "title": "Fix bug",
            "message": "Fix bug in authentication",
            "author_name": "John Doe",
            "authored_date": "2025-01-01T10:00:00Z",
        }
        mock_commit2 = Mock()
        mock_commit2.asdict.return_value = {
            "id": "def456",
            "short_id": "def456",
            "title": "Add feature",
            "message": "Add new feature",
            "author_name": "Jane Smith",
            "authored_date": "2025-01-02T10:00:00Z",
        }

        mock_mr = Mock()
        mock_mr.commits.return_value = [mock_commit1, mock_commit2]

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request_commits(project_id=123, merge_request_iid=10)

        # Verify MR was fetched
        mock_project.mergerequests.get.assert_called_once_with(10)
        mock_mr.commits.assert_called_once()

        # Verify result structure
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "abc123"
        assert result[0]["title"] == "Fix bug"
        assert result[1]["id"] == "def456"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_commits_empty_list(self, mock_gitlab_class):
        """Test get_merge_request_commits returns empty list when no commits."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.commits.return_value = []

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request_commits(project_id=123, merge_request_iid=10)

        assert isinstance(result, list)
        assert len(result) == 0

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_commits_by_project_path(self, mock_gitlab_class):
        """Test get_merge_request_commits using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.commits.return_value = []

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_merge_request_commits(project_id="group/project", merge_request_iid=10)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_commits_requires_authentication(self, mock_gitlab_class):
        """Test that get_merge_request_commits checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_merge_request_commits(project_id=123, merge_request_iid=10)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_commits_not_found_raises_error(self, mock_gitlab_class):
        """Test that get_merge_request_commits raises NotFoundError for missing MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Merge request not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.mergerequests.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_merge_request_commits(project_id=123, merge_request_iid=999)

        assert "Merge request not found" in str(exc_info.value)


class TestGitLabClientGetMergeRequestPipelines:
    """Tests for get_merge_request_pipelines method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_pipelines_success(self, mock_gitlab_class):
        """Test get_merge_request_pipelines returns list of pipelines."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock pipeline objects
        mock_pipeline1 = Mock()
        mock_pipeline1.asdict.return_value = {
            "id": 101,
            "iid": 1,
            "ref": "main",
            "status": "success",
            "sha": "abc123",
        }
        mock_pipeline2 = Mock()
        mock_pipeline2.asdict.return_value = {
            "id": 102,
            "iid": 2,
            "ref": "main",
            "status": "failed",
            "sha": "def456",
        }

        mock_mr = Mock()
        mock_mr.pipelines.return_value = [mock_pipeline1, mock_pipeline2]

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request_pipelines(project_id=123, merge_request_iid=10)

        # Verify MR was fetched
        mock_project.mergerequests.get.assert_called_once_with(10)
        mock_mr.pipelines.assert_called_once()

        # Verify result structure
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 101
        assert result[0]["status"] == "success"
        assert result[1]["id"] == 102
        assert result[1]["status"] == "failed"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_pipelines_empty_list(self, mock_gitlab_class):
        """Test get_merge_request_pipelines returns empty list when no pipelines."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.pipelines.return_value = []

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request_pipelines(project_id=123, merge_request_iid=10)

        assert isinstance(result, list)
        assert len(result) == 0

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_pipelines_by_project_path(self, mock_gitlab_class):
        """Test get_merge_request_pipelines using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.pipelines.return_value = []

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_merge_request_pipelines(project_id="group/project", merge_request_iid=10)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_pipelines_requires_authentication(self, mock_gitlab_class):
        """Test that get_merge_request_pipelines checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_merge_request_pipelines(project_id=123, merge_request_iid=10)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request_pipelines_not_found_raises_error(self, mock_gitlab_class):
        """Test that get_merge_request_pipelines raises NotFoundError for missing MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Merge request not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.mergerequests.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_merge_request_pipelines(project_id=123, merge_request_iid=999)

        assert "Merge request not found" in str(exc_info.value)


class TestGitLabClientReopenMergeRequest:
    """Tests for reopen_merge_request method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_merge_request_success(self, mock_gitlab_class):
        """Test reopen_merge_request reopens a closed MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock MR object
        mock_mr = Mock()
        mock_mr.state = "closed"
        mock_mr.save = Mock()

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.reopen_merge_request(project_id=123, mr_iid=10)

        # Verify state was set and saved
        assert mock_mr.state_event == "reopen"
        mock_mr.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_merge_request_by_project_path(self, mock_gitlab_class):
        """Test reopen_merge_request using project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_mr = Mock()
        mock_mr.state = "closed"
        mock_mr.save = Mock()

        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.reopen_merge_request(project_id="group/project", mr_iid=10)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_merge_request_requires_authentication(self, mock_gitlab_class):
        """Test that reopen_merge_request checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.reopen_merge_request(project_id=123, mr_iid=10)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_reopen_merge_request_not_found_raises_error(self, mock_gitlab_class):
        """Test that reopen_merge_request raises NotFoundError for missing MR."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Merge request not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.mergerequests.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.reopen_merge_request(project_id=123, mr_iid=999)

        assert "Project or merge request not found" in str(exc_info.value)


class TestGitLabClientRetryJob:
    """Test GitLabClient.retry_job() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_job_success(self, mock_gitlab_class):
        """Test that retry_job successfully retries a job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job object
        mock_job = Mock()
        mock_job.id = 456
        mock_job.name = "test-job"
        mock_job.status = "success"
        mock_job.retry.return_value = None

        # Mock project with jobs manager
        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.retry_job(project_id=123, job_id=456)

        # Verify job retry was called
        mock_job.retry.assert_called_once()

        # Verify result contains confirmation
        assert result["job_id"] == 456
        assert result["status"] == "retried"
        assert "message" in result

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_job_with_string_project_id(self, mock_gitlab_class):
        """Test that retry_job accepts string project ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.id = 456
        mock_job.retry.return_value = None

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.retry_job(project_id="group/project", job_id=456)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_job_requires_authentication(self, mock_gitlab_class):
        """Test that retry_job checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.retry_job(project_id=123, job_id=456)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_retry_job_not_found_raises_error(self, mock_gitlab_class):
        """Test that retry_job raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.retry_job(project_id=123, job_id=999)

        assert "Job not found" in str(exc_info.value)


class TestGitLabClientCancelJob:
    """Test GitLabClient.cancel_job() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_job_success(self, mock_gitlab_class):
        """Test that cancel_job successfully cancels a job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job object
        mock_job = Mock()
        mock_job.id = 456
        mock_job.name = "test-job"
        mock_job.status = "running"
        mock_job.cancel.return_value = None

        # Mock project with jobs manager
        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.cancel_job(project_id=123, job_id=456)

        # Verify job cancel was called
        mock_job.cancel.assert_called_once()

        # Verify result contains confirmation
        assert result["job_id"] == 456
        assert result["status"] == "canceled"
        assert "message" in result

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_job_with_string_project_id(self, mock_gitlab_class):
        """Test that cancel_job accepts string project ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.id = 456
        mock_job.cancel.return_value = None

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.cancel_job(project_id="group/project", job_id=456)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_job_requires_authentication(self, mock_gitlab_class):
        """Test that cancel_job checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.cancel_job(project_id=123, job_id=456)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_cancel_job_not_found_raises_error(self, mock_gitlab_class):
        """Test that cancel_job raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.cancel_job(project_id=123, job_id=999)

        assert "Job not found" in str(exc_info.value)


class TestGitLabClientPlayJob:
    """Test GitLabClient.play_job() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_play_job_success(self, mock_gitlab_class):
        """Test that play_job successfully starts a manual job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job object
        mock_job = Mock()
        mock_job.id = 456
        mock_job.name = "manual-deploy"
        mock_job.status = "manual"
        mock_job.play.return_value = None

        # Mock project with jobs manager
        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.play_job(project_id=123, job_id=456)

        # Verify job play was called
        mock_job.play.assert_called_once()

        # Verify result contains confirmation
        assert result["job_id"] == 456
        assert result["status"] == "started"
        assert "message" in result

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_play_job_with_string_project_id(self, mock_gitlab_class):
        """Test that play_job accepts string project ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.id = 456
        mock_job.play.return_value = None

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.play_job(project_id="group/project", job_id=456)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_play_job_requires_authentication(self, mock_gitlab_class):
        """Test that play_job checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.play_job(project_id=123, job_id=456)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_play_job_not_found_raises_error(self, mock_gitlab_class):
        """Test that play_job raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.play_job(project_id=123, job_id=999)

        assert "Job not found" in str(exc_info.value)


class TestGitLabClientDownloadJobArtifacts:
    """Test GitLabClient.download_job_artifacts() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_download_job_artifacts_success(self, mock_gitlab_class):
        """Test that download_job_artifacts successfully downloads artifacts."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job object with artifacts
        mock_job = Mock()
        mock_job.id = 456
        mock_job.name = "build-job"
        mock_job.artifacts.return_value = b"binary artifact data"

        # Mock project with jobs manager
        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.download_job_artifacts(project_id=123, job_id=456)

        # Verify job artifacts was called
        mock_job.artifacts.assert_called_once()

        # Verify result contains artifacts data
        assert result["job_id"] == 456
        assert result["artifacts_data"] == b"binary artifact data"
        assert "size_bytes" in result

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_download_job_artifacts_with_string_project_id(self, mock_gitlab_class):
        """Test that download_job_artifacts accepts string project ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_job = Mock()
        mock_job.id = 456
        mock_job.artifacts.return_value = b"data"

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.download_job_artifacts(project_id="group/project", job_id=456)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_download_job_artifacts_requires_authentication(self, mock_gitlab_class):
        """Test that download_job_artifacts checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.download_job_artifacts(project_id=123, job_id=456)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_download_job_artifacts_not_found_raises_error(self, mock_gitlab_class):
        """Test that download_job_artifacts raises NotFoundError for missing job."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Job not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.jobs.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.download_job_artifacts(project_id=123, job_id=999)

        assert "Job not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_download_job_artifacts_no_artifacts_raises_error(self, mock_gitlab_class):
        """Test that download_job_artifacts raises error when no artifacts exist."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock job that raises error when accessing artifacts
        mock_job = Mock()
        mock_job.id = 456
        error = GitlabGetError("No artifacts available")
        error.response_code = 404
        mock_job.artifacts.side_effect = error

        mock_project = Mock()
        mock_project.jobs.get.return_value = mock_job

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.download_job_artifacts(project_id=123, job_id=456)

        assert "No artifacts available" in str(exc_info.value)


class TestGitLabClientListPipelineVariables:
    """Test GitLabClient.list_pipeline_variables() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_variables_success(self, mock_gitlab_class):
        """Test that list_pipeline_variables returns pipeline variables."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock variable objects
        mock_var1 = Mock()
        mock_var1.key = "ENV"
        mock_var1.value = "production"

        mock_var2 = Mock()
        mock_var2.key = "DEBUG"
        mock_var2.value = "false"

        # Mock pipeline with variables
        mock_pipeline = Mock()
        mock_pipeline.id = 789
        mock_pipeline.variables.list.return_value = [mock_var1, mock_var2]

        # Mock project
        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_pipeline_variables(project_id=123, pipeline_id=789)

        # Verify variables were fetched
        mock_pipeline.variables.list.assert_called_once()

        # Verify result structure
        assert len(result) == 2
        assert result[0]["key"] == "ENV"
        assert result[0]["value"] == "production"
        assert result[1]["key"] == "DEBUG"
        assert result[1]["value"] == "false"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_variables_empty(self, mock_gitlab_class):
        """Test that list_pipeline_variables returns empty list when no variables."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.variables.list.return_value = []

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_pipeline_variables(project_id=123, pipeline_id=789)

        assert result == []

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_variables_with_string_project_id(self, mock_gitlab_class):
        """Test that list_pipeline_variables accepts string project ID."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_pipeline = Mock()
        mock_pipeline.variables.list.return_value = []

        mock_project = Mock()
        mock_project.pipelines.get.return_value = mock_pipeline

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_pipeline_variables(project_id="group/project", pipeline_id=789)

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_variables_requires_authentication(self, mock_gitlab_class):
        """Test that list_pipeline_variables checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_pipeline_variables(project_id=123, pipeline_id=789)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_pipeline_variables_pipeline_not_found_raises_error(self, mock_gitlab_class):
        """Test that list_pipeline_variables raises NotFoundError for missing pipeline."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Pipeline not found")
        error.response_code = 404

        mock_project = Mock()
        mock_project.pipelines.get.side_effect = error

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_pipeline_variables(project_id=123, pipeline_id=999)

        assert "Pipeline not found" in str(exc_info.value)


class TestGitLabClientSearchProjects:
    """Test searching for projects."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_projects_success(self, mock_gitlab_class):
        """Test searching for projects with a search term."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock search results
        mock_project1 = {
            "id": 1,
            "name": "Frontend Project",
            "path": "frontend-project",
            "path_with_namespace": "group/frontend-project",
            "description": "Frontend application",
            "visibility": "private",
            "web_url": "https://gitlab.example.com/group/frontend-project",
        }
        mock_project2 = {
            "id": 2,
            "name": "Frontend Library",
            "path": "frontend-lib",
            "path_with_namespace": "group/frontend-lib",
            "description": "Shared frontend library",
            "visibility": "internal",
            "web_url": "https://gitlab.example.com/group/frontend-lib",
        }

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.search.return_value = [mock_project1, mock_project2]
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.search_projects("frontend")

        # Verify search was called with correct scope
        from gitlab import const as gitlab_const

        mock_gitlab_instance.search.assert_called_once_with(
            gitlab_const.SearchScope.PROJECTS,
            "frontend",
            page=1,
            per_page=20,
        )

        # Verify result format
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "Frontend Project"
        assert result[1]["name"] == "Frontend Library"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_projects_with_pagination(self, mock_gitlab_class):
        """Test searching projects with custom pagination."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.search.return_value = []
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.search_projects("test", page=3, per_page=50)

        # Verify pagination parameters passed
        from gitlab import const as gitlab_const

        mock_gitlab_instance.search.assert_called_once_with(
            gitlab_const.SearchScope.PROJECTS,
            "test",
            page=3,
            per_page=50,
        )

        assert isinstance(result, list)
        assert len(result) == 0

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_projects_empty_results(self, mock_gitlab_class):
        """Test searching projects with no matches."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.search.return_value = []
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.search_projects("nonexistent-xyz-123")

        assert isinstance(result, list)
        assert len(result) == 0

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_projects_requires_authentication(self, mock_gitlab_class):
        """Test that search_projects checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.search_projects("test")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_projects_authentication_error(self, mock_gitlab_class):
        """Test search_projects handles authentication errors from API."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.search.side_effect = GitlabAuthenticationError("Invalid token")
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(AuthenticationError):
            client.search_projects("test")


class TestGitLabClientListProjectMembers:
    """Test listing project members."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_project_members_success(self, mock_gitlab_class):
        """Test listing project members."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock member data
        mock_member1 = Mock()
        mock_member1.id = 1
        mock_member1.username = "alice"
        mock_member1.name = "Alice Developer"
        mock_member1.access_level = 40  # Maintainer
        mock_member1.state = "active"
        mock_member1.web_url = "https://gitlab.example.com/alice"

        mock_member2 = Mock()
        mock_member2.id = 2
        mock_member2.username = "bob"
        mock_member2.name = "Bob Reviewer"
        mock_member2.access_level = 30  # Developer
        mock_member2.state = "active"
        mock_member2.web_url = "https://gitlab.example.com/bob"

        mock_members_manager = Mock()
        mock_members_manager.list.return_value = [mock_member1, mock_member2]

        mock_project = Mock()
        mock_project.members = mock_members_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_project_members(project_id=123)

        # Verify members list was called
        mock_members_manager.list.assert_called_once_with(page=1, per_page=20)

        # Verify result format
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["username"] == "alice"
        assert result[0]["access_level"] == 40
        assert result[1]["username"] == "bob"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_project_members_with_pagination(self, mock_gitlab_class):
        """Test listing project members with pagination."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_members_manager = Mock()
        mock_members_manager.list.return_value = []

        mock_project = Mock()
        mock_project.members = mock_members_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_project_members(project_id=123, page=2, per_page=50)

        # Verify pagination parameters
        mock_members_manager.list.assert_called_once_with(page=2, per_page=50)
        assert isinstance(result, list)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_project_members_project_path(self, mock_gitlab_class):
        """Test listing members with project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_members_manager = Mock()
        mock_members_manager.list.return_value = []

        mock_project = Mock()
        mock_project.members = mock_members_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_project_members(project_id="group/project")

        # Verify project fetched with path
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_project_members_requires_authentication(self, mock_gitlab_class):
        """Test that list_project_members checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_project_members(project_id=123)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_project_members_project_not_found(self, mock_gitlab_class):
        """Test list_project_members raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_project_members(project_id=999)

        assert "Project" in str(exc_info.value)


class TestGitLabClientGetProjectStatistics:
    """Test getting project statistics."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_statistics_success(self, mock_gitlab_class):
        """Test getting project statistics."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock project with statistics
        mock_statistics = Mock()
        mock_statistics.commit_count = 1523
        mock_statistics.storage_size = 1048576  # 1 MB
        mock_statistics.repository_size = 524288
        mock_statistics.wiki_size = 0
        mock_statistics.lfs_objects_size = 0
        mock_statistics.job_artifacts_size = 262144

        mock_project = Mock()
        mock_project.statistics = mock_statistics

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_project_statistics(project_id=123)

        # Verify project was fetched with statistics=True
        mock_gitlab_instance.projects.get.assert_called_once_with(123, statistics=True)

        # Verify result format
        assert isinstance(result, dict)
        assert result["commit_count"] == 1523
        assert result["storage_size"] == 1048576
        assert result["repository_size"] == 524288

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_statistics_project_path(self, mock_gitlab_class):
        """Test getting statistics with project path."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_statistics = Mock()
        mock_statistics.commit_count = 100

        mock_project = Mock()
        mock_project.statistics = mock_statistics

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.get_project_statistics(project_id="group/project")

        # Verify project fetched with path and statistics flag
        mock_gitlab_instance.projects.get.assert_called_once_with("group/project", statistics=True)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_statistics_requires_authentication(self, mock_gitlab_class):
        """Test that get_project_statistics checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_project_statistics(project_id=123)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_project_statistics_project_not_found(self, mock_gitlab_class):
        """Test get_project_statistics raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_project_statistics(project_id=999)

        assert "Project" in str(exc_info.value)


class TestGitLabClientListMilestones:
    """Test listing project milestones."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_milestones_success(self, mock_gitlab_class):
        """Test listing project milestones."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock milestone data
        mock_milestone1 = Mock()
        mock_milestone1.id = 1
        mock_milestone1.iid = 1
        mock_milestone1.title = "Version 1.0"
        mock_milestone1.description = "First major release"
        mock_milestone1.state = "active"
        mock_milestone1.due_date = "2025-12-31"
        mock_milestone1.web_url = "https://gitlab.example.com/group/project/-/milestones/1"

        mock_milestone2 = Mock()
        mock_milestone2.id = 2
        mock_milestone2.iid = 2
        mock_milestone2.title = "Version 2.0"
        mock_milestone2.description = "Second major release"
        mock_milestone2.state = "active"
        mock_milestone2.due_date = "2026-06-30"
        mock_milestone2.web_url = "https://gitlab.example.com/group/project/-/milestones/2"

        mock_milestones_manager = Mock()
        mock_milestones_manager.list.return_value = [mock_milestone1, mock_milestone2]

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_milestones(project_id=123)

        # Verify milestones list was called
        mock_milestones_manager.list.assert_called_once_with(page=1, per_page=20)

        # Verify result format
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["title"] == "Version 1.0"
        assert result[0]["state"] == "active"
        assert result[1]["title"] == "Version 2.0"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_milestones_with_state_filter(self, mock_gitlab_class):
        """Test listing milestones with state filter."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_milestones_manager = Mock()
        mock_milestones_manager.list.return_value = []

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_milestones(project_id=123, state="active")

        # Verify state filter passed
        mock_milestones_manager.list.assert_called_once_with(state="active", page=1, per_page=20)
        assert isinstance(result, list)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_milestones_requires_authentication(self, mock_gitlab_class):
        """Test that list_milestones checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_milestones(project_id=123)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_milestones_project_not_found(self, mock_gitlab_class):
        """Test list_milestones raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_milestones(project_id=999)

        assert "Project" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_success(self, mock_gitlab_class):
        """Test getting a milestone by milestone_id."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock milestone data
        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0"
        mock_milestone.description = "First major release"
        mock_milestone.state = "active"
        mock_milestone.due_date = "2025-12-31"
        mock_milestone.start_date = "2025-01-01"
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_milestone(project_id=123, milestone_id=1)

        # Verify milestones.get was called
        mock_milestones_manager.get.assert_called_once_with(1)

        # Verify result format
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert result["iid"] == 1
        assert result["title"] == "Version 1.0"
        assert result["description"] == "First major release"
        assert result["state"] == "active"
        assert result["due_date"] == "2025-12-31"
        assert result["start_date"] == "2025-01-01"
        assert result["web_url"] == "https://gitlab.example.com/group/project/-/milestones/1"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_requires_authentication(self, mock_gitlab_class):
        """Test that get_milestone checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_milestone(project_id=123, milestone_id=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_project_not_found(self, mock_gitlab_class):
        """Test get_milestone raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_milestone(project_id=999, milestone_id=1)

        assert "Project" in str(exc_info.value)


class TestGitLabClientGetMilestone:
    """Tests for GitLabClient.get_milestone."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_success(self, mock_gitlab_class):
        """Test getting a milestone by milestone_id."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock milestone data
        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0"
        mock_milestone.description = "First major release"
        mock_milestone.state = "active"
        mock_milestone.due_date = "2025-12-31"
        mock_milestone.start_date = "2025-01-01"
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_milestone(project_id=123, milestone_id=1)

        # Verify milestones.get was called
        mock_milestones_manager.get.assert_called_once_with(1)

        # Verify result format
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert result["iid"] == 1
        assert result["title"] == "Version 1.0"
        assert result["description"] == "First major release"
        assert result["state"] == "active"
        assert result["due_date"] == "2025-12-31"
        assert result["start_date"] == "2025-01-01"
        assert result["web_url"] == "https://gitlab.example.com/group/project/-/milestones/1"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_requires_authentication(self, mock_gitlab_class):
        """Test that get_milestone checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_milestone(project_id=123, milestone_id=1)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_project_not_found(self, mock_gitlab_class):
        """Test get_milestone raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_milestone(project_id=999, milestone_id=1)

        assert "Project" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_milestone_not_found(self, mock_gitlab_class):
        """Test get_milestone raises NotFoundError for missing milestone."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Milestone not found")
        error.response_code = 404

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.side_effect = error

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_milestone(project_id=123, milestone_id=999)

        assert "Milestone" in str(exc_info.value)


class TestGitLabClientCreateMilestone:
    """Tests for GitLabClient.create_milestone."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_milestone_success(self, mock_gitlab_class):
        """Test creating a milestone with all fields."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock created milestone
        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0"
        mock_milestone.description = "First major release"
        mock_milestone.state = "active"
        mock_milestone.due_date = "2025-12-31"
        mock_milestone.start_date = "2025-01-01"
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"

        mock_milestones_manager = Mock()
        mock_milestones_manager.create.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_milestone(
            project_id=123,
            title="Version 1.0",
            description="First major release",
            due_date="2025-12-31",
            start_date="2025-01-01",
        )

        # Verify milestones.create was called
        mock_milestones_manager.create.assert_called_once()
        call_args = mock_milestones_manager.create.call_args[0][0]
        assert call_args["title"] == "Version 1.0"
        assert call_args["description"] == "First major release"
        assert call_args["due_date"] == "2025-12-31"
        assert call_args["start_date"] == "2025-01-01"

        # Verify result format
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert result["title"] == "Version 1.0"
        assert result["description"] == "First major release"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_milestone_minimal(self, mock_gitlab_class):
        """Test creating a milestone with minimal fields (title only)."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0"
        mock_milestone.description = ""
        mock_milestone.state = "active"
        mock_milestone.due_date = None
        mock_milestone.start_date = None
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"

        mock_milestones_manager = Mock()
        mock_milestones_manager.create.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_milestone(project_id=123, title="Version 1.0")

        # Verify milestones.create was called with only title
        mock_milestones_manager.create.assert_called_once()
        call_args = mock_milestones_manager.create.call_args[0][0]
        assert call_args["title"] == "Version 1.0"
        assert "description" not in call_args
        assert "due_date" not in call_args
        assert "start_date" not in call_args

        # Verify result
        assert result["title"] == "Version 1.0"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_milestone_requires_authentication(self, mock_gitlab_class):
        """Test that create_milestone checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.create_milestone(project_id=123, title="Version 1.0")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_milestone_project_not_found(self, mock_gitlab_class):
        """Test create_milestone raises NotFoundError for missing project."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Project not found")
        error.response_code = 404

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = error
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.create_milestone(project_id=999, title="Version 1.0")

        assert "Project" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_milestone_invalid_title(self, mock_gitlab_class):
        """Test create_milestone validates title is not empty."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_milestone(project_id=123, title="")

        assert "title" in str(exc_info.value).lower()


class TestGitLabClientUpdateMilestone:
    """Tests for GitLabClient.update_milestone."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_milestone_success(self, mock_gitlab_class):
        """Test updating a milestone with all fields."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        # Mock milestone
        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0 Updated"
        mock_milestone.description = "Updated description"
        mock_milestone.state = "active"
        mock_milestone.due_date = "2026-01-31"
        mock_milestone.start_date = "2025-02-01"
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"
        mock_milestone.save = Mock()

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_milestone(
            project_id=123,
            milestone_id=1,
            title="Version 1.0 Updated",
            description="Updated description",
            due_date="2026-01-31",
            start_date="2025-02-01",
        )

        # Verify milestone was retrieved and saved
        mock_milestones_manager.get.assert_called_once_with(1)
        mock_milestone.save.assert_called_once()

        # Verify attributes were updated
        assert mock_milestone.title == "Version 1.0 Updated"
        assert mock_milestone.description == "Updated description"
        assert mock_milestone.due_date == "2026-01-31"
        assert mock_milestone.start_date == "2025-02-01"

        # Verify result format
        assert isinstance(result, dict)
        assert result["title"] == "Version 1.0 Updated"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_milestone_partial(self, mock_gitlab_class):
        """Test updating only some milestone fields."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Original Title"
        mock_milestone.description = "Original description"
        mock_milestone.state = "active"
        mock_milestone.due_date = "2025-12-31"
        mock_milestone.start_date = "2025-01-01"
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"
        mock_milestone.save = Mock()

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        # Update only title
        client.update_milestone(project_id=123, milestone_id=1, title="New Title")

        # Verify only title was changed
        assert mock_milestone.title == "New Title"
        # Other fields unchanged (still have original values from mock)
        mock_milestone.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_milestone_state_to_close(self, mock_gitlab_class):
        """Test closing a milestone by updating state."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        mock_milestone = Mock()
        mock_milestone.id = 1
        mock_milestone.iid = 1
        mock_milestone.title = "Version 1.0"
        mock_milestone.description = "First release"
        mock_milestone.state = "closed"
        mock_milestone.due_date = None
        mock_milestone.start_date = None
        mock_milestone.web_url = "https://gitlab.example.com/group/project/-/milestones/1"
        mock_milestone.save = Mock()

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.return_value = mock_milestone

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_milestone(project_id=123, milestone_id=1, state="close")

        # Verify state_event was set
        assert mock_milestone.state_event == "close"
        mock_milestone.save.assert_called_once()
        assert result["state"] == "closed"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_milestone_requires_authentication(self, mock_gitlab_class):
        """Test that update_milestone checks authentication."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.update_milestone(project_id=123, milestone_id=1, title="Updated")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_milestone_not_found(self, mock_gitlab_class):
        """Test update_milestone raises NotFoundError for missing milestone."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token-123",
        )

        error = GitlabGetError("Milestone not found")
        error.response_code = 404

        mock_milestones_manager = Mock()
        mock_milestones_manager.get.side_effect = error

        mock_project = Mock()
        mock_project.milestones = mock_milestones_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_milestone(project_id=123, milestone_id=999, title="Updated")

        assert "Milestone" in str(exc_info.value)


# ============================================================================
# Label Operations Tests
# ============================================================================


@patch("gitlab.Gitlab")
class TestGitLabClientListLabels:
    """Tests for list_labels method."""

    def test_list_labels_success_returns_labels(self, mock_gitlab_class: Mock) -> None:
        """Test listing labels returns list of labels."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        # Mock labels
        mock_label1 = Mock()
        mock_label1.id = 1
        mock_label1.name = "bug"
        mock_label1.color = "#FF0000"
        mock_label1.description = "Bug reports"
        mock_label1.priority = 1
        mock_label1.text_color = "#FFFFFF"

        mock_label2 = Mock()
        mock_label2.id = 2
        mock_label2.name = "feature"
        mock_label2.color = "#00FF00"
        mock_label2.description = "New features"
        mock_label2.priority = 2
        mock_label2.text_color = "#000000"

        # Mock project
        mock_project = Mock()
        mock_project.labels.list.return_value = [mock_label1, mock_label2]

        # Mock GitLab instance
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_labels(project_id=123)

        assert len(result) == 2
        assert result[0]["name"] == "bug"
        assert result[0]["color"] == "#FF0000"
        assert result[1]["name"] == "feature"
        assert result[1]["color"] == "#00FF00"
        mock_project.labels.list.assert_called_once()

    def test_list_labels_with_search_filters_labels(self, mock_gitlab_class: Mock) -> None:
        """Test listing labels with search parameter."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "bug"
        mock_label.color = "#FF0000"
        mock_label.description = "Bug reports"

        mock_project = Mock()
        mock_project.labels.list.return_value = [mock_label]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_labels(project_id=123, search="bug")

        assert len(result) == 1
        assert result[0]["name"] == "bug"
        mock_project.labels.list.assert_called_once_with(search="bug")

    def test_list_labels_empty_returns_empty_list(self, mock_gitlab_class: Mock) -> None:
        """Test listing labels when none exist returns empty list."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.labels.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_labels(project_id=123)

        assert result == []

    def test_list_labels_project_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test listing labels for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_labels(project_id=999)

        assert "Project not found" in str(exc_info.value)


@patch("gitlab.Gitlab")
class TestGitLabClientCreateLabel:
    """Tests for create_label method."""

    def test_create_label_success_returns_label(self, mock_gitlab_class: Mock) -> None:
        """Test creating a label returns the created label."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        # Mock created label
        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "bug"
        mock_label.color = "#FF0000"
        mock_label.description = "Bug reports"
        mock_label.priority = None
        mock_label.text_color = "#FFFFFF"

        # Mock project
        mock_project = Mock()
        mock_project.labels.create.return_value = mock_label

        # Mock GitLab instance
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_label(
            project_id=123, name="bug", color="#FF0000", description="Bug reports"
        )

        assert result["name"] == "bug"
        assert result["color"] == "#FF0000"
        assert result["description"] == "Bug reports"
        mock_project.labels.create.assert_called_once_with(
            {"name": "bug", "color": "#FF0000", "description": "Bug reports"}
        )

    def test_create_label_with_priority_includes_priority(self, mock_gitlab_class: Mock) -> None:
        """Test creating a label with priority."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "critical"
        mock_label.color = "#FF0000"
        mock_label.description = ""
        mock_label.priority = 1
        mock_label.text_color = "#FFFFFF"

        mock_project = Mock()
        mock_project.labels.create.return_value = mock_label

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_label(project_id=123, name="critical", color="#FF0000", priority=1)

        assert result["priority"] == 1
        mock_project.labels.create.assert_called_once_with(
            {"name": "critical", "color": "#FF0000", "priority": 1}
        )

    def test_create_label_minimal_only_required_fields(self, mock_gitlab_class: Mock) -> None:
        """Test creating a label with only required fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "feature"
        mock_label.color = "#00FF00"
        mock_label.description = ""
        mock_label.priority = None
        mock_label.text_color = "#000000"

        mock_project = Mock()
        mock_project.labels.create.return_value = mock_label

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_label(project_id=123, name="feature", color="#00FF00")

        assert result["name"] == "feature"
        assert result["color"] == "#00FF00"
        mock_project.labels.create.assert_called_once_with({"name": "feature", "color": "#00FF00"})

    def test_create_label_missing_name_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test creating a label without name raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_label(project_id=123, name="", color="#FF0000")

        assert "name" in str(exc_info.value).lower()

    def test_create_label_project_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test creating a label for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.create_label(project_id=999, name="bug", color="#FF0000")

        assert "Project not found" in str(exc_info.value)


@patch("gitlab.Gitlab")
class TestGitLabClientUpdateLabel:
    """Tests for update_label method."""

    def test_update_label_color_updates_successfully(self, mock_gitlab_class: Mock) -> None:
        """Test updating label color."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        # Mock label
        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "bug"
        mock_label.color = "#00FF00"  # Updated color
        mock_label.description = "Bug reports"
        mock_label.priority = None
        mock_label.text_color = "#000000"

        # Mock project
        mock_project = Mock()
        mock_project.labels.get.return_value = mock_label

        # Mock GitLab instance
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_label(project_id=123, label_id=1, color="#00FF00")

        assert result["color"] == "#00FF00"
        mock_label.save.assert_called_once()

    def test_update_label_name_updates_successfully(self, mock_gitlab_class: Mock) -> None:
        """Test updating label name."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "bug"
        mock_label.new_name = None
        mock_label.color = "#FF0000"
        mock_label.description = "Bug reports"
        mock_label.priority = None
        mock_label.text_color = "#FFFFFF"

        mock_project = Mock()
        mock_project.labels.get.return_value = mock_label

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_label(project_id=123, label_id=1, new_name="critical")

        assert mock_label.new_name == "critical"
        mock_label.save.assert_called_once()

    def test_update_label_multiple_fields_updates_all(self, mock_gitlab_class: Mock) -> None:
        """Test updating multiple label fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_label = Mock()
        mock_label.id = 1
        mock_label.name = "bug"
        mock_label.new_name = None
        mock_label.color = "#00FF00"
        mock_label.description = "Critical bugs"
        mock_label.priority = 1
        mock_label.text_color = "#000000"

        mock_project = Mock()
        mock_project.labels.get.return_value = mock_label

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_label(
            project_id=123,
            label_id=1,
            new_name="critical",
            color="#00FF00",
            description="Critical bugs",
            priority=1,
        )

        assert mock_label.new_name == "critical"
        assert mock_label.color == "#00FF00"
        assert mock_label.description == "Critical bugs"
        assert mock_label.priority == 1
        mock_label.save.assert_called_once()

    def test_update_label_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test updating non-existent label raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.labels.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_label(project_id=123, label_id=999, color="#FF0000")

        assert "Label not found" in str(exc_info.value)

    def test_update_label_project_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test updating label for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_label(project_id=999, label_id=1, color="#FF0000")

        assert "Project not found" in str(exc_info.value)


@patch("gitlab.Gitlab")
class TestGitLabClientDeleteLabel:
    """Tests for delete_label method."""

    def test_delete_label_success_deletes_label(self, mock_gitlab_class: Mock) -> None:
        """Test deleting a label successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        # Mock label
        mock_label = Mock()

        # Mock project
        mock_project = Mock()
        mock_project.labels.get.return_value = mock_label

        # Mock GitLab instance
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_label(project_id=123, label_id=1)

        mock_label.delete.assert_called_once()

    def test_delete_label_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test deleting non-existent label raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.labels.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_label(project_id=123, label_id=999)

        assert "Label not found" in str(exc_info.value)

    def test_delete_label_project_not_found_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test deleting label for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_label(project_id=999, label_id=1)

        assert "Project not found" in str(exc_info.value)


@pytest.fixture
def mock_gitlab_class():
    """Fixture for mocking Gitlab class for wiki tests."""
    with patch("gitlab_mcp.client.gitlab_client.Gitlab") as mock:
        yield mock


class TestListWikiPages:
    """Test list_wiki_pages method."""

    def test_list_wiki_pages_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing wiki pages successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        # Create mock wiki pages
        mock_page1 = Mock()
        mock_page1.slug = "home"
        mock_page1.title = "Home"

        mock_page2 = Mock()
        mock_page2.slug = "getting-started"
        mock_page2.title = "Getting Started"

        mock_project = Mock()
        mock_project.wikis.list.return_value = [mock_page1, mock_page2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_wiki_pages(project_id=123)

        assert len(result) == 2
        assert result[0]["slug"] == "home"
        assert result[0]["title"] == "Home"
        assert result[1]["slug"] == "getting-started"
        assert result[1]["title"] == "Getting Started"
        mock_project.wikis.list.assert_called_once_with(get_all=True)

    def test_list_wiki_pages_empty(self, mock_gitlab_class: Mock) -> None:
        """Test listing wiki pages when project has no wikis."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.wikis.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_wiki_pages(project_id=123)

        assert result == []
        mock_project.wikis.list.assert_called_once_with(get_all=True)

    def test_list_wiki_pages_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test listing wiki pages for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_wiki_pages(project_id=999)

        assert "Project not found" in str(exc_info.value)

    def test_list_wiki_pages_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing wiki pages with pagination parameters."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "test-page"
        mock_page.title = "Test Page"

        mock_project = Mock()
        mock_project.wikis.list.return_value = [mock_page]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_wiki_pages(project_id=123, page=2, per_page=50)

        assert len(result) == 1
        mock_project.wikis.list.assert_called_once_with(page=2, per_page=50)


class TestGetWikiPage:
    """Test get_wiki_page method."""

    def test_get_wiki_page_success(self, mock_gitlab_class: Mock) -> None:
        """Test getting a wiki page successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "home"
        mock_page.title = "Home"
        mock_page.content = "# Welcome\n\nThis is the home page."
        mock_page.format = "markdown"

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_wiki_page(project_id=123, slug="home")

        assert result["slug"] == "home"
        assert result["title"] == "Home"
        assert result["content"] == "# Welcome\n\nThis is the home page."
        assert result["format"] == "markdown"
        mock_project.wikis.get.assert_called_once_with("home")

    def test_get_wiki_page_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting non-existent wiki page raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.wikis.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_wiki_page(project_id=123, slug="nonexistent")

        assert "Wiki page not found" in str(exc_info.value)

    def test_get_wiki_page_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting wiki page for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_wiki_page(project_id=999, slug="home")

        assert "Project not found" in str(exc_info.value)

    def test_get_wiki_page_with_optional_fields(self, mock_gitlab_class: Mock) -> None:
        """Test getting wiki page with all optional fields present."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "api-docs"
        mock_page.title = "API Documentation"
        mock_page.content = "# API Docs\n\nDetailed API information."
        mock_page.format = "markdown"
        mock_page.encoding = "UTF-8"

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_wiki_page(project_id=123, slug="api-docs")

        assert result["slug"] == "api-docs"
        assert result["title"] == "API Documentation"
        assert result["content"] == "# API Docs\n\nDetailed API information."
        assert result["format"] == "markdown"
        assert result["encoding"] == "UTF-8"


class TestCreateWikiPage:
    """Test create_wiki_page method."""

    def test_create_wiki_page_success(self, mock_gitlab_class: Mock) -> None:
        """Test creating a wiki page successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_created_page = Mock()
        mock_created_page.slug = "new-page"
        mock_created_page.title = "New Page"
        mock_created_page.content = "# New Page\n\nContent here."
        mock_created_page.format = "markdown"

        mock_project = Mock()
        mock_project.wikis.create.return_value = mock_created_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_wiki_page(
            project_id=123, title="New Page", content="# New Page\n\nContent here."
        )

        assert result["slug"] == "new-page"
        assert result["title"] == "New Page"
        assert result["content"] == "# New Page\n\nContent here."
        assert result["format"] == "markdown"
        mock_project.wikis.create.assert_called_once_with(
            {
                "title": "New Page",
                "content": "# New Page\n\nContent here.",
            }
        )

    def test_create_wiki_page_with_format(self, mock_gitlab_class: Mock) -> None:
        """Test creating a wiki page with custom format."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_created_page = Mock()
        mock_created_page.slug = "rdoc-page"
        mock_created_page.title = "RDoc Page"
        mock_created_page.content = "Content in RDoc format"
        mock_created_page.format = "rdoc"

        mock_project = Mock()
        mock_project.wikis.create.return_value = mock_created_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_wiki_page(
            project_id=123, title="RDoc Page", content="Content in RDoc format", format="rdoc"
        )

        assert result["format"] == "rdoc"
        mock_project.wikis.create.assert_called_once_with(
            {
                "title": "RDoc Page",
                "content": "Content in RDoc format",
                "format": "rdoc",
            }
        )

    def test_create_wiki_page_missing_title_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test creating wiki page without title raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_wiki_page(project_id=123, title="", content="Content")

        assert "Title is required" in str(exc_info.value)

    def test_create_wiki_page_missing_content_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test creating wiki page without content raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_wiki_page(project_id=123, title="Title", content="")

        assert "Content is required" in str(exc_info.value)

    def test_create_wiki_page_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test creating wiki page for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.create_wiki_page(project_id=999, title="Page", content="Content")

        assert "Project not found" in str(exc_info.value)


class TestUpdateWikiPage:
    """Test update_wiki_page method."""

    def test_update_wiki_page_success(self, mock_gitlab_class: Mock) -> None:
        """Test updating a wiki page successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "home"
        mock_page.title = "Updated Home"
        mock_page.content = "# Updated Welcome\n\nThis is the updated home page."
        mock_page.format = "markdown"
        mock_page.save = Mock()

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_wiki_page(
            project_id=123,
            slug="home",
            title="Updated Home",
            content="# Updated Welcome\n\nThis is the updated home page.",
        )

        assert result["slug"] == "home"
        assert result["title"] == "Updated Home"
        assert result["content"] == "# Updated Welcome\n\nThis is the updated home page."
        assert mock_page.title == "Updated Home"
        assert mock_page.content == "# Updated Welcome\n\nThis is the updated home page."
        mock_page.save.assert_called_once()

    def test_update_wiki_page_partial_update(self, mock_gitlab_class: Mock) -> None:
        """Test updating only the content of a wiki page."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "docs"
        mock_page.title = "Documentation"
        mock_page.content = "Updated content only"
        mock_page.format = "markdown"
        mock_page.save = Mock()

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_wiki_page(
            project_id=123, slug="docs", content="Updated content only"
        )

        assert result["content"] == "Updated content only"
        assert mock_page.content == "Updated content only"
        mock_page.save.assert_called_once()

    def test_update_wiki_page_with_format(self, mock_gitlab_class: Mock) -> None:
        """Test updating wiki page format."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.slug = "api"
        mock_page.title = "API Docs"
        mock_page.content = "Content"
        mock_page.format = "rdoc"
        mock_page.save = Mock()

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_wiki_page(project_id=123, slug="api", format="rdoc")

        assert result["format"] == "rdoc"
        assert mock_page.format == "rdoc"
        mock_page.save.assert_called_once()

    def test_update_wiki_page_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test updating non-existent wiki page raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.wikis.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_wiki_page(project_id=123, slug="nonexistent", content="Updated")

        assert "Wiki page not found" in str(exc_info.value)

    def test_update_wiki_page_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test updating wiki page for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_wiki_page(project_id=999, slug="home", content="Updated")

        assert "Project not found" in str(exc_info.value)


class TestDeleteWikiPage:
    """Test delete_wiki_page method."""

    def test_delete_wiki_page_success(self, mock_gitlab_class: Mock) -> None:
        """Test deleting a wiki page successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_page = Mock()
        mock_page.delete = Mock()

        mock_project = Mock()
        mock_project.wikis.get.return_value = mock_page

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_wiki_page(project_id=123, slug="old-page")

        mock_page.delete.assert_called_once()

    def test_delete_wiki_page_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test deleting non-existent wiki page raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.wikis.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_wiki_page(project_id=123, slug="nonexistent")

        assert "Wiki page not found" in str(exc_info.value)

    def test_delete_wiki_page_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test deleting wiki page for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_wiki_page(project_id=999, slug="page")

        assert "Project not found" in str(exc_info.value)

    # -------------------------------------------------------------------------
    # Snippets Operations Tests
    # -------------------------------------------------------------------------

    def test_list_snippets_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing snippets successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet1 = Mock()
        mock_snippet1.id = 1
        mock_snippet1.title = "Test Snippet 1"
        mock_snippet1.file_name = "snippet1.py"
        mock_snippet1.visibility = "private"

        mock_snippet2 = Mock()
        mock_snippet2.id = 2
        mock_snippet2.title = "Test Snippet 2"
        mock_snippet2.file_name = "snippet2.js"
        mock_snippet2.visibility = "public"

        mock_project = Mock()
        mock_project.snippets.list.return_value = [mock_snippet1, mock_snippet2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_snippets(project_id=123)

        mock_gitlab_instance.projects.get.assert_called_once_with(123)
        mock_project.snippets.list.assert_called_once_with(page=1, per_page=20)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["title"] == "Test Snippet 1"
        assert result[1]["id"] == 2

    def test_list_snippets_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing snippets with pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.id = 1
        mock_snippet.title = "Test"

        mock_project = Mock()
        mock_project.snippets.list.return_value = [mock_snippet]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_snippets(project_id=123, page=2, per_page=50)

        mock_project.snippets.list.assert_called_once_with(page=2, per_page=50)
        assert len(result) == 1

    def test_list_snippets_empty(self, mock_gitlab_class: Mock) -> None:
        """Test listing snippets when none exist."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.snippets.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_snippets(project_id=123)

        assert result == []

    def test_list_snippets_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test listing snippets for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_snippets(project_id=999)

        assert "Project not found" in str(exc_info.value)

    def test_get_snippet_success(self, mock_gitlab_class: Mock) -> None:
        """Test getting a snippet successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.asdict.return_value = {
            "id": 1,
            "title": "Test Snippet",
            "file_name": "test.py",
            "description": "A test snippet",
            "visibility": "private",
            "content": "print('Hello World')",
        }

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_snippet(project_id=123, snippet_id=1)

        mock_gitlab_instance.projects.get.assert_called_once_with(123)
        mock_project.snippets.get.assert_called_once_with(1)
        assert result["id"] == 1
        assert result["title"] == "Test Snippet"
        assert result["content"] == "print('Hello World')"

    def test_get_snippet_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting non-existent snippet raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.snippets.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_snippet(project_id=123, snippet_id=999)

        assert "Snippet not found" in str(exc_info.value)

    def test_get_snippet_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting snippet for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_snippet(project_id=999, snippet_id=1)

        assert "Project not found" in str(exc_info.value)

    def test_get_snippet_with_all_fields(self, mock_gitlab_class: Mock) -> None:
        """Test getting snippet with all available fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.asdict.return_value = {
            "id": 1,
            "title": "Full Snippet",
            "file_name": "full.py",
            "description": "Complete snippet",
            "visibility": "public",
            "author": {"name": "John Doe"},
            "content": "def foo(): pass",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "web_url": "https://gitlab.example.com/snippets/1",
            "raw_url": "https://gitlab.example.com/snippets/1/raw",
        }

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_snippet(project_id=123, snippet_id=1)

        assert result["id"] == 1
        assert result["title"] == "Full Snippet"
        assert result["content"] == "def foo(): pass"
        assert result["author"] == {"name": "John Doe"}
        assert result["created_at"] == "2024-01-01T00:00:00Z"

    def test_create_snippet_success(self, mock_gitlab_class: Mock) -> None:
        """Test creating a snippet successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.asdict.return_value = {
            "id": 1,
            "title": "New Snippet",
            "file_name": "test.py",
            "content": "print('test')",
        }

        mock_project = Mock()
        mock_project.snippets.create.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_snippet(
            project_id=123, title="New Snippet", file_name="test.py", content="print('test')"
        )

        mock_project.snippets.create.assert_called_once_with(
            {"title": "New Snippet", "file_name": "test.py", "content": "print('test')"}
        )
        assert result["id"] == 1
        assert result["title"] == "New Snippet"

    def test_create_snippet_with_optional_fields(self, mock_gitlab_class: Mock) -> None:
        """Test creating snippet with optional fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.asdict.return_value = {"id": 1}

        mock_project = Mock()
        mock_project.snippets.create.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_snippet(
            project_id=123,
            title="Full Snippet",
            file_name="full.py",
            content="def foo(): pass",
            description="A complete snippet",
            visibility="public",
        )

        mock_project.snippets.create.assert_called_once_with(
            {
                "title": "Full Snippet",
                "file_name": "full.py",
                "content": "def foo(): pass",
                "description": "A complete snippet",
                "visibility": "public",
            }
        )
        assert result["id"] == 1

    def test_create_snippet_missing_title(self, mock_gitlab_class: Mock) -> None:
        """Test creating snippet without title raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_snippet(project_id=123, title="", file_name="test.py", content="test")

        assert "title" in str(exc_info.value).lower()

    def test_create_snippet_missing_file_name(self, mock_gitlab_class: Mock) -> None:
        """Test creating snippet without file_name raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_snippet(project_id=123, title="Test", file_name="", content="test")

        assert "file_name" in str(exc_info.value).lower()

    def test_create_snippet_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test creating snippet for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.create_snippet(project_id=999, title="Test", file_name="test.py", content="test")

        assert "Project not found" in str(exc_info.value)

    def test_update_snippet_success(self, mock_gitlab_class: Mock) -> None:
        """Test updating a snippet successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.id = 1
        mock_snippet.title = "Updated Snippet"
        mock_snippet.save = Mock()
        mock_snippet.asdict.return_value = {
            "id": 1,
            "title": "Updated Snippet",
        }

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.update_snippet(project_id=123, snippet_id=1, title="Updated Snippet")

        mock_snippet.save.assert_called_once()
        assert result["id"] == 1
        assert result["title"] == "Updated Snippet"

    def test_update_snippet_partial_update(self, mock_gitlab_class: Mock) -> None:
        """Test updating snippet with partial fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.id = 1
        mock_snippet.title = "Original"
        mock_snippet.file_name = "old.py"
        mock_snippet.save = Mock()

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_snippet(project_id=123, snippet_id=1, file_name="new.py")

        assert mock_snippet.file_name == "new.py"
        assert mock_snippet.title == "Original"  # unchanged
        mock_snippet.save.assert_called_once()

    def test_update_snippet_all_fields(self, mock_gitlab_class: Mock) -> None:
        """Test updating snippet with all fields."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.id = 1
        mock_snippet.save = Mock()

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_snippet(
            project_id=123,
            snippet_id=1,
            title="New Title",
            file_name="new.py",
            content="new content",
            description="new desc",
            visibility="public",
        )

        assert mock_snippet.title == "New Title"
        assert mock_snippet.file_name == "new.py"
        assert mock_snippet.content == "new content"
        assert mock_snippet.description == "new desc"
        assert mock_snippet.visibility == "public"
        mock_snippet.save.assert_called_once()

    def test_update_snippet_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test updating non-existent snippet raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.snippets.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_snippet(project_id=123, snippet_id=999, title="New")

        assert "Snippet not found" in str(exc_info.value)

    def test_update_snippet_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test updating snippet for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.update_snippet(project_id=999, snippet_id=1, title="New")

        assert "Project not found" in str(exc_info.value)

    def test_delete_snippet_success(self, mock_gitlab_class: Mock) -> None:
        """Test deleting a snippet successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_snippet = Mock()
        mock_snippet.delete = Mock()

        mock_project = Mock()
        mock_project.snippets.get.return_value = mock_snippet

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_snippet(project_id=123, snippet_id=1)

        mock_gitlab_instance.projects.get.assert_called_once_with(123)
        mock_project.snippets.get.assert_called_once_with(1)
        mock_snippet.delete.assert_called_once()

    def test_delete_snippet_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test deleting non-existent snippet raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.snippets.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_snippet(project_id=123, snippet_id=999)

        assert "Snippet not found" in str(exc_info.value)

    def test_delete_snippet_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test deleting snippet for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_snippet(project_id=999, snippet_id=1)

        assert "Project not found" in str(exc_info.value)


class TestReleases:
    """Tests for release operations."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_releases_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing project releases successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_release = Mock()
        mock_release.tag_name = "v1.0.0"
        mock_release.name = "Version 1.0.0"
        mock_release.description = "First stable release"
        mock_release.created_at = "2025-01-01T00:00:00Z"
        mock_release.released_at = "2025-01-01T12:00:00Z"

        mock_project = Mock()
        mock_project.releases.list.return_value = [mock_release]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        releases = client.list_releases(project_id=123)

        assert len(releases) == 1
        assert releases[0]["tag_name"] == "v1.0.0"
        assert releases[0]["name"] == "Version 1.0.0"
        assert releases[0]["description"] == "First stable release"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_releases_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing releases with custom pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.releases.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_releases(project_id=123, page=2, per_page=50)

        mock_project.releases.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_releases_empty(self, mock_gitlab_class: Mock) -> None:
        """Test listing releases returns empty list when no releases exist."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.releases.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        releases = client.list_releases(project_id=123)

        assert releases == []

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_releases_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test listing releases for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_releases(project_id=999)

        assert "Project not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_release_success(self, mock_gitlab_class: Mock) -> None:
        """Test getting a specific release by tag name."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_release = Mock()
        mock_release.tag_name = "v1.0.0"
        mock_release.name = "Version 1.0.0"
        mock_release.description = "First stable release"
        mock_release.created_at = "2025-01-01T00:00:00Z"
        mock_release.released_at = "2025-01-01T12:00:00Z"
        mock_release.author = {"name": "John Doe"}

        mock_project = Mock()
        mock_project.releases.get.return_value = mock_release

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        release = client.get_release(project_id=123, tag_name="v1.0.0")

        assert release["tag_name"] == "v1.0.0"
        assert release["name"] == "Version 1.0.0"
        assert release["description"] == "First stable release"
        mock_project.releases.get.assert_called_once_with("v1.0.0")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_release_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting non-existent release raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project = Mock()
        mock_project.releases.get.side_effect = GitlabGetError("Not found", response_code=404)

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_release(project_id=123, tag_name="v999.0.0")

        assert "Release not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_release_project_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting release for non-existent project raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.side_effect = GitlabGetError(
            "Not found", response_code=404
        )
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_release(project_id=999, tag_name="v1.0.0")

        assert "Project not found" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_release_success(self, mock_gitlab_class: Mock) -> None:
        """Test creating a release successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_release = Mock()
        mock_release.tag_name = "v1.0.0"
        mock_release.name = "Version 1.0.0"

        mock_project = Mock()
        mock_project.releases.create.return_value = mock_release

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.create_release(
            project_id=123, tag_name="v1.0.0", name="Version 1.0.0", description="Release notes"
        )

        mock_project.releases.create.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_release_validation_error(self, mock_gitlab_class: Mock) -> None:
        """Test creating release with missing required fields raises ValueError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.create_release(project_id=123, tag_name="", name="Version 1.0.0")

        assert "tag_name" in str(exc_info.value)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_update_release_success(self, mock_gitlab_class: Mock) -> None:
        """Test updating a release successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_release = Mock()
        mock_release.name = "Version 1.0.0"
        mock_release.description = "Updated notes"
        mock_release.save = Mock()

        mock_project = Mock()
        mock_project.releases.get.return_value = mock_release

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.update_release(project_id=123, tag_name="v1.0.0", name="Updated Name")

        assert mock_release.name == "Updated Name"
        mock_release.save.assert_called_once()

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_release_success(self, mock_gitlab_class: Mock) -> None:
        """Test deleting a release successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_releases_manager = Mock()
        mock_releases_manager.delete = Mock()

        mock_project = Mock()
        mock_project.releases = mock_releases_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.delete_release(project_id=123, tag_name="v1.0.0")

        mock_releases_manager.delete.assert_called_once_with("v1.0.0")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_delete_release_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test deleting non-existent release raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_releases_manager = Mock()
        mock_releases_manager.delete.side_effect = GitlabGetError("Not found", response_code=404)

        mock_project = Mock()
        mock_project.releases = mock_releases_manager

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.delete_release(project_id=123, tag_name="v999.0.0")

        assert "Release not found" in str(exc_info.value)


class TestGitLabClientGetUser:
    """Test GitLabClient.get_user() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_user_by_id_success(self, mock_gitlab_class: Mock) -> None:
        """Test getting a user by ID successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_user = Mock()
        mock_user.id = 42
        mock_user.username = "johndoe"
        mock_user.name = "John Doe"
        mock_user.email = "john@example.com"
        mock_user.state = "active"
        mock_user.web_url = "https://gitlab.example.com/johndoe"

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.get.return_value = mock_user
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_user(user_id=42)

        assert result["id"] == 42
        assert result["username"] == "johndoe"
        assert result["name"] == "John Doe"
        assert result["email"] == "john@example.com"
        assert result["state"] == "active"
        assert result["web_url"] == "https://gitlab.example.com/johndoe"
        mock_gitlab_instance.users.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_user_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that get_user requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_user(user_id=42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_user_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting non-existent user raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.get.side_effect = GitlabGetError("Not found", response_code=404)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_user(user_id=999)

        assert "User not found" in str(exc_info.value)


class TestGitLabClientSearchUsers:
    """Test GitLabClient.search_users() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_users_success(self, mock_gitlab_class: Mock) -> None:
        """Test searching users successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_user1 = Mock()
        mock_user1.id = 1
        mock_user1.username = "alice"
        mock_user1.name = "Alice Smith"

        mock_user2 = Mock()
        mock_user2.id = 2
        mock_user2.username = "alicia"
        mock_user2.name = "Alicia Johnson"

        mock_users_list = [mock_user1, mock_user2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.list.return_value = mock_users_list
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.search_users(search="ali")

        assert len(result) == 2
        assert result[0]["username"] == "alice"
        assert result[1]["username"] == "alicia"
        mock_gitlab_instance.users.list.assert_called_once_with(search="ali", page=1, per_page=20)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_users_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test searching users with custom pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.list.return_value = []
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.search_users(search="test", page=2, per_page=50)

        mock_gitlab_instance.users.list.assert_called_once_with(search="test", page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_users_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that search_users requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.search_users(search="test")

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_search_users_empty_search_raises_error(self, mock_gitlab_class: Mock) -> None:
        """Test that search_users validates search parameter is not empty."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(ValueError) as exc_info:
            client.search_users(search="")

        assert "Search query cannot be empty" in str(exc_info.value)


class TestGitLabClientListUserProjects:
    """Test GitLabClient.list_user_projects() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_user_projects_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing user projects successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_project1 = Mock()
        mock_project1.id = 1
        mock_project1.name = "Project 1"
        mock_project1.path_with_namespace = "user/project1"

        mock_project2 = Mock()
        mock_project2.id = 2
        mock_project2.name = "Project 2"
        mock_project2.path_with_namespace = "user/project2"

        mock_user = Mock()
        mock_user.projects.list.return_value = [mock_project1, mock_project2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.get.return_value = mock_user
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_user_projects(user_id=42)

        assert len(result) == 2
        assert result[0]["name"] == "Project 1"
        assert result[1]["path_with_namespace"] == "user/project2"
        mock_gitlab_instance.users.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_user_projects_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing user projects with pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_user = Mock()
        mock_user.projects.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.get.return_value = mock_user
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_user_projects(user_id=42, page=2, per_page=50)

        mock_user.projects.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_user_projects_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that list_user_projects requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_user_projects(user_id=42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_user_projects_user_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test listing projects for non-existent user raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.users.get.side_effect = GitlabGetError("Not found", response_code=404)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_user_projects(user_id=999)

        assert "User not found" in str(exc_info.value)


class TestGitLabClientListGroups:
    """Test GitLabClient.list_groups() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_groups_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing groups successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_group1 = Mock()
        mock_group1.id = 1
        mock_group1.name = "Group 1"
        mock_group1.full_path = "group1"

        mock_group2 = Mock()
        mock_group2.id = 2
        mock_group2.name = "Group 2"
        mock_group2.full_path = "group2"

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.list.return_value = [mock_group1, mock_group2]
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_groups()

        assert len(result) == 2
        assert result[0]["name"] == "Group 1"
        assert result[1]["full_path"] == "group2"
        mock_gitlab_instance.groups.list.assert_called_once_with(page=1, per_page=20)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_groups_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing groups with pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.list.return_value = []
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_groups(page=3, per_page=100)

        mock_gitlab_instance.groups.list.assert_called_once_with(page=3, per_page=100)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_groups_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that list_groups requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_groups()


class TestGitLabClientGetGroup:
    """Test GitLabClient.get_group() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_group_by_id_success(self, mock_gitlab_class: Mock) -> None:
        """Test getting a group by ID successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_group = Mock()
        mock_group.id = 42
        mock_group.name = "Engineering Team"
        mock_group.path = "engineering"
        mock_group.full_path = "company/engineering"
        mock_group.description = "Engineering group"
        mock_group.web_url = "https://gitlab.example.com/groups/engineering"
        mock_group.visibility = "private"

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.get.return_value = mock_group
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_group(group_id=42)

        assert result["id"] == 42
        assert result["name"] == "Engineering Team"
        assert result["full_path"] == "company/engineering"
        mock_gitlab_instance.groups.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_group_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that get_group requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.get_group(group_id=42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_group_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test getting non-existent group raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.get.side_effect = GitlabGetError("Not found", response_code=404)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.get_group(group_id=999)

        assert "Group not found" in str(exc_info.value)


class TestGitLabClientListGroupMembers:
    """Test GitLabClient.list_group_members() method."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_group_members_success(self, mock_gitlab_class: Mock) -> None:
        """Test listing group members successfully."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_member1 = Mock()
        mock_member1.id = 1
        mock_member1.username = "alice"
        mock_member1.name = "Alice Smith"
        mock_member1.access_level = 50  # Owner

        mock_member2 = Mock()
        mock_member2.id = 2
        mock_member2.username = "bob"
        mock_member2.name = "Bob Jones"
        mock_member2.access_level = 40  # Maintainer

        mock_group = Mock()
        mock_group.members.list.return_value = [mock_member1, mock_member2]

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.get.return_value = mock_group
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_group_members(group_id=42)

        assert len(result) == 2
        assert result[0]["username"] == "alice"
        assert result[0]["access_level"] == 50
        assert result[1]["username"] == "bob"
        mock_gitlab_instance.groups.get.assert_called_once_with(42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_group_members_with_pagination(self, mock_gitlab_class: Mock) -> None:
        """Test listing group members with pagination."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_group = Mock()
        mock_group.members.list.return_value = []

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.get.return_value = mock_group
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        client.list_group_members(group_id=42, page=2, per_page=50)

        mock_group.members.list.assert_called_once_with(page=2, per_page=50)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_group_members_requires_authentication(self, mock_gitlab_class: Mock) -> None:
        """Test that list_group_members requires authentication."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        client = GitLabClient(config)
        client._ensure_authenticated = Mock(side_effect=AuthenticationError("Not authenticated"))

        with pytest.raises(AuthenticationError):
            client.list_group_members(group_id=42)

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_group_members_group_not_found(self, mock_gitlab_class: Mock) -> None:
        """Test listing members for non-existent group raises NotFoundError."""
        config = GitLabConfig(gitlab_url="https://gitlab.example.com", gitlab_token="test-token")

        mock_gitlab_instance = Mock()
        mock_gitlab_instance.groups.get.side_effect = GitlabGetError("Not found", response_code=404)
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        with pytest.raises(NotFoundError) as exc_info:
            client.list_group_members(group_id=999)

        assert "Group not found" in str(exc_info.value)


class TestErrorMessageConstants:
    """Test error message constants for SonarQube S1192 compliance."""

    def test_err_auth_required_constant(self) -> None:
        """Test ERR_AUTH_REQUIRED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_AUTH_REQUIRED

        assert ERR_AUTH_REQUIRED == "Authentication required"
        assert isinstance(ERR_AUTH_REQUIRED, str)

    def test_err_not_authenticated_constant(self) -> None:
        """Test ERR_NOT_AUTHENTICATED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_NOT_AUTHENTICATED

        assert ERR_NOT_AUTHENTICATED == "Not authenticated"
        assert isinstance(ERR_NOT_AUTHENTICATED, str)

    def test_err_auth_failed_constant(self) -> None:
        """Test ERR_AUTH_FAILED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_AUTH_FAILED

        assert ERR_AUTH_FAILED == "Authentication failed"
        assert isinstance(ERR_AUTH_FAILED, str)

    def test_err_auth_failed_401_constant(self) -> None:
        """Test ERR_AUTH_FAILED_401 constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_AUTH_FAILED_401

        assert ERR_AUTH_FAILED_401 == "Authentication failed (401)"
        assert "401" in ERR_AUTH_FAILED_401

    def test_err_file_path_required_constant(self) -> None:
        """Test ERR_FILE_PATH_REQUIRED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_FILE_PATH_REQUIRED

        assert ERR_FILE_PATH_REQUIRED == "file_path is required and cannot be empty"
        assert "file_path" in ERR_FILE_PATH_REQUIRED

    def test_err_branch_required_constant(self) -> None:
        """Test ERR_BRANCH_REQUIRED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_BRANCH_REQUIRED

        assert ERR_BRANCH_REQUIRED == "branch is required and cannot be empty"
        assert "branch" in ERR_BRANCH_REQUIRED

    def test_err_commit_msg_required_constant(self) -> None:
        """Test ERR_COMMIT_MSG_REQUIRED constant has expected value."""
        from gitlab_mcp.client.gitlab_client import ERR_COMMIT_MSG_REQUIRED

        assert ERR_COMMIT_MSG_REQUIRED == "commit_message is required and cannot be empty"
        assert "commit_message" in ERR_COMMIT_MSG_REQUIRED

    def test_all_error_constants_are_strings(self) -> None:
        """Test that all error message constants are strings."""
        from gitlab_mcp.client.gitlab_client import (
            ERR_AUTH_FAILED,
            ERR_AUTH_FAILED_401,
            ERR_AUTH_REQUIRED,
            ERR_BRANCH_REQUIRED,
            ERR_COMMIT_MSG_REQUIRED,
            ERR_FILE_PATH_REQUIRED,
            ERR_NOT_AUTHENTICATED,
        )

        constants = [
            ERR_AUTH_REQUIRED,
            ERR_NOT_AUTHENTICATED,
            ERR_AUTH_FAILED,
            ERR_AUTH_FAILED_401,
            ERR_FILE_PATH_REQUIRED,
            ERR_BRANCH_REQUIRED,
            ERR_COMMIT_MSG_REQUIRED,
        ]

        for const in constants:
            assert isinstance(const, str)
            assert len(const) > 0


class TestGitLabClientMergeRequests:
    """Test merge request client methods."""

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_list_merge_requests(self, mock_gitlab_class):
        """Test listing merge requests."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token",
        )
        mock_mr = Mock()
        mock_mr.id = 1
        mock_mr.iid = 10
        mock_mr.title = "Test MR"
        mock_mr.author = Mock(username="user1", name="User One")
        
        mock_project = Mock()
        mock_project.mergerequests.list.return_value = [mock_mr]
        
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.list_merge_requests(123, state="opened")

        mock_project.mergerequests.list.assert_called_once_with(page=1, per_page=20, state="opened")
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["iid"] == 10
        assert result[0]["title"] == "Test MR"
        assert result[0]["author"]["username"] == "user1"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_get_merge_request(self, mock_gitlab_class):
        """Test getting details of a specific merge request."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token",
        )
        mock_mr = Mock()
        mock_mr.id = 1
        mock_mr.iid = 10
        mock_mr.title = "Test MR"
        
        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr
        
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.get_merge_request(123, 10)

        mock_project.mergerequests.get.assert_called_once_with(10)
        assert result["id"] == 1
        assert result["iid"] == 10
        assert result["title"] == "Test MR"

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_create_merge_request(self, mock_gitlab_class):
        """Test creating a merge request."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token",
        )
        mock_mr = Mock()
        mock_mr.id = 1
        mock_mr.iid = 10
        
        mock_project = Mock()
        mock_project.mergerequests.create.return_value = mock_mr
        
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.create_merge_request(123, "feature", "main", "New Feature", description="Desc")

        mock_project.mergerequests.create.assert_called_once_with({
            "source_branch": "feature",
            "target_branch": "main",
            "title": "New Feature",
            "description": "Desc",
        })
        assert result["id"] == 1
        assert result["iid"] == 10

    @patch("gitlab_mcp.client.gitlab_client.Gitlab")
    def test_approve_merge_request(self, mock_gitlab_class):
        """Test approving a merge request."""
        config = GitLabConfig(
            gitlab_url="https://gitlab.example.com",
            gitlab_token="test-token",
        )
        mock_mr = Mock()
        mock_mr.id = 1
        mock_mr.iid = 10
        mock_mr.approvals.approve.return_value = {"approved": True}
        
        mock_project = Mock()
        mock_project.mergerequests.get.return_value = mock_mr
        
        mock_gitlab_instance = Mock()
        mock_gitlab_instance.projects.get.return_value = mock_project
        mock_gitlab_class.return_value = mock_gitlab_instance

        client = GitLabClient(config)
        client._ensure_authenticated = Mock()
        client._gitlab = mock_gitlab_instance

        result = client.approve_merge_request(123, 10)

        mock_mr.approvals.approve.assert_called_once()
        assert result["approved"] is True
