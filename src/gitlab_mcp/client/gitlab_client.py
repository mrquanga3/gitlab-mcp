"""
GitLab Client wrapper.

This module provides a wrapper around the python-gitlab library, handling:
- Authentication and connection management
- Error conversion to custom exceptions
- Rate limit tracking
- Basic GitLab API operations
"""

from typing import Any

from gitlab import Gitlab
from gitlab import const as gitlab_const
from gitlab.exceptions import (
    GitlabAuthenticationError,
    GitlabGetError,
    GitlabHttpError,
)

from gitlab_mcp.client.exceptions import (
    AuthenticationError,
    GitLabAPIError,
    GitLabServerError,
    NotFoundError,
    PermissionError,
    RateLimitError,
)
from gitlab_mcp.config.settings import GitLabConfig

# Error message constants (SonarQube S1192 compliance)
ERR_AUTH_REQUIRED = "Authentication required"
ERR_NOT_AUTHENTICATED = "Not authenticated"
ERR_AUTH_FAILED = "Authentication failed"
ERR_AUTH_FAILED_401 = "Authentication failed (401)"

# Validation error message constants (SonarQube S1192 compliance)
ERR_FILE_PATH_REQUIRED = "file_path is required and cannot be empty"
ERR_BRANCH_REQUIRED = "branch is required and cannot be empty"
ERR_COMMIT_MSG_REQUIRED = "commit_message is required and cannot be empty"


class GitLabClient:
    """
    Wrapper around python-gitlab library.

    Provides a higher-level interface to GitLab API with:
    - Lazy connection (doesn't connect on __init__)
    - Automatic error conversion to custom exceptions
    - Rate limit tracking
    - Basic operations (get user, version, health check)

    Attributes:
        config: GitLab configuration settings
        _gitlab: Internal python-gitlab client (None until authenticated)
    """

    def __init__(self, config: GitLabConfig) -> None:
        """
        Initialize GitLab client with configuration.

        Args:
            config: GitLab configuration settings

        Note:
            Does NOT connect to GitLab immediately (lazy connection).
            Call authenticate() or use any method that calls _ensure_authenticated().
        """
        self.config = config
        self._gitlab: Gitlab | None = None

    def authenticate(self) -> None:
        """
        Authenticate and connect to GitLab.

        Creates the python-gitlab client and authenticates with the token.

        Raises:
            AuthenticationError: If authentication fails (invalid token, wrong URL, etc.)
            GitLabAPIError: If network or other errors occur
        """
        try:
            # Create the python-gitlab client
            self._gitlab = Gitlab(
                url=self.config.gitlab_url,
                private_token=self.config.gitlab_token.get_secret_value(),
                timeout=self.config.timeout,
            )

            # Authenticate
            self._gitlab.auth()

        except GitlabAuthenticationError as e:
            raise AuthenticationError("GitLab authentication failed") from e
        except OSError as e:
            raise GitLabAPIError(f"Network error: {str(e).lower()}") from e
        except Exception as e:
            raise GitLabAPIError(f"Unexpected error during authentication: {str(e)}") from e

    def _ensure_authenticated(self) -> None:
        """
        Ensure client is authenticated, authenticate if not.

        This is called by all API methods to ensure connection is established.
        """
        if self._gitlab is None:
            self.authenticate()

    def get_current_user(self) -> Any:
        """
        Get the currently authenticated user.

        Returns:
            User object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated or auth token invalid
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            return self._gitlab.user  # type: ignore
        except GitlabAuthenticationError as e:
            raise AuthenticationError("Failed to get current user") from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_version(self) -> dict[str, str]:
        """
        Get GitLab server version information.

        Returns:
            Dictionary with version info (e.g., {"version": "16.5.0", "revision": "abc123"})

        Raises:
            AuthenticationError: If not authenticated
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            return self._gitlab.version()  # type: ignore
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def health_check(self) -> bool:
        """
        Check if GitLab server is healthy and accessible.

        Returns:
            True if server is healthy, False otherwise
        """
        self._ensure_authenticated()

        try:
            self._gitlab.version()  # type: ignore
            return True
        except Exception:
            return False

    def get_instance_info(self) -> dict[str, str]:
        """
        Get GitLab instance information.

        Returns:
            Dictionary with instance URL and version:
            {
                "url": "https://gitlab.example.com",
                "version": "16.5.0"
            }

        Raises:
            AuthenticationError: If not authenticated
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            version_info = self._gitlab.version()  # type: ignore
            # version_info could be dict or other types, handle safely
            version = "unknown"
            if isinstance(version_info, dict):
                version = version_info.get("version", "unknown")
            return {
                "url": self.config.gitlab_url,
                "version": version,
            }
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_project(self, project_id: str | int) -> dict[str, Any]:
        """
        Get a single GitLab project by ID or path.

        Args:
            project_id: Project ID (int) or path (str) in format 'namespace/project'

        Returns:
            Project dictionary with project details

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project doesn't exist
            PermissionError: If user doesn't have permission to access project
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            return project.asdict()
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_project(
        self,
        name: str,
        path: str | None = None,
        namespace_id: int | None = None,
        description: str | None = None,
        visibility: str = "private",
        initialize_with_readme: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new GitLab project.

        Args:
            name: Project name (required)
            path: Project path/slug (optional, defaults to name if not provided)
            namespace_id: ID of the namespace/group to create project in (optional)
            description: Project description (optional)
            visibility: Project visibility level - 'private', 'internal', or 'public'
                       (default: 'private')
            initialize_with_readme: Whether to initialize project with a README.md
                                   (default: False)

        Returns:
            Dictionary with created project details

        Raises:
            AuthenticationError: If not authenticated
            PermissionError: If user doesn't have permission to create projects
            GitLabAPIError: If API call fails or validation error occurs
        """
        self._ensure_authenticated()

        try:
            # Build project creation parameters
            project_data: dict[str, Any] = {
                "name": name,
                "visibility": visibility,
                "initialize_with_readme": initialize_with_readme,
            }

            # Add optional parameters if provided
            if path is not None:
                project_data["path"] = path
            if namespace_id is not None:
                project_data["namespace_id"] = namespace_id
            if description is not None:
                project_data["description"] = description

            # Create the project
            project = self._gitlab.projects.create(project_data)  # type: ignore
            return project.asdict()

        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_branches(
        self,
        project_id: str | int,
        search: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List all branches in a repository.

        Args:
            project_id: Project ID (int) or path (str) in format 'namespace/project'
            search: Optional search term to filter branches by name
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20, max: 100)

        Returns:
            List of branch objects from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project doesn't exist
            PermissionError: If user doesn't have permission to access project
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            branches = project.branches.list(search=search, page=page, per_page=per_page)
            return branches
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_branch(self, project_id: str | int, branch_name: str) -> Any:
        """
        Get a specific branch from a repository.

        Args:
            project_id: Project ID (int) or path (str) in format 'namespace/project'
            branch_name: Name of the branch to retrieve

        Returns:
            Branch object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or branch doesn't exist
            PermissionError: If user doesn't have permission to access project
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            branch = project.branches.get(branch_name)
            return branch
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_file_content(
        self, project_id: str | int, file_path: str, ref: str | None = None
    ) -> Any:
        """
        Get the contents of a file from a repository.

        Args:
            project_id: Project ID (int) or path (str) in format 'namespace/project'
            file_path: Path to the file in the repository
            ref: Branch, tag, or commit SHA (default: project's default branch)

        Returns:
            File object from python-gitlab with content in base64 encoding

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or file doesn't exist
            PermissionError: If user doesn't have permission to access project
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            # Use project's default branch if ref not specified
            if ref is None:
                ref = project.default_branch
            file = project.files.get(file_path=file_path, ref=ref)
            return file
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_repository_tree(
        self,
        project_id: str | int,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        Get repository tree (files and directories).

        Args:
            project_id: Project ID or URL-encoded path
            path: Path inside repository (default: root)
            ref: Branch/tag/commit to get tree from (default: project's default branch)
            recursive: Get recursive tree (default: False)
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            List of tree entries (files and directories)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or path not found
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            # Use project's default branch if ref not specified
            if ref is None:
                ref = project.default_branch
            tree = project.repository_tree(
                path=path, ref=ref, recursive=recursive, page=page, per_page=per_page
            )
            return tree  # type: ignore
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_commit(self, project_id: str | int, commit_sha: str) -> Any:
        """
        Get details of a specific commit.

        Args:
            project_id: Project ID or URL-encoded path
            commit_sha: Commit SHA or reference

        Returns:
            Commit object with all details

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If commit not found
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            commit = project.commits.get(commit_sha)
            return commit
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_commits(
        self,
        project_id: str | int,
        ref: str | None = None,
        since: str | None = None,
        until: str | None = None,
        path: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List commits for a project.

        Args:
            project_id: Project ID or URL-encoded path
            ref: Branch/tag name to list commits from (uses default branch if not specified)
            since: Only commits after this date (ISO 8601 format)
            until: Only commits before this date (ISO 8601 format)
            path: Only commits affecting this file path
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20, max: 100)

        Returns:
            List of commit objects

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            # Use project's default branch if ref not specified
            if ref is None:
                ref = project.default_branch

            # Build query parameters
            kwargs: dict[str, Any] = {
                "ref_name": ref,
                "page": page,
                "per_page": per_page,
            }
            if since:
                kwargs["since"] = since
            if until:
                kwargs["until"] = until
            if path:
                kwargs["path"] = path

            commits = project.commits.list(**kwargs)
            return list(commits)
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def compare_branches(
        self,
        project_id: str | int,
        from_ref: str,
        to_ref: str,
        straight: bool = False,
    ) -> Any:
        """
        Compare two branches, tags, or commits.

        Args:
            project_id: Project ID or URL-encoded path
            from_ref: Source branch, tag, or commit SHA
            to_ref: Target branch, tag, or commit SHA
            straight: If true, compare refs directly (no merge base)

        Returns:
            Comparison object with commits and diffs

        Raises:
            NotFoundError: If project or refs not found
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            comparison = project.repository_compare(from_ref, to_ref, straight=straight)
            return comparison
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_branch(
        self,
        project_id: str | int,
        branch_name: str,
        ref: str,
    ) -> Any:
        """
        Create a new branch.

        Args:
            project_id: Project ID or URL-encoded path
            branch_name: Name for the new branch
            ref: Source branch, tag, or commit SHA

        Returns:
            Branch object

        Raises:
            NotFoundError: If project or ref not found
            ValidationError: If branch name invalid or already exists
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            branch = project.branches.create({"branch": branch_name, "ref": ref})
            return branch
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_branch(
        self,
        project_id: str | int,
        branch_name: str,
    ) -> None:
        """
        Delete a branch.

        Args:
            project_id: Project ID or URL-encoded path
            branch_name: Name of branch to delete

        Raises:
            NotFoundError: If project or branch not found
            PermissionError: If branch is protected or insufficient permissions
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            project.branches.delete(branch_name)
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_tags(
        self,
        project_id: str | int,
        search: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List repository tags.

        Args:
            project_id: Project ID or URL-encoded path
            search: Optional search pattern to filter tags
            page: Page number (default: 1)
            per_page: Results per page (default: 20, max: 100)

        Returns:
            List of tag objects

        Raises:
            NotFoundError: If project not found
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            kwargs: dict[str, Any] = {"page": page, "per_page": per_page}
            if search:
                kwargs["search"] = search
            tags = project.tags.list(**kwargs)
            return tags  # type: ignore
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_tag(
        self,
        project_id: str | int,
        tag_name: str,
    ) -> Any:
        """
        Get details of a specific tag.

        Args:
            project_id: Project ID or URL-encoded path
            tag_name: Name of the tag

        Returns:
            Tag object with details

        Raises:
            NotFoundError: If project or tag not found
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            tag = project.tags.get(tag_name)
            return tag
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_tag(
        self,
        project_id: str | int,
        tag_name: str,
        ref: str,
        message: str | None = None,
    ) -> Any:
        """
        Create a new tag.

        Args:
            project_id: Project ID or URL-encoded path
            tag_name: Name for the new tag
            ref: Source branch, tag, or commit SHA
            message: Optional tag message (creates annotated tag)

        Returns:
            Tag object

        Raises:
            NotFoundError: If project or ref not found
            ValidationError: If tag name invalid or already exists
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
        """
        self._ensure_authenticated()
        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            data = {"tag_name": tag_name, "ref": ref}
            if message:
                data["message"] = message
            tag = project.tags.create(data)
            return tag
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_projects(
        self,
        visibility: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """
        List accessible GitLab projects.

        Args:
            visibility: Filter by visibility (public, private, internal)
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20, max: 100)

        Returns:
            Dictionary with:
            {
                "projects": List of project dictionaries,
                "total": Total project count,
                "page": Current page number,
                "per_page": Results per page
            }

        Raises:
            AuthenticationError: If not authenticated
            GitLabAPIError: If API call fails
        """
        self._ensure_authenticated()

        try:
            # Get projects from GitLab
            projects = self._gitlab.projects.list(  # type: ignore
                visibility=visibility, page=page, per_page=per_page
            )

            # Convert project objects to dictionaries
            project_dicts = []
            for project in projects:
                project_dicts.append(
                    {
                        "id": project.id,
                        "name": project.name,
                        "path": project.path,
                        "visibility": project.visibility,
                        "web_url": project.web_url,
                        "description": getattr(project, "description", ""),
                    }
                )

            return {
                "projects": project_dicts,
                "total": len(project_dicts),
                "page": page,
                "per_page": per_page,
            }
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_REQUIRED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def search_code(
        self,
        search_term: str,
        project_id: str | int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search for code across repositories.

        Uses GitLab's code search API (blobs scope) to search for code patterns
        across all accessible repositories or within a specific project.

        Args:
            search_term: Code pattern to search for. Can include filters like
                        'filename:*.py' or 'extension:js' for more targeted searches.
            project_id: Optional project ID or path to limit search scope to a
                       specific project. If not provided, searches globally across
                       all accessible projects.
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20, max: 100)

        Returns:
            List of search result dictionaries. Each result contains:
            - basename: Filename without path
            - data: Preview of file content with context around match
            - path: Full file path in repository
            - filename: Full path (deprecated, use 'path' instead)
            - ref: Branch or tag name where match was found
            - startline: Line number where match begins
            - project_id: ID of the project containing the match

        Raises:
            NotFoundError: If project not found (when project_id specified)
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
            GitLabServerError: If GitLab server error occurs

        Example:
            # Global search
            results = client.search_code("def search_code")

            # Project-specific search
            results = client.search_code("TODO", project_id=123)

            # Search with filename filter
            results = client.search_code("class Config filename:*.py")
        """
        self._ensure_authenticated()

        try:
            if project_id is not None:
                # Project-level search
                project = self._gitlab.projects.get(project_id)  # type: ignore
                results = project.search(
                    gitlab_const.SearchScope.BLOBS,
                    search_term,
                    page=page,
                    per_page=per_page,
                )
            else:
                # Global search
                results = self._gitlab.search(  # type: ignore
                    gitlab_const.SearchScope.BLOBS,
                    search_term,
                    page=page,
                    per_page=per_page,
                )

            # python-gitlab search returns list of dicts
            return list(results)

        except GitlabAuthenticationError as e:
            raise AuthenticationError("Authentication required for code search") from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def search_projects(
        self,
        search_term: str,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search for projects across GitLab instance.

        Uses GitLab's search API (projects scope) to search for projects by name,
        description, or path.

        Args:
            search_term: Search query string to match against project names,
                        descriptions, and paths.
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20, max: 100)

        Returns:
            List of project dictionaries matching the search criteria.
            Each result contains standard project fields:
            - id: Project ID
            - name: Project name
            - path: Project path
            - path_with_namespace: Full project path including group
            - description: Project description
            - visibility: Visibility level (private/internal/public)
            - web_url: Project web URL

        Raises:
            AuthenticationError: If not authenticated
            GitLabAPIError: If API request fails
            GitLabServerError: If GitLab server error occurs

        Example:
            # Search for projects with "frontend" in name/description
            results = client.search_projects("frontend")

            # Search with pagination
            results = client.search_projects("api", page=2, per_page=50)
        """
        self._ensure_authenticated()

        try:
            # Global project search using PROJECTS scope
            results = self._gitlab.search(  # type: ignore
                gitlab_const.SearchScope.PROJECTS,
                search_term,
                page=page,
                per_page=per_page,
            )

            # python-gitlab search returns list of dicts
            return list(results)

        except GitlabAuthenticationError as e:
            raise AuthenticationError("Authentication required for project search") from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_project_members(
        self,
        project_id: str | int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List members of a project.

        Retrieves all members (users) who have access to the specified project,
        including their access levels and roles.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20, max: 100)

        Returns:
            List of member dictionaries. Each member contains:
            - id: User ID
            - username: User's username
            - name: User's full name
            - access_level: Numeric access level (10=Guest, 20=Reporter,
                           30=Developer, 40=Maintainer, 50=Owner)
            - state: User account state (active/blocked)
            - web_url: User profile URL

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            # List members by project ID
            members = client.list_project_members(123)

            # List members by project path
            members = client.list_project_members("group/project")

            # With pagination
            members = client.list_project_members(123, page=2, per_page=50)
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # List project members
            members = project.members.list(page=page, per_page=per_page)

            # Convert to dictionaries
            result = []
            for member in members:
                result.append(
                    {
                        "id": getattr(member, "id", 0),
                        "username": getattr(member, "username", ""),
                        "name": getattr(member, "name", ""),
                        "access_level": getattr(member, "access_level", 0),
                        "state": getattr(member, "state", ""),
                        "web_url": getattr(member, "web_url", ""),
                    }
                )

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_project_statistics(
        self,
        project_id: str | int,
    ) -> dict[str, Any]:
        """
        Get project statistics including storage, commits, and activity metrics.

        Retrieves detailed statistics for a project including commit count,
        storage sizes, and other metrics.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)

        Returns:
            Dictionary containing project statistics:
            - commit_count: Total number of commits
            - storage_size: Total storage used in bytes
            - repository_size: Repository size in bytes
            - wiki_size: Wiki storage size in bytes
            - lfs_objects_size: LFS objects size in bytes
            - job_artifacts_size: CI/CD artifacts size in bytes

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            # Get statistics by project ID
            stats = client.get_project_statistics(123)
            print(f"Commits: {stats['commit_count']}")

            # Get statistics by project path
            stats = client.get_project_statistics("group/project")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get project with statistics=True to include stats
            project = self._gitlab.projects.get(project_id, statistics=True)

            # Extract statistics
            statistics = getattr(project, "statistics", None)
            if not statistics:
                # Return empty stats if not available
                return {
                    "commit_count": 0,
                    "storage_size": 0,
                    "repository_size": 0,
                    "wiki_size": 0,
                    "lfs_objects_size": 0,
                    "job_artifacts_size": 0,
                }

            # Convert to dictionary
            return {
                "commit_count": getattr(statistics, "commit_count", 0),
                "storage_size": getattr(statistics, "storage_size", 0),
                "repository_size": getattr(statistics, "repository_size", 0),
                "wiki_size": getattr(statistics, "wiki_size", 0),
                "lfs_objects_size": getattr(statistics, "lfs_objects_size", 0),
                "job_artifacts_size": getattr(statistics, "job_artifacts_size", 0),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_milestones(
        self,
        project_id: str | int,
        state: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List milestones for a project.

        Retrieves all milestones for the specified project, optionally filtered by state.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            state: Optional milestone state filter ('active' or 'closed')
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20, max: 100)

        Returns:
            List of milestone dictionaries. Each milestone contains:
            - id: Milestone ID
            - iid: Internal ID (project-scoped)
            - title: Milestone title
            - description: Milestone description
            - state: State ('active' or 'closed')
            - due_date: Due date in YYYY-MM-DD format
            - web_url: Milestone web URL

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            # List all milestones
            milestones = client.list_milestones(123)

            # List active milestones only
            active_milestones = client.list_milestones(123, state="active")

            # With pagination
            milestones = client.list_milestones("group/project", page=2, per_page=50)
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Build list parameters
            list_params: dict[str, Any] = {"page": page, "per_page": per_page}
            if state is not None:
                list_params["state"] = state

            # List milestones
            milestones = project.milestones.list(**list_params)

            # Convert to dictionaries
            result = []
            for milestone in milestones:
                result.append(
                    {
                        "id": getattr(milestone, "id", 0),
                        "iid": getattr(milestone, "iid", 0),
                        "title": getattr(milestone, "title", ""),
                        "description": getattr(milestone, "description", ""),
                        "state": getattr(milestone, "state", ""),
                        "due_date": getattr(milestone, "due_date", None),
                        "web_url": getattr(milestone, "web_url", ""),
                    }
                )

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_milestone(
        self,
        project_id: str | int,
        milestone_id: int,
    ) -> dict[str, Any]:
        """
        Get a specific milestone by ID.

        Retrieves detailed information about a single milestone.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            milestone_id: Milestone ID or IID

        Returns:
            Milestone dictionary containing:
            - id: Milestone ID
            - iid: Internal ID (project-scoped)
            - title: Milestone title
            - description: Milestone description
            - state: State ('active' or 'closed')
            - due_date: Due date in YYYY-MM-DD format
            - start_date: Start date in YYYY-MM-DD format
            - web_url: Milestone web URL

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or milestone not found
            GitLabAPIError: For other API errors

        Example:
            milestone = client.get_milestone(123, milestone_id=5)
            print(f"Milestone: {milestone['title']}")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Get the milestone
            milestone = project.milestones.get(milestone_id)

            # Convert to dictionary
            return {
                "id": getattr(milestone, "id", 0),
                "iid": getattr(milestone, "iid", 0),
                "title": getattr(milestone, "title", ""),
                "description": getattr(milestone, "description", ""),
                "state": getattr(milestone, "state", ""),
                "due_date": getattr(milestone, "due_date", None),
                "start_date": getattr(milestone, "start_date", None),
                "web_url": getattr(milestone, "web_url", ""),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or milestone not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Milestone not found: milestone_id={milestone_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_milestone(
        self,
        project_id: str | int,
        title: str,
        description: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new milestone in a project.

        Creates a milestone with the specified title and optional metadata.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            title: Milestone title (required, must not be empty)
            description: Optional milestone description
            due_date: Optional due date in YYYY-MM-DD format
            start_date: Optional start date in YYYY-MM-DD format

        Returns:
            Milestone dictionary containing:
            - id: Milestone ID
            - iid: Internal ID (project-scoped)
            - title: Milestone title
            - description: Milestone description
            - state: State ('active' or 'closed')
            - due_date: Due date in YYYY-MM-DD format
            - start_date: Start date in YYYY-MM-DD format
            - web_url: Milestone web URL

        Raises:
            AuthenticationError: If not authenticated
            ValueError: If title is empty or invalid
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            milestone = client.create_milestone(
                123,
                title="Version 1.0",
                description="First major release",
                due_date="2025-12-31"
            )
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        # Validate title
        if not title or not title.strip():
            raise ValueError("Milestone title cannot be empty")

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Build create parameters
            create_params: dict[str, Any] = {"title": title}
            if description is not None:
                create_params["description"] = description
            if due_date is not None:
                create_params["due_date"] = due_date
            if start_date is not None:
                create_params["start_date"] = start_date

            # Create the milestone
            milestone = project.milestones.create(create_params)

            # Convert to dictionary
            return {
                "id": getattr(milestone, "id", 0),
                "iid": getattr(milestone, "iid", 0),
                "title": getattr(milestone, "title", ""),
                "description": getattr(milestone, "description", ""),
                "state": getattr(milestone, "state", ""),
                "due_date": getattr(milestone, "due_date", None),
                "start_date": getattr(milestone, "start_date", None),
                "web_url": getattr(milestone, "web_url", ""),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_milestone(
        self,
        project_id: str | int,
        milestone_id: int,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing milestone.

        Updates one or more fields of a milestone. Only provided fields will be updated.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            milestone_id: Milestone ID or IID
            title: Optional new milestone title
            description: Optional new milestone description
            due_date: Optional new due date in YYYY-MM-DD format
            start_date: Optional new start date in YYYY-MM-DD format
            state: Optional state event ('close' to close, 'activate' to reopen)

        Returns:
            Updated milestone dictionary containing:
            - id: Milestone ID
            - iid: Internal ID (project-scoped)
            - title: Milestone title
            - description: Milestone description
            - state: State ('active' or 'closed')
            - due_date: Due date in YYYY-MM-DD format
            - start_date: Start date in YYYY-MM-DD format
            - web_url: Milestone web URL

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or milestone not found
            GitLabAPIError: For other API errors

        Example:
            # Update title and due date
            milestone = client.update_milestone(
                123,
                milestone_id=5,
                title="Version 1.0 Final",
                due_date="2026-01-31"
            )

            # Close a milestone
            milestone = client.update_milestone(123, milestone_id=5, state="close")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Get the milestone
            milestone = project.milestones.get(milestone_id)

            # Update fields if provided
            if title is not None:
                milestone.title = title
            if description is not None:
                milestone.description = description
            if due_date is not None:
                milestone.due_date = due_date
            if start_date is not None:
                milestone.start_date = start_date
            if state is not None:
                milestone.state_event = state

            # Save the changes
            milestone.save()

            # Convert to dictionary
            return {
                "id": getattr(milestone, "id", 0),
                "iid": getattr(milestone, "iid", 0),
                "title": getattr(milestone, "title", ""),
                "description": getattr(milestone, "description", ""),
                "state": getattr(milestone, "state", ""),
                "due_date": getattr(milestone, "due_date", None),
                "start_date": getattr(milestone, "start_date", None),
                "web_url": getattr(milestone, "web_url", ""),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or milestone not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Milestone not found: milestone_id={milestone_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_labels(
        self,
        project_id: str | int,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List labels for a project.

        Retrieves all labels associated with a project, optionally filtered by search term.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            search: Optional search term to filter labels by name

        Returns:
            List of label dictionaries, each containing:
            - id: Label ID
            - name: Label name
            - color: Label color (hex code)
            - description: Label description
            - priority: Label priority (optional)
            - text_color: Text color for the label (hex code)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            labels = client.list_labels(project_id=123)
            bug_labels = client.list_labels(project_id=123, search="bug")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Build query parameters
            kwargs: dict[str, Any] = {}
            if search:
                kwargs["search"] = search

            # Get labels
            labels = project.labels.list(**kwargs)

            # Convert to dictionaries
            result = []
            for label in labels:
                result.append(
                    {
                        "id": getattr(label, "id", None),
                        "name": getattr(label, "name", ""),
                        "color": getattr(label, "color", ""),
                        "description": getattr(label, "description", ""),
                        "priority": getattr(label, "priority", None),
                        "text_color": getattr(label, "text_color", ""),
                    }
                )

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_label(
        self,
        project_id: str | int,
        name: str,
        color: str,
        description: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a new label in a project.

        Creates a label with the specified name and color. Optionally can include
        a description and priority.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            name: Label name (required, must not be empty)
            color: Label color in hex format (e.g., '#FF0000') or CSS color name
            description: Optional label description
            priority: Optional label priority (integer)

        Returns:
            Label dictionary containing:
            - id: Label ID
            - name: Label name
            - color: Label color (hex code)
            - description: Label description
            - priority: Label priority
            - text_color: Text color for the label (hex code)

        Raises:
            AuthenticationError: If not authenticated
            ValueError: If name is empty
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            label = client.create_label(
                project_id=123,
                name="bug",
                color="#FF0000",
                description="Bug reports",
                priority=1
            )
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        # Validate required fields
        if not name or not name.strip():
            raise ValueError("Label name cannot be empty")

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Build label data
            label_data: dict[str, Any] = {
                "name": name,
                "color": color,
            }

            if description is not None:
                label_data["description"] = description

            if priority is not None:
                label_data["priority"] = priority

            # Create the label
            label = project.labels.create(label_data)

            # Return label details
            return {
                "id": getattr(label, "id", None),
                "name": getattr(label, "name", ""),
                "color": getattr(label, "color", ""),
                "description": getattr(label, "description", ""),
                "priority": getattr(label, "priority", None),
                "text_color": getattr(label, "text_color", ""),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_label(
        self,
        project_id: str | int,
        label_id: int,
        new_name: str | None = None,
        color: str | None = None,
        description: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing label.

        Updates a label's properties. Only provided fields will be updated.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            label_id: Label ID
            new_name: New label name (optional)
            color: New label color in hex format (optional)
            description: New label description (optional)
            priority: New label priority (optional)

        Returns:
            Updated label dictionary containing:
            - id: Label ID
            - name: Label name
            - color: Label color (hex code)
            - description: Label description
            - priority: Label priority
            - text_color: Text color for the label (hex code)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or label not found
            GitLabAPIError: For other API errors

        Example:
            label = client.update_label(
                project_id=123,
                label_id=5,
                color="#0000FF",
                description="Updated description"
            )
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Get the label
            label = project.labels.get(label_id)

            # Update fields if provided
            if new_name is not None:
                label.new_name = new_name

            if color is not None:
                label.color = color

            if description is not None:
                label.description = description

            if priority is not None:
                label.priority = priority

            # Save the changes
            label.save()

            # Return updated label details
            return {
                "id": getattr(label, "id", None),
                "name": getattr(label, "name", ""),
                "color": getattr(label, "color", ""),
                "description": getattr(label, "description", ""),
                "priority": getattr(label, "priority", None),
                "text_color": getattr(label, "text_color", ""),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or label not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Label not found: label_id={label_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_label(
        self,
        project_id: str | int,
        label_id: int,
    ) -> None:
        """
        Delete a label from a project.

        Permanently deletes a label from the project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            label_id: Label ID

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or label not found
            GitLabAPIError: For other API errors

        Example:
            client.delete_label(project_id=123, label_id=5)
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)

            # Get the label
            label = project.labels.get(label_id)

            # Delete the label
            label.delete()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or label not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Label not found: label_id={label_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_wiki_pages(
        self,
        project_id: str | int,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        List wiki pages for a project.

        Args:
            project_id: The ID or URL-encoded path of the project
            page: Page number for pagination (default: None, uses get_all=True)
            per_page: Number of items per page (default: None, uses get_all=True)

        Returns:
            list[dict[str, Any]]: List of wiki page dictionaries with slug and title

        Raises:
            NotFoundError: If the project is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> pages = client.list_wiki_pages(project_id=123)
            >>> for page in pages:
            ...     print(f"{page['title']}: {page['slug']}")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)

            # Get wiki pages with optional pagination
            if page is not None and per_page is not None:
                wiki_pages = project.wikis.list(page=page, per_page=per_page)
            else:
                wiki_pages = project.wikis.list(get_all=True)

            # Convert to list of dicts
            result = []
            for wiki_page in wiki_pages:
                result.append(
                    {
                        "slug": getattr(wiki_page, "slug", ""),
                        "title": getattr(wiki_page, "title", ""),
                    }
                )

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_wiki_page(
        self,
        project_id: str | int,
        slug: str,
    ) -> dict[str, Any]:
        """
        Get a specific wiki page by slug.

        Args:
            project_id: The ID or URL-encoded path of the project
            slug: The slug (URL-encoded name) of the wiki page

        Returns:
            dict[str, Any]: Wiki page dictionary with slug, title, content, format, and encoding

        Raises:
            NotFoundError: If the project or wiki page is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> page = client.get_wiki_page(project_id=123, slug="home")
            >>> print(f"{page['title']}: {page['content']}")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            wiki_page = project.wikis.get(slug)

            # Convert to dict
            result = {
                "slug": getattr(wiki_page, "slug", ""),
                "title": getattr(wiki_page, "title", ""),
                "content": getattr(wiki_page, "content", ""),
                "format": getattr(wiki_page, "format", "markdown"),
            }

            # Add optional fields if present
            if hasattr(wiki_page, "encoding"):
                result["encoding"] = wiki_page.encoding

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or wiki page not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Wiki page not found: slug={slug}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_wiki_page(
        self,
        project_id: str | int,
        title: str,
        content: str,
        format: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new wiki page.

        Args:
            project_id: The ID or URL-encoded path of the project
            title: The title of the wiki page
            content: The content of the wiki page
            format: The format of the wiki page (markdown, rdoc, asciidoc, org)

        Returns:
            dict[str, Any]: Created wiki page dictionary with slug, title, content, and format

        Raises:
            ValueError: If title or content is empty
            NotFoundError: If the project is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> page = client.create_wiki_page(
            ...     project_id=123,
            ...     title="New Page",
            ...     content="# New Page\\n\\nContent here."
            ... )
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        # Validate required fields
        if not title or not title.strip():
            raise ValueError("Title is required and cannot be empty")
        if not content or not content.strip():
            raise ValueError("Content is required and cannot be empty")

        try:
            project = self._gitlab.projects.get(project_id)

            # Build wiki page data
            wiki_data = {
                "title": title,
                "content": content,
            }
            if format:
                wiki_data["format"] = format

            # Create the wiki page
            wiki_page = project.wikis.create(wiki_data)

            # Convert to dict
            result = {
                "slug": getattr(wiki_page, "slug", ""),
                "title": getattr(wiki_page, "title", ""),
                "content": getattr(wiki_page, "content", ""),
                "format": getattr(wiki_page, "format", "markdown"),
            }

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_wiki_page(
        self,
        project_id: str | int,
        slug: str,
        title: str | None = None,
        content: str | None = None,
        format: str | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing wiki page.

        Args:
            project_id: The ID or URL-encoded path of the project
            slug: The slug (URL-encoded name) of the wiki page
            title: The new title (optional)
            content: The new content (optional)
            format: The new format (optional)

        Returns:
            dict[str, Any]: Updated wiki page dictionary

        Raises:
            NotFoundError: If the project or wiki page is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> page = client.update_wiki_page(
            ...     project_id=123,
            ...     slug="home",
            ...     content="# Updated Content"
            ... )
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            wiki_page = project.wikis.get(slug)

            # Update only provided fields
            if title is not None:
                wiki_page.title = title
            if content is not None:
                wiki_page.content = content
            if format is not None:
                wiki_page.format = format

            # Save changes
            wiki_page.save()

            # Convert to dict
            result = {
                "slug": getattr(wiki_page, "slug", ""),
                "title": getattr(wiki_page, "title", ""),
                "content": getattr(wiki_page, "content", ""),
                "format": getattr(wiki_page, "format", "markdown"),
            }

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or wiki page not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Wiki page not found: slug={slug}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_wiki_page(
        self,
        project_id: str | int,
        slug: str,
    ) -> None:
        """
        Delete a wiki page.

        Args:
            project_id: The ID or URL-encoded path of the project
            slug: The slug (URL-encoded name) of the wiki page to delete

        Raises:
            NotFoundError: If the project or wiki page is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> client.delete_wiki_page(project_id=123, slug="old-page")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            wiki_page = project.wikis.get(slug)

            # Delete the wiki page
            wiki_page.delete()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or wiki page not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Wiki page not found: slug={slug}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    # -------------------------------------------------------------------------
    # Snippets Operations
    # -------------------------------------------------------------------------

    def list_snippets(
        self,
        project_id: str | int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """
        List all snippets in a project.

        Args:
            project_id: The ID or URL-encoded path of the project
            page: Page number for pagination (default: 1)
            per_page: Number of items per page (default: 20)

        Returns:
            List of snippet dictionaries with their details

        Raises:
            NotFoundError: If the project is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> snippets = client.list_snippets(project_id=123)
            >>> for snippet in snippets:
            ...     print(f"{snippet['id']}: {snippet['title']}")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            snippets = project.snippets.list(page=page, per_page=per_page)

            result = []
            for snippet in snippets:
                snippet_dict = {
                    "id": getattr(snippet, "id", None),
                    "title": getattr(snippet, "title", None),
                    "file_name": getattr(snippet, "file_name", None),
                    "description": getattr(snippet, "description", None),
                    "visibility": getattr(snippet, "visibility", None),
                    "author": getattr(snippet, "author", None),
                    "created_at": getattr(snippet, "created_at", None),
                    "updated_at": getattr(snippet, "updated_at", None),
                    "web_url": getattr(snippet, "web_url", None),
                    "raw_url": getattr(snippet, "raw_url", None),
                }
                result.append(snippet_dict)

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_snippet(
        self,
        project_id: str | int,
        snippet_id: int,
    ) -> dict:
        """
        Get details of a specific snippet.

        Args:
            project_id: The ID or URL-encoded path of the project
            snippet_id: The ID of the snippet

        Returns:
            Dictionary containing snippet details including content

        Raises:
            NotFoundError: If the project or snippet is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> snippet = client.get_snippet(project_id=123, snippet_id=1)
            >>> print(f"Title: {snippet['title']}")
            >>> print(f"Content: {snippet['content']}")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            snippet = project.snippets.get(snippet_id)

            # Use asdict() to properly serialize the snippet object
            return snippet.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or snippet not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Snippet not found: snippet_id={snippet_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_snippet(
        self,
        project_id: str | int,
        title: str,
        file_name: str,
        content: str,
        description: str | None = None,
        visibility: str | None = None,
    ) -> dict:
        """
        Create a new snippet in a project.

        Args:
            project_id: The ID or URL-encoded path of the project
            title: The title of the snippet
            file_name: The name of the file
            content: The content of the snippet
            description: Optional description of the snippet
            visibility: Optional visibility level (private, internal, public)

        Returns:
            Dictionary containing the created snippet details

        Raises:
            ValueError: If required fields are missing or invalid
            NotFoundError: If the project is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> snippet = client.create_snippet(
            ...     project_id=123,
            ...     title="My Snippet",
            ...     file_name="example.py",
            ...     content="print('Hello')"
            ... )
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        # Validate required fields
        if not title or not title.strip():
            raise ValueError("Snippet title is required and cannot be empty")
        if not file_name or not file_name.strip():
            raise ValueError("Snippet file_name is required and cannot be empty")

        try:
            project = self._gitlab.projects.get(project_id)

            # Build snippet data
            snippet_data = {
                "title": title,
                "file_name": file_name,
                "content": content,
            }

            # Add optional fields if provided
            if description is not None:
                snippet_data["description"] = description
            if visibility is not None:
                snippet_data["visibility"] = visibility

            # Create the snippet
            snippet = project.snippets.create(snippet_data)

            # Use asdict() to properly serialize the snippet object
            return snippet.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_snippet(
        self,
        project_id: str | int,
        snippet_id: int,
        title: str | None = None,
        file_name: str | None = None,
        content: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
    ) -> dict:
        """
        Update an existing snippet.

        Args:
            project_id: The ID or URL-encoded path of the project
            snippet_id: The ID of the snippet to update
            title: Optional new title
            file_name: Optional new file name
            content: Optional new content
            description: Optional new description
            visibility: Optional new visibility level (private, internal, public)

        Returns:
            Dictionary containing the updated snippet details

        Raises:
            NotFoundError: If the project or snippet is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> snippet = client.update_snippet(
            ...     project_id=123,
            ...     snippet_id=1,
            ...     title="Updated Title"
            ... )
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            snippet = project.snippets.get(snippet_id)

            # Update only provided fields (partial update)
            if title is not None:
                snippet.title = title
            if file_name is not None:
                snippet.file_name = file_name
            if content is not None:
                snippet.content = content  # type: ignore[assignment]
            if description is not None:
                snippet.description = description
            if visibility is not None:
                snippet.visibility = visibility

            # Save changes
            snippet.save()

            # Use asdict() to properly serialize the updated snippet object
            return snippet.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or snippet not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Snippet not found: snippet_id={snippet_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_snippet(
        self,
        project_id: str | int,
        snippet_id: int,
    ) -> None:
        """
        Delete a snippet.

        Args:
            project_id: The ID or URL-encoded path of the project
            snippet_id: The ID of the snippet to delete

        Raises:
            NotFoundError: If the project or snippet is not found
            GitLabAPIError: If the GitLab API returns an error

        Example:
            >>> client.delete_snippet(project_id=123, snippet_id=1)
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            snippet = project.snippets.get(snippet_id)

            # Delete the snippet
            snippet.delete()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to determine if it's project or snippet not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Snippet not found: snippet_id={snippet_id}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: project_id={project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_issues(
        self,
        project_id: str | int,
        state: str | None = None,
        labels: list[str] | None = None,
        milestone: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List issues for a project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            state: Filter by state ('opened', 'closed', 'all')
            labels: Filter by labels (list of label names)
            milestone: Filter by milestone title
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20)

        Returns:
            List of issue objects from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issues = client.list_issues(project_id=123, state='opened')
            >>> for issue in issues:
            ...     print(f"#{issue.iid}: {issue.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build filter parameters
            filters: dict[str, Any] = {
                "page": page,
                "per_page": per_page,
            }

            if state is not None:
                filters["state"] = state

            if labels is not None:
                filters["labels"] = labels

            if milestone is not None:
                filters["milestone"] = milestone

            # List issues
            issues = project.issues.list(**filters)

            return list(issues)

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_issue(
        self,
        project_id: str | int,
        issue_iid: int,
    ) -> Any:
        """
        Get a specific issue by its IID (internal ID).

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID, not the global ID)

        Returns:
            Issue object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issue = client.get_issue(project_id=123, issue_iid=42)
            >>> print(f"#{issue.iid}: {issue.title}")
            >>> print(f"State: {issue.state}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue by IID
            issue = project.issues.get(issue_iid)

            return issue

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Could be project not found or issue not found
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_issue(
        self,
        project_id: str | int,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
        milestone_id: int | None = None,
    ) -> Any:
        """
        Create a new issue in a project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            title: Issue title (required)
            description: Issue description (optional)
            labels: List of label names to apply (optional)
            assignee_ids: List of user IDs to assign (optional)
            milestone_id: Milestone ID to associate (optional)

        Returns:
            Created issue object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issue = client.create_issue(
            ...     project_id=123,
            ...     title="Bug in login",
            ...     description="Users cannot login",
            ...     labels=["bug", "critical"]
            ... )
            >>> print(f"Created issue #{issue.iid}: {issue.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build issue data dictionary
            issue_data: dict[str, Any] = {
                "title": title,
            }

            # Add optional fields if provided
            if description is not None:
                issue_data["description"] = description

            if labels is not None:
                issue_data["labels"] = labels

            if assignee_ids is not None:
                issue_data["assignee_ids"] = assignee_ids

            if milestone_id is not None:
                issue_data["milestone_id"] = milestone_id

            # Create the issue
            issue = project.issues.create(issue_data)

            return issue

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_issue(
        self,
        project_id: str | int,
        issue_iid: int,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
        milestone_id: int | None = None,
        state_event: str | None = None,
    ) -> Any:
        """
        Update an existing issue.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID within the project)
            title: New issue title (optional)
            description: New issue description (optional)
            labels: New list of label names (optional)
            assignee_ids: New list of user IDs to assign (optional)
            milestone_id: New milestone ID (optional)
            state_event: State event: 'close' or 'reopen' (optional)

        Returns:
            Updated issue object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issue = client.update_issue(
            ...     project_id=123,
            ...     issue_iid=42,
            ...     title="Updated title",
            ...     labels=["bug", "high-priority"]
            ... )
            >>> print(f"Updated issue #{issue.iid}: {issue.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue
            issue = project.issues.get(issue_iid)

            # Update fields that were provided
            if title is not None:
                issue.title = title

            if description is not None:
                issue.description = description

            if labels is not None:
                issue.labels = labels

            if assignee_ids is not None:
                issue.assignee_ids = assignee_ids

            if milestone_id is not None:
                issue.milestone_id = milestone_id

            if state_event is not None:
                issue.state_event = state_event

            # Save changes
            issue.save()

            return issue

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def close_issue(
        self,
        project_id: str | int,
        issue_iid: int,
    ) -> Any:
        """
        Close an issue.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID within the project)

        Returns:
            Closed issue object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issue = client.close_issue(project_id=123, issue_iid=42)
            >>> print(f"Closed issue #{issue.iid}: {issue.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue
            issue = project.issues.get(issue_iid)

            # Set state_event to close
            issue.state_event = "close"

            # Save changes
            issue.save()

            return issue

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def reopen_issue(
        self,
        project_id: str | int,
        issue_iid: int,
    ) -> Any:
        """
        Reopen a closed issue.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID within the project)

        Returns:
            Reopened issue object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> issue = client.reopen_issue(project_id=123, issue_iid=42)
            >>> print(f"Reopened issue #{issue.iid}: {issue.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue
            issue = project.issues.get(issue_iid)

            # Set state_event to reopen
            issue.state_event = "reopen"

            # Save changes
            issue.save()

            return issue

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def add_issue_comment(
        self,
        project_id: str | int,
        issue_iid: int,
        body: str,
    ) -> Any:
        """
        Add a comment (note) to an issue.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID within the project)
            body: Comment text (required)

        Returns:
            Created note object from python-gitlab

        Raises:
            ValueError: If body is empty
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> note = client.add_issue_comment(
            ...     project_id=123,
            ...     issue_iid=42,
            ...     body="This is a comment"
            ... )
            >>> print(f"Added comment #{note.id}")
        """
        # Validate body is not empty
        if not body or not body.strip():
            raise ValueError("Comment body cannot be empty")

        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue
            issue = project.issues.get(issue_iid)

            # Create the note/comment
            note = issue.notes.create({"body": body})

            return note

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_issue_comments(
        self,
        project_id: str | int,
        issue_iid: int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List comments (notes) on an issue.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            issue_iid: Issue IID (internal ID within the project)
            page: Page number for pagination (default: 1)
            per_page: Number of items per page (default: 20)

        Returns:
            List of note objects from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or issue not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> notes = client.list_issue_comments(project_id=123, issue_iid=42)
            >>> for note in notes:
            ...     print(f"Comment #{note.id}: {note.body}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the issue
            issue = project.issues.get(issue_iid)

            # List notes with pagination
            notes = issue.notes.list(page=page, per_page=per_page)

            return notes

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or issue not found: project={project_id}, issue_iid={issue_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    # =============================================================================
    # Repository Files Operations
    # =============================================================================

    def create_file(
        self,
        project_id: str | int,
        file_path: str,
        branch: str,
        content: str,
        commit_message: str,
        author_email: str | None = None,
        author_name: str | None = None,
        encoding: str = "text",
    ) -> Any:
        """
        Create a new file in a repository.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            file_path: Path to the file in the repository (e.g., "docs/README.md")
            branch: Branch name to commit to (e.g., "main", "develop")
            content: File content (text or base64-encoded)
            commit_message: Commit message describing the change
            author_email: Optional author email (defaults to authenticated user)
            author_name: Optional author name (defaults to authenticated user)
            encoding: Content encoding - "text" or "base64" (default: "text")

        Returns:
            File object from python-gitlab with attributes:
            - file_path: Path to the created file
            - content: File content (base64 encoded)
            - commit_id: SHA of the commit that created the file

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            ValidationError: If required parameters are invalid
            GitLabAPIError: If the API request fails
        """
        self._ensure_authenticated()

        # Validate required parameters
        if not file_path or not file_path.strip():
            raise ValueError(ERR_FILE_PATH_REQUIRED)
        if not branch or not branch.strip():
            raise ValueError(ERR_BRANCH_REQUIRED)
        if not commit_message or not commit_message.strip():
            raise ValueError(ERR_COMMIT_MSG_REQUIRED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Prepare file data
            file_data = {
                "file_path": file_path,
                "branch": branch,
                "content": content,
                "commit_message": commit_message,
                "encoding": encoding,
            }

            # Add optional author information if provided
            if author_email:
                file_data["author_email"] = author_email
            if author_name:
                file_data["author_name"] = author_name

            # Create the file
            created_file = project.files.create(file_data)

            return created_file

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_file(
        self,
        project_id: str | int,
        file_path: str,
        branch: str,
        content: str,
        commit_message: str,
        author_email: str | None = None,
        author_name: str | None = None,
        encoding: str = "text",
    ) -> Any:
        """
        Update an existing file in a repository.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            file_path: Path to the file in the repository (e.g., "docs/README.md")
            branch: Branch name to commit to (e.g., "main", "develop")
            content: New file content (text or base64-encoded)
            commit_message: Commit message describing the change
            author_email: Optional author email (defaults to authenticated user)
            author_name: Optional author name (defaults to authenticated user)
            encoding: Content encoding - "text" or "base64" (default: "text")

        Returns:
            File object from python-gitlab with attributes:
            - file_path: Path to the updated file
            - content: Updated file content (base64 encoded)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or file not found
            ValidationError: If required parameters are invalid
            GitLabAPIError: If the API request fails
        """
        self._ensure_authenticated()

        # Validate required parameters
        if not file_path or not file_path.strip():
            raise ValueError(ERR_FILE_PATH_REQUIRED)
        if not branch or not branch.strip():
            raise ValueError(ERR_BRANCH_REQUIRED)
        if not commit_message or not commit_message.strip():
            raise ValueError(ERR_COMMIT_MSG_REQUIRED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the existing file
            file = project.files.get(file_path=file_path, ref=branch)

            # Update the file content and encoding
            file.content = content
            file.encoding = encoding

            # Prepare save parameters
            save_params = {
                "branch": branch,
                "commit_message": commit_message,
            }

            # Add optional author information if provided
            if author_email:
                save_params["author_email"] = author_email
            if author_name:
                save_params["author_name"] = author_name

            # Save the file
            file.save(**save_params)

            return file

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or file not found: project={project_id}, file={file_path}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_file(
        self,
        project_id: str | int,
        file_path: str,
        branch: str,
        commit_message: str,
        author_email: str | None = None,
        author_name: str | None = None,
    ) -> None:
        """
        Delete a file from a repository.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            file_path: Path to the file in the repository (e.g., "docs/old.md")
            branch: Branch name to commit to (e.g., "main", "develop")
            commit_message: Commit message describing the deletion
            author_email: Optional author email (defaults to authenticated user)
            author_name: Optional author name (defaults to authenticated user)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or file not found
            ValidationError: If required parameters are invalid
            GitLabAPIError: If the API request fails
        """
        self._ensure_authenticated()

        # Validate required parameters
        if not file_path or not file_path.strip():
            raise ValueError(ERR_FILE_PATH_REQUIRED)
        if not branch or not branch.strip():
            raise ValueError(ERR_BRANCH_REQUIRED)
        if not commit_message or not commit_message.strip():
            raise ValueError(ERR_COMMIT_MSG_REQUIRED)

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Prepare delete parameters
            delete_params = {
                "file_path": file_path,
                "branch": branch,
                "commit_message": commit_message,
            }

            # Add optional author information if provided
            if author_email:
                delete_params["author_email"] = author_email
            if author_name:
                delete_params["author_name"] = author_name

            # Delete the file
            project.files.delete(**delete_params)

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or file not found: project={project_id}, file={file_path}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    # ========================================
    # Merge Request Operations
    # ========================================

    def list_merge_requests(
        self,
        project_id: str | int,
        state: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List merge requests for a project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            state: Filter by state ('opened', 'closed', 'merged', 'all')
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20)

        Returns:
            List of merge request dictionaries

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mrs = client.list_merge_requests(project_id=123, state='opened')
            >>> for mr in mrs:
            ...     print(f"!{mr['iid']}: {mr['title']}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build filter parameters
            filters: dict[str, Any] = {
                "page": page,
                "per_page": per_page,
            }

            if state is not None:
                filters["state"] = state

            # List merge requests and convert to dicts
            merge_requests = project.mergerequests.list(**filters)
            return [mr.asdict() for mr in merge_requests]

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project with ID {project_id} not found") from e
            raise self._convert_exception(e) from e
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_FAILED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
    ) -> Any:
        """
        Get a specific merge request by its IID.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request internal ID (IID)

        Returns:
            Merge request object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mr = client.get_merge_request(project_id=123, mr_iid=42)
            >>> print(f"!{mr.iid}: {mr.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            merge_request = project.mergerequests.get(mr_iid)
            return merge_request.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Check if project exists first
                try:
                    self._gitlab.projects.get(project_id)  # type: ignore
                    # Project exists, so MR not found
                    raise NotFoundError(
                        f"Merge request with IID {mr_iid} not found in project {project_id}"
                    ) from e
                except GitlabGetError:
                    # Project not found
                    raise NotFoundError(f"Project with ID {project_id} not found") from e
            raise self._convert_exception(e) from e
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_FAILED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_merge_request(
        self,
        project_id: str | int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str | None = None,
        assignee_ids: list[int] | None = None,
    ) -> Any:
        """
        Create a new merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            source_branch: Source branch name (e.g., 'feature/new-feature')
            target_branch: Target branch name (e.g., 'main', 'develop')
            title: Merge request title
            description: Optional merge request description
            assignee_ids: Optional list of user IDs to assign as reviewers

        Returns:
            Created merge request object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            ValueError: If required parameters are invalid
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mr = client.create_merge_request(
            ...     project_id=123,
            ...     source_branch='feature/new-feature',
            ...     target_branch='main',
            ...     title='Add new feature',
            ...     description='This adds a great new feature',
            ...     assignee_ids=[10, 20]
            ... )
            >>> print(f"Created MR !{mr.iid}")
        """
        self._ensure_authenticated()

        # Validate required parameters
        if not title or not title.strip():
            raise ValueError("title is required and cannot be empty")
        if not source_branch or not source_branch.strip():
            raise ValueError("source_branch is required and cannot be empty")
        if not target_branch or not target_branch.strip():
            raise ValueError("target_branch is required and cannot be empty")

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build MR creation parameters
            mr_data: dict[str, Any] = {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
            }

            # Add optional fields if provided
            if description is not None:
                mr_data["description"] = description
            if assignee_ids is not None:
                mr_data["assignee_ids"] = assignee_ids

            # Create the merge request
            merge_request = project.mergerequests.create(mr_data)
            return merge_request.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project with ID {project_id} not found") from e
            raise self._convert_exception(e) from e
        except GitlabAuthenticationError as e:
            raise AuthenticationError(ERR_AUTH_FAILED) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
    ) -> Any:
        """
        Update an existing merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)
            title: New MR title (optional)
            description: New MR description (optional)
            labels: New list of label names (optional)
            assignee_ids: New list of user IDs to assign (optional)

        Returns:
            Updated merge request object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mr = client.update_merge_request(
            ...     project_id=123,
            ...     mr_iid=42,
            ...     title="Updated title",
            ...     labels=["bug", "high-priority"]
            ... )
            >>> print(f"Updated MR !{mr.iid}: {mr.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            merge_request = project.mergerequests.get(mr_iid)

            # Update fields that were provided
            if title is not None:
                merge_request.title = title

            if description is not None:
                merge_request.description = description

            if labels is not None:
                merge_request.labels = labels

            if assignee_ids is not None:
                merge_request.assignee_ids = assignee_ids

            # Save changes
            merge_request.save()

            return merge_request.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def merge_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
        merge_commit_message: str | None = None,
    ) -> Any:
        """
        Merge a merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)
            merge_commit_message: Optional custom merge commit message

        Returns:
            Merged merge request object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For merge conflicts, already merged, or other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mr = client.merge_merge_request(
            ...     project_id=123,
            ...     mr_iid=42,
            ...     merge_commit_message="Merge feature X into main"
            ... )
            >>> print(f"Merged MR !{mr.iid}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            merge_request = project.mergerequests.get(mr_iid)

            # Merge the MR with optional message
            if merge_commit_message is not None:
                merge_request.merge(merge_commit_message=merge_commit_message)
            else:
                merge_request.merge()

            # Refresh to get updated state and return as dict
            merge_request = project.mergerequests.get(mr_iid)
            return merge_request.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def close_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
    ) -> Any:
        """
        Close a merge request without merging it.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)

        Returns:
            Closed merge request object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> mr = client.close_merge_request(project_id=123, mr_iid=42)
            >>> print(f"Closed MR !{mr.iid}: {mr.title}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            merge_request = project.mergerequests.get(mr_iid)

            # Set state_event to close
            merge_request.state_event = "close"

            # Save changes
            merge_request.save()

            return merge_request.asdict()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def reopen_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
    ) -> None:
        """
        Reopen a closed merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)

        Returns:
            None

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> client.reopen_merge_request(project_id=123, mr_iid=42)
            >>> print("MR reopened")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            merge_request = project.mergerequests.get(mr_iid)

            # Set state_event to reopen
            merge_request.state_event = "reopen"

            # Save changes
            merge_request.save()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def add_mr_comment(
        self,
        project_id: str | int,
        mr_iid: int,
        body: str,
    ) -> Any:
        """
        Add a comment (note) to a merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)
            body: Comment text (required)

        Returns:
            Created note object from python-gitlab

        Raises:
            ValueError: If body is empty
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> note = client.add_mr_comment(
            ...     project_id=123,
            ...     mr_iid=42,
            ...     body="This is a comment"
            ... )
            >>> print(f"Added comment #{note.id}")
        """
        # Validate body is not empty
        if not body or not body.strip():
            raise ValueError("Comment body cannot be empty")

        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(mr_iid)

            # Create the note/comment
            note = mr.notes.create({"body": body})

            return note

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_mr_comments(
        self,
        project_id: str | int,
        mr_iid: int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """
        List comments (notes) on a merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)
            page: Page number for pagination (default: 1)
            per_page: Number of items per page (default: 20)

        Returns:
            List of note objects from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> notes = client.list_mr_comments(project_id=123, mr_iid=42)
            >>> for note in notes:
            ...     print(f"Comment #{note.id}: {note.body}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(mr_iid)

            # List notes with pagination
            notes = mr.notes.list(page=page, per_page=per_page)

            return notes

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def approve_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
    ) -> Any:
        """
        Approve a merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)

        Returns:
            Approval object from python-gitlab

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> approval = client.approve_merge_request(project_id=123, mr_iid=42)
            >>> print(f"Approved by {approval.user['username']}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(mr_iid)

            # Approve the merge request
            approval = mr.approve()

            return approval

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def unapprove_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
    ) -> None:
        """
        Remove approval from a merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            mr_iid: Merge request IID (internal ID within the project)

        Returns:
            None

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> client.unapprove_merge_request(project_id=123, mr_iid=42)
            >>> print("Approval removed")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(mr_iid)

            # Remove approval from the merge request
            mr.unapprove()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or merge request not found: project={project_id}, mr_iid={mr_iid}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_merge_request_changes(
        self,
        project_id: str | int,
        merge_request_iid: int,
    ) -> dict[str, Any]:
        """
        Get changes/diffs for a specific merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            merge_request_iid: MR IID (internal ID within the project)

        Returns:
            Dictionary containing MR changes including:
            - changes: List of file changes with diffs
            - old_path: Original file path
            - new_path: New file path
            - diff: Unified diff string

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> changes = client.get_merge_request_changes(project_id=123, merge_request_iid=10)
            >>> for change in changes["changes"]:
            ...     print(f"{change['new_path']}: {len(change['diff'])} bytes")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(merge_request_iid)

            # Get changes/diffs
            changes = mr.changes()

            # Ensure we return a dict
            if isinstance(changes, dict):
                return changes
            return dict(changes)  # type: ignore

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Merge request not found: project={project_id}, mr_iid={merge_request_iid}"
                ) from e
            raise GitLabAPIError(f"Failed to get merge request changes: {str(e)}") from e
        except Exception as e:
            raise GitLabAPIError(f"Failed to get merge request changes: {str(e)}") from e

    def get_merge_request_commits(
        self,
        project_id: str | int,
        merge_request_iid: int,
    ) -> list[dict[str, Any]]:
        """
        Get commits in a specific merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            merge_request_iid: MR IID (internal ID within the project)

        Returns:
            List of commit dictionaries containing:
            - id: Full commit SHA
            - short_id: Short commit SHA
            - title: Commit title (first line)
            - message: Full commit message
            - author_name: Commit author
            - authored_date: When commit was authored

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> commits = client.get_merge_request_commits(project_id=123, merge_request_iid=10)
            >>> for commit in commits:
            ...     print(f"{commit['short_id']}: {commit['title']}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(merge_request_iid)

            # Get commits
            commits = mr.commits()

            # Convert to list of dicts
            result = []
            try:
                for commit in commits:
                    result.append(commit.asdict())
            except Exception:
                # If iteration fails, return what we have
                pass

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Merge request not found: project={project_id}, mr_iid={merge_request_iid}"
                ) from e
            raise GitLabAPIError(f"Failed to get merge request commits: {str(e)}") from e
        except Exception as e:
            raise GitLabAPIError(f"Failed to get merge request commits: {str(e)}") from e

    def get_merge_request_pipelines(
        self,
        project_id: str | int,
        merge_request_iid: int,
    ) -> list[dict[str, Any]]:
        """
        Get CI/CD pipelines for a specific merge request.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            merge_request_iid: MR IID (internal ID within the project)

        Returns:
            List of pipeline dictionaries containing:
            - id: Pipeline ID
            - iid: Pipeline IID
            - ref: Branch/ref the pipeline ran on
            - status: Pipeline status (success, failed, running, etc.)
            - sha: Commit SHA the pipeline ran for

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or merge request not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> pipelines = client.get_merge_request_pipelines(project_id=123, merge_request_iid=10)
            >>> for pipeline in pipelines:
            ...     print(f"Pipeline {pipeline['id']}: {pipeline['status']}")
        """
        self._ensure_authenticated()

        try:
            # Get the project
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Get the merge request
            mr = project.mergerequests.get(merge_request_iid)

            # Get pipelines
            pipelines = mr.pipelines()  # type: ignore

            # Convert to list of dicts
            result = []
            try:
                for pipeline in pipelines:
                    result.append(pipeline.asdict())
            except Exception:
                # If iteration fails, return what we have
                pass

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Merge request not found: project={project_id}, mr_iid={merge_request_iid}"
                ) from e
            raise GitLabAPIError(f"Failed to get merge request pipelines: {str(e)}") from e
        except Exception as e:
            raise GitLabAPIError(f"Failed to get merge request pipelines: {str(e)}") from e

    def list_pipelines(
        self,
        project_id: int | str,
        page: int = 1,
        per_page: int = 20,
        ref: str | None = None,
        status: str | None = None,
    ) -> dict:
        """
        List pipelines for a project.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            page: Page number for pagination (default: 1)
            per_page: Number of items per page (default: 20)
            ref: Optional ref (branch/tag) to filter pipelines
            status: Optional status to filter pipelines (e.g., "success", "failed", "running")

        Returns:
            Dictionary with list of pipelines:
            {
                "pipelines": [
                    {
                        "id": 101,
                        "status": "success",
                        "ref": "main",
                        "sha": "abc123",
                        ...
                    },
                    ...
                ]
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build kwargs for list call
            kwargs: dict = {"page": page, "per_page": per_page}

            # Add optional filters
            if ref is not None:
                kwargs["ref"] = ref
            if status is not None:
                kwargs["status"] = status

            pipelines = project.pipelines.list(**kwargs)

            # Convert pipeline objects to dicts
            pipeline_list = []
            try:
                for pipeline in pipelines:
                    try:
                        pipeline_list.append(pipeline.asdict())
                    except Exception:
                        # Fallback if asdict() fails
                        pipeline_list.append({"id": pipeline.get_id()})
            except Exception:
                # Handle iteration errors gracefully
                pass

            return {"pipelines": pipeline_list}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_pipeline(
        self,
        project_id: int | str,
        pipeline_id: int,
    ) -> dict:
        """
        Get details of a specific pipeline.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            pipeline_id: Pipeline ID

        Returns:
            Pipeline details as a dictionary:
            {
                "id": 101,
                "status": "success",
                "ref": "main",
                "sha": "abc123",
                "web_url": "...",
                "created_at": "...",
                ...
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            pipeline = project.pipelines.get(pipeline_id)

            # Convert to dict
            try:
                return pipeline.asdict()
            except Exception:
                # Fallback if asdict() fails
                return {"id": pipeline.get_id()}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or pipeline not found: project={project_id}, pipeline={pipeline_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_pipeline(
        self,
        project_id: int | str,
        ref: str,
        variables: dict | None = None,
    ) -> dict:
        """
        Create (trigger) a new pipeline for a ref.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            ref: Branch or tag name to run pipeline for
            variables: Optional dict of pipeline variables

        Returns:
            Created pipeline details:
            {
                "id": 201,
                "status": "pending",
                "ref": "main",
                "sha": "abc123",
                ...
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors (e.g., invalid ref)
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore

            # Build pipeline creation data
            pipeline_data: dict = {"ref": ref}
            if variables is not None:
                pipeline_data["variables"] = variables

            pipeline = project.pipelines.create(pipeline_data)

            # Convert to dict
            try:
                return pipeline.asdict()
            except Exception:
                # Fallback if asdict() fails
                return {"id": pipeline.get_id()}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def retry_pipeline(
        self,
        project_id: int | str,
        pipeline_id: int,
    ) -> dict:
        """
        Retry a failed pipeline.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            pipeline_id: Pipeline ID to retry

        Returns:
            Retried pipeline details:
            {
                "id": 101,
                "status": "pending",
                ...
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            pipeline = project.pipelines.get(pipeline_id)

            # Retry the pipeline
            pipeline.retry()

            # Return updated pipeline info
            try:
                return pipeline.asdict()
            except Exception:
                # Fallback if asdict() fails
                return {"id": pipeline.get_id()}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or pipeline not found: project={project_id}, pipeline={pipeline_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def cancel_pipeline(
        self,
        project_id: int | str,
        pipeline_id: int,
    ) -> dict:
        """
        Cancel a running pipeline.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            pipeline_id: Pipeline ID to cancel

        Returns:
            Cancelled pipeline details:
            {
                "id": 101,
                "status": "canceled",
                ...
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            pipeline = project.pipelines.get(pipeline_id)

            # Cancel the pipeline
            pipeline.cancel()

            # Return updated pipeline info
            try:
                return pipeline.asdict()
            except Exception:
                # Fallback if asdict() fails
                return {"id": pipeline.get_id()}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or pipeline not found: project={project_id}, pipeline={pipeline_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_pipeline(
        self,
        project_id: int | str,
        pipeline_id: int,
    ) -> dict:
        """
        Delete a pipeline.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            pipeline_id: Pipeline ID to delete

        Returns:
            Success confirmation:
            {
                "success": True,
                "pipeline_id": 101,
                "message": "Pipeline deleted"
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            pipeline = project.pipelines.get(pipeline_id)

            # Delete the pipeline
            pipeline.delete()

            # Return success confirmation
            return {
                "success": True,
                "pipeline_id": pipeline_id,
                "message": "Pipeline deleted",
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or pipeline not found: project={project_id}, pipeline={pipeline_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_pipeline_jobs(
        self,
        project_id: int | str,
        pipeline_id: int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """
        List all jobs in a pipeline.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            pipeline_id: Pipeline ID
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20)

        Returns:
            List of job details:
            [
                {
                    "id": 1,
                    "name": "build",
                    "status": "success",
                    "stage": "build",
                    ...
                },
                ...
            ]

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            pipeline = project.pipelines.get(pipeline_id)

            # List jobs in the pipeline
            jobs = pipeline.jobs.list(page=page, per_page=per_page)

            # Convert jobs to dicts
            result = []
            try:
                for job in jobs:
                    result.append(job.asdict())
            except Exception:
                # Fallback if iteration or asdict() fails
                pass

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or pipeline not found: project={project_id}, pipeline={pipeline_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_job(
        self,
        project_id: int | str,
        job_id: int,
    ) -> dict:
        """
        Get details of a specific job.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            job_id: Job ID

        Returns:
            Job details:
            {
                "id": 1,
                "name": "build",
                "status": "success",
                "stage": "build",
                "duration": 120.5,
                ...
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or job not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            job = project.jobs.get(job_id)

            # Return job details
            try:
                return job.asdict()
            except Exception:
                # Fallback if asdict() fails
                return {"id": job.get_id()}

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or job not found: project={project_id}, job={job_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_job_trace(
        self,
        project_id: int | str,
        job_id: int,
        tail_lines: int | None = None,
    ) -> dict:
        """
        Get the execution log/trace of a job.

        Args:
            project_id: Project ID or path (e.g., "group/project")
            job_id: Job ID
            tail_lines: Optional number of lines to return from the end of the log.
                       Useful for large logs to get only the most recent output (where errors typically are).
                       If None, returns the full log. Recommended: 500-1000 for error analysis.

        Returns:
            Job trace details:
            {
                "job_id": 1,
                "trace": "Building project...\\nTests passed!\\n",
                "truncated": false,
                "total_lines": 1234,
                "returned_lines": 1234
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or job not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        try:
            project = self._gitlab.projects.get(project_id)  # type: ignore
            job = project.jobs.get(job_id)

            # Get job trace (log)
            trace = job.trace()

            # Decode bytes to string if needed
            if isinstance(trace, bytes):
                trace_str = trace.decode("utf-8", errors="replace")
            else:
                trace_str = str(trace) if trace else ""

            # Calculate line counts
            lines = trace_str.splitlines(keepends=True) if trace_str else []
            total_lines = len(lines)

            # Apply tail if requested
            truncated = False
            if tail_lines is not None and tail_lines > 0 and total_lines > tail_lines:
                lines = lines[-tail_lines:]
                trace_str = "".join(lines)
                truncated = True

            returned_lines = len(lines)

            return {
                "job_id": job_id,
                "trace": trace_str,
                "truncated": truncated,
                "total_lines": total_lines,
                "returned_lines": returned_lines,
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(
                    f"Project or job not found: project={project_id}, job={job_id}"
                ) from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def retry_job(self, project_id: str | int, job_id: int) -> dict[str, str | int]:
        """
        Retry a failed job.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            job_id: Job ID to retry

        Returns:
            Dictionary with retry confirmation:
            {
                "job_id": 456,
                "status": "retried",
                "message": "Job retried successfully"
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or job not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            job = project.jobs.get(job_id)

            # Retry the job
            job.retry()

            return {
                "job_id": job_id,
                "status": "retried",
                "message": f"Job {job_id} retried successfully",
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Job not found: job_id={job_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def cancel_job(self, project_id: str | int, job_id: int) -> dict[str, str | int]:
        """
        Cancel a running job.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            job_id: Job ID to cancel

        Returns:
            Dictionary with cancel confirmation:
            {
                "job_id": 456,
                "status": "canceled",
                "message": "Job canceled successfully"
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or job not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            job = project.jobs.get(job_id)

            # Cancel the job
            job.cancel()

            return {
                "job_id": job_id,
                "status": "canceled",
                "message": f"Job {job_id} canceled successfully",
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Job not found: job_id={job_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def play_job(self, project_id: str | int, job_id: int) -> dict[str, str | int]:
        """
        Start a manual job.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            job_id: Job ID to start

        Returns:
            Dictionary with play confirmation:
            {
                "job_id": 456,
                "status": "started",
                "message": "Manual job started successfully"
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or job not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            job = project.jobs.get(job_id)

            # Start the manual job
            job.play()

            return {
                "job_id": job_id,
                "status": "started",
                "message": f"Manual job {job_id} started successfully",
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Job not found: job_id={job_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def download_job_artifacts(self, project_id: str | int, job_id: int) -> dict[str, int | bytes]:
        """
        Download job artifacts.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            job_id: Job ID to download artifacts from

        Returns:
            Dictionary with artifacts data:
            {
                "job_id": 456,
                "artifacts_data": b"...",
                "size_bytes": 1024
            }

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project, job, or artifacts not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            job = project.jobs.get(job_id)

            # Download the artifacts (returns bytes)
            artifacts_data = job.artifacts()

            return {
                "job_id": job_id,
                "artifacts_data": artifacts_data,
                "size_bytes": len(artifacts_data),
            }

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Could be job not found or no artifacts available
                error_msg = str(e)
                if "artifact" in error_msg.lower():
                    raise NotFoundError(f"No artifacts available for job {job_id}") from e
                raise NotFoundError(f"Job not found: job_id={job_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_pipeline_variables(
        self, project_id: str | int, pipeline_id: int
    ) -> list[dict[str, str]]:
        """
        List variables for a pipeline.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            pipeline_id: Pipeline ID

        Returns:
            List of variable dictionaries:
            [
                {"key": "ENV", "value": "production"},
                {"key": "DEBUG", "value": "false"}
            ]

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or pipeline not found
            GitLabAPIError: For other API errors
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            pipeline = project.pipelines.get(pipeline_id)

            # List pipeline variables
            variables = pipeline.variables.list()

            # Convert to dictionaries
            result = []
            for var in variables:
                result.append({"key": getattr(var, "key", ""), "value": getattr(var, "value", "")})

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Pipeline not found: pipeline_id={pipeline_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def _convert_exception(self, exc: Exception) -> Exception:
        """
        Convert python-gitlab exceptions to our custom exceptions.

        Args:
            exc: The original exception from python-gitlab

        Returns:
            Our custom exception with appropriate type
        """
        # Handle HTTP errors with status codes
        if isinstance(exc, GitlabHttpError):
            status_code = getattr(exc, "response_code", None)

            if status_code == 401:
                return AuthenticationError(ERR_AUTH_FAILED_401)
            elif status_code == 403:
                return PermissionError("Permission denied (403)")
            elif status_code == 404:
                return NotFoundError("Resource not found (404)")
            elif status_code == 429:
                return RateLimitError("Rate limit exceeded (429)")
            elif status_code and 500 <= status_code < 600:
                return GitLabServerError(f"GitLab server error ({status_code})")

        # Handle specific GitLab exceptions
        if isinstance(exc, GitlabAuthenticationError):
            return AuthenticationError(ERR_AUTH_FAILED)
        elif isinstance(exc, GitlabGetError):
            status_code = getattr(exc, "response_code", None)
            if status_code == 404:
                return NotFoundError("Resource not found")

        # Default to generic API error
        return GitLabAPIError(f"GitLab API error: {str(exc)}")

    # ========================================
    # Release Operations
    # ========================================

    def list_releases(
        self,
        project_id: str | int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List releases for a project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20)

        Returns:
            List of release dictionaries containing:
            - tag_name: The Git tag associated with the release
            - name: Release name
            - description: Release description/notes
            - created_at: When the release was created
            - released_at: When the release was published

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> releases = client.list_releases(project_id=123)
            >>> for release in releases:
            ...     print(f"{release['tag_name']}: {release['name']}")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            releases = project.releases.list(page=page, per_page=per_page)

            result = []
            for release in releases:
                release_dict = {
                    "tag_name": getattr(release, "tag_name", None),
                    "name": getattr(release, "name", None),
                    "description": getattr(release, "description", None),
                    "created_at": getattr(release, "created_at", None),
                    "released_at": getattr(release, "released_at", None),
                }
                result.append(release_dict)

            return result

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_release(
        self,
        project_id: str | int,
        tag_name: str,
    ) -> dict[str, Any]:
        """
        Get a specific release by tag name.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            tag_name: The Git tag associated with the release (e.g., 'v1.0.0')

        Returns:
            Release dictionary containing:
            - tag_name: The Git tag associated with the release
            - name: Release name
            - description: Release description/notes
            - created_at: When the release was created
            - released_at: When the release was published
            - author: Release author information

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or release not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> release = client.get_release(project_id=123, tag_name='v1.0.0')
            >>> print(f"{release['tag_name']}: {release['name']}")
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            release = project.releases.get(tag_name)

            release_dict = {
                "tag_name": getattr(release, "tag_name", None),
                "name": getattr(release, "name", None),
                "description": getattr(release, "description", None),
                "created_at": getattr(release, "created_at", None),
                "released_at": getattr(release, "released_at", None),
                "author": getattr(release, "author", None),
            }

            return release_dict

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to differentiate between project not found and release not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Release not found: tag={tag_name}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_release(
        self,
        project_id: str | int,
        tag_name: str,
        name: str,
        description: str | None = None,
        ref: str | None = None,
    ) -> None:
        """
        Create a new release for a project.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            tag_name: The Git tag name for the release (e.g., 'v1.0.0')
            name: Release name
            description: Optional release description/notes
            ref: Optional commit SHA, tag, or branch (required if tag doesn't exist)

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project not found
            ValueError: If required parameters are invalid
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> client.create_release(
            ...     project_id=123,
            ...     tag_name='v1.0.0',
            ...     name='Version 1.0.0',
            ...     description='First stable release'
            ... )
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        # Validate required parameters
        if not tag_name or not tag_name.strip():
            raise ValueError("tag_name is required and cannot be empty")
        if not name or not name.strip():
            raise ValueError("name is required and cannot be empty")

        try:
            project = self._gitlab.projects.get(project_id)

            release_data: dict[str, Any] = {
                "tag_name": tag_name,
                "name": name,
            }

            if description is not None:
                release_data["description"] = description
            if ref is not None:
                release_data["ref"] = ref

            project.releases.create(release_data)

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_release(
        self,
        project_id: str | int,
        tag_name: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """
        Update an existing release.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            tag_name: The Git tag name for the release (e.g., 'v1.0.0')
            name: Optional new release name
            description: Optional new release description/notes

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or release not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> client.update_release(
            ...     project_id=123,
            ...     tag_name='v1.0.0',
            ...     name='Updated Version 1.0.0',
            ...     description='Updated release notes'
            ... )
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)
            release = project.releases.get(tag_name)

            # Update only provided fields (partial update)
            if name is not None:
                release.name = name

            if description is not None:
                release.description = description

            # Save changes
            release.save()

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to differentiate between project not found and release not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Release not found: tag={tag_name}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def delete_release(
        self,
        project_id: str | int,
        tag_name: str,
    ) -> None:
        """
        Delete a release.

        Args:
            project_id: Project ID or path (e.g., 'group/project' or 123)
            tag_name: The Git tag name for the release (e.g., 'v1.0.0')

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If project or release not found
            GitLabAPIError: For other API errors

        Example:
            >>> client = GitLabClient(config)
            >>> client.delete_release(project_id=123, tag_name='v1.0.0')
        """
        self._ensure_authenticated()

        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            project = self._gitlab.projects.get(project_id)

            # Delete the release using the manager's delete method
            project.releases.delete(tag_name)

        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                # Try to differentiate between project not found and release not found
                try:
                    self._gitlab.projects.get(project_id)
                    raise NotFoundError(f"Release not found: tag={tag_name}") from e
                except GitlabGetError:
                    raise NotFoundError(f"Project not found: {project_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_user(self, user_id: int) -> dict:
        """Get user details by ID.

        Args:
            user_id: User ID to retrieve

        Returns:
            Dictionary containing user information

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If user not found
            GitLabAPIError: For other API errors

        Example:
            >>> user = client.get_user(user_id=42)
            >>> print(user['username'])
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            user = self._gitlab.users.get(user_id)
            return {
                "id": user.id,
                "username": getattr(user, "username", ""),
                "name": getattr(user, "name", ""),
                "email": getattr(user, "email", ""),
                "state": getattr(user, "state", ""),
                "web_url": getattr(user, "web_url", ""),
                "avatar_url": getattr(user, "avatar_url", ""),
                "bio": getattr(user, "bio", ""),
                "location": getattr(user, "location", ""),
                "public_email": getattr(user, "public_email", ""),
                "created_at": getattr(user, "created_at", ""),
            }
        except GitlabGetError as e:
            if e.response_code == 404:
                raise NotFoundError(f"User not found: {user_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def search_users(
        self,
        search: str,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """Search for users by username, name, or email.

        Args:
            search: Search query string
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20)

        Returns:
            List of dictionaries containing user information

        Raises:
            AuthenticationError: If not authenticated
            ValueError: If search query is empty
            GitLabAPIError: For other API errors

        Example:
            >>> users = client.search_users(search="john")
            >>> for user in users:
            ...     print(user['username'])
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        if not search or not search.strip():
            raise ValueError("Search query cannot be empty")

        try:
            users = self._gitlab.users.list(search=search, page=page, per_page=per_page)
            return [
                {
                    "id": user.id,
                    "username": getattr(user, "username", ""),
                    "name": getattr(user, "name", ""),
                    "email": getattr(user, "email", ""),
                    "state": getattr(user, "state", ""),
                    "web_url": getattr(user, "web_url", ""),
                    "avatar_url": getattr(user, "avatar_url", ""),
                }
                for user in users
            ]
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_user_projects(
        self,
        user_id: int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """List projects accessible to a specific user.

        Args:
            user_id: User ID
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20)

        Returns:
            List of dictionaries containing project information

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If user not found
            GitLabAPIError: For other API errors

        Example:
            >>> projects = client.list_user_projects(user_id=42)
            >>> for project in projects:
            ...     print(project['name'])
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            user = self._gitlab.users.get(user_id)
            projects = user.projects.list(page=page, per_page=per_page)
            return [
                {
                    "id": project.id,
                    "name": getattr(project, "name", ""),
                    "path": getattr(project, "path", ""),
                    "path_with_namespace": getattr(project, "path_with_namespace", ""),
                    "description": getattr(project, "description", ""),
                    "web_url": getattr(project, "web_url", ""),
                    "visibility": getattr(project, "visibility", ""),
                    "created_at": getattr(project, "created_at", ""),
                }
                for project in projects
            ]
        except GitlabGetError as e:
            if e.response_code == 404:
                raise NotFoundError(f"User not found: {user_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_groups(
        self,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """List groups accessible to the authenticated user.

        Args:
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20)

        Returns:
            List of dictionaries containing group information

        Raises:
            AuthenticationError: If not authenticated
            GitLabAPIError: For other API errors

        Example:
            >>> groups = client.list_groups()
            >>> for group in groups:
            ...     print(group['name'])
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            groups = self._gitlab.groups.list(page=page, per_page=per_page)
            return [
                {
                    "id": group.id,
                    "name": getattr(group, "name", ""),
                    "path": getattr(group, "path", ""),
                    "full_path": getattr(group, "full_path", ""),
                    "description": getattr(group, "description", ""),
                    "web_url": getattr(group, "web_url", ""),
                    "visibility": getattr(group, "visibility", ""),
                    "created_at": getattr(group, "created_at", ""),
                }
                for group in groups
            ]
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_group(self, group_id: str | int) -> dict:
        """Get group details by ID or path.

        Args:
            group_id: Group ID or full path (e.g., 'company/engineering')

        Returns:
            Dictionary containing group information

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If group not found
            GitLabAPIError: For other API errors

        Example:
            >>> group = client.get_group(group_id=42)
            >>> print(group['name'])
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            group = self._gitlab.groups.get(group_id)
            return {
                "id": group.id,
                "name": getattr(group, "name", ""),
                "path": getattr(group, "path", ""),
                "full_path": getattr(group, "full_path", ""),
                "description": getattr(group, "description", ""),
                "web_url": getattr(group, "web_url", ""),
                "visibility": getattr(group, "visibility", ""),
                "avatar_url": getattr(group, "avatar_url", ""),
                "created_at": getattr(group, "created_at", ""),
            }
        except GitlabGetError as e:
            if e.response_code == 404:
                raise NotFoundError(f"Group not found: {group_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_group_members(
        self,
        group_id: str | int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """List members of a specific group.

        Args:
            group_id: Group ID or full path (e.g., 'company/engineering')
            page: Page number for pagination (default: 1)
            per_page: Number of results per page (default: 20)

        Returns:
            List of dictionaries containing member information

        Raises:
            AuthenticationError: If not authenticated
            NotFoundError: If group not found
            GitLabAPIError: For other API errors

        Example:
            >>> members = client.list_group_members(group_id=42)
            >>> for member in members:
            ...     print(f"{member['username']} - {member['access_level']}")
        """
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)

        try:
            group = self._gitlab.groups.get(group_id)
            members = group.members.list(page=page, per_page=per_page)
            return [
                {
                    "id": member.id,
                    "username": getattr(member, "username", ""),
                    "name": getattr(member, "name", ""),
                    "email": getattr(member, "email", ""),
                    "state": getattr(member, "state", ""),
                    "access_level": getattr(member, "access_level", 0),
                    "web_url": getattr(member, "web_url", ""),
                    "avatar_url": getattr(member, "avatar_url", ""),
                }
                for member in members
            ]
        except GitlabGetError as e:
            if e.response_code == 404:
                raise NotFoundError(f"Group not found: {group_id}") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def _mr_to_dict(self, mr: Any) -> dict[str, Any]:
        """Convert a python-gitlab MergeRequest object to a dictionary."""
        if hasattr(mr, "asdict"):
            try:
                res = mr.asdict()
                if isinstance(res, dict):
                    return res
            except Exception:
                pass
        if isinstance(mr, dict):
            return mr

        # Manual extraction fallback
        author = None
        if hasattr(mr, "author") and mr.author:
            author = {
                "id": getattr(mr.author, "id", None),
                "username": getattr(mr.author, "username", ""),
                "name": getattr(mr.author, "name", ""),
                "state": getattr(mr.author, "state", ""),
                "web_url": getattr(mr.author, "web_url", ""),
                "avatar_url": getattr(mr.author, "avatar_url", ""),
            }
        
        assignees = []
        if hasattr(mr, "assignees") and mr.assignees:
            try:
                for assignee in mr.assignees:
                    assignees.append({
                        "id": getattr(assignee, "id", None),
                        "username": getattr(assignee, "username", ""),
                        "name": getattr(assignee, "name", ""),
                        "state": getattr(assignee, "state", ""),
                        "web_url": getattr(assignee, "web_url", ""),
                        "avatar_url": getattr(assignee, "avatar_url", ""),
                    })
            except TypeError:
                pass

        reviewers = []
        if hasattr(mr, "reviewers") and mr.reviewers:
            try:
                for reviewer in mr.reviewers:
                    reviewers.append({
                        "id": getattr(reviewer, "id", None),
                        "username": getattr(reviewer, "username", ""),
                        "name": getattr(reviewer, "name", ""),
                        "state": getattr(reviewer, "state", ""),
                        "web_url": getattr(reviewer, "web_url", ""),
                        "avatar_url": getattr(reviewer, "avatar_url", ""),
                    })
            except TypeError:
                pass

        milestone = None
        if hasattr(mr, "milestone") and mr.milestone:
            milestone = {
                "id": getattr(mr.milestone, "id", None),
                "iid": getattr(mr.milestone, "iid", None),
                "title": getattr(mr.milestone, "title", ""),
                "description": getattr(mr.milestone, "description", ""),
                "state": getattr(mr.milestone, "state", ""),
            }

        return {
            "id": getattr(mr, "id", None),
            "iid": getattr(mr, "iid", None),
            "project_id": getattr(mr, "project_id", None),
            "title": getattr(mr, "title", ""),
            "description": getattr(mr, "description", ""),
            "state": getattr(mr, "state", ""),
            "created_at": getattr(mr, "created_at", ""),
            "updated_at": getattr(mr, "updated_at", ""),
            "merged_by": getattr(mr, "merged_by", None),
            "merged_at": getattr(mr, "merged_at", None),
            "closed_by": getattr(mr, "closed_by", None),
            "closed_at": getattr(mr, "closed_at", None),
            "target_branch": getattr(mr, "target_branch", ""),
            "source_branch": getattr(mr, "source_branch", ""),
            "user_notes_count": getattr(mr, "user_notes_count", 0),
            "upvotes": getattr(mr, "upvotes", 0),
            "downvotes": getattr(mr, "downvotes", 0),
            "author": author,
            "assignees": assignees,
            "assignee": assignees[0] if assignees else None,
            "reviewers": reviewers,
            "source_project_id": getattr(mr, "source_project_id", None),
            "target_project_id": getattr(mr, "target_project_id", None),
            "labels": getattr(mr, "labels", []),
            "draft": getattr(mr, "draft", False),
            "work_in_progress": getattr(mr, "work_in_progress", False),
            "milestone": milestone,
            "merge_when_pipeline_succeeds": getattr(mr, "merge_when_pipeline_succeeds", False),
            "merge_status": getattr(mr, "merge_status", ""),
            "detailed_merge_status": getattr(mr, "detailed_merge_status", ""),
            "sha": getattr(mr, "sha", ""),
            "merge_commit_sha": getattr(mr, "merge_commit_sha", None),
            "squash_commit_sha": getattr(mr, "squash_commit_sha", None),
            "web_url": getattr(mr, "web_url", ""),
            "has_conflicts": getattr(mr, "has_conflicts", False),
            "blocking_discussions_resolved": getattr(mr, "blocking_discussions_resolved", True),
        }

    def list_merge_requests(
        self,
        project_id: str | int,
        state: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """List merge requests for a project."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            project = self._gitlab.projects.get(project_id)
            kwargs: dict[str, Any] = {
                "page": page,
                "per_page": per_page,
            }
            if state is not None:
                kwargs["state"] = state
            mrs = project.mergerequests.list(**kwargs)
            return [self._mr_to_dict(mr) for mr in mrs]
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project with ID {project_id} not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_merge_request(self, project_id: str | int, mr_iid: int) -> dict[str, Any]:
        """Get details of a specific merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
            except GitlabGetError as pe:
                if getattr(pe, "response_code", None) == 404:
                    raise NotFoundError(f"Project with ID {project_id} not found") from pe
                raise
            
            mr = project.mergerequests.get(mr_iid)
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Merge request with IID {mr_iid} not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def create_merge_request(
        self,
        project_id: str | int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str | None = None,
        assignee_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Create a new merge request."""
        if not source_branch or not source_branch.strip():
            raise ValueError("source_branch is required")
        if not target_branch or not target_branch.strip():
            raise ValueError("target_branch is required")
        if not title or not title.strip():
            raise ValueError("title is required")

        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            project = self._gitlab.projects.get(project_id)
            data: dict[str, Any] = {
                "source_branch": source_branch.strip(),
                "target_branch": target_branch.strip(),
                "title": title.strip(),
            }
            if description is not None:
                data["description"] = description
            if assignee_ids is not None:
                data["assignee_ids"] = assignee_ids
            mr = project.mergerequests.create(data)
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError(f"Project with ID {project_id} not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def update_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Update an existing merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            if title is not None:
                mr.title = title
            if description is not None:
                mr.description = description
            if labels is not None:
                mr.labels = labels
            if assignee_ids is not None:
                mr.assignee_ids = assignee_ids
            mr.save()
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def merge_merge_request(
        self,
        project_id: str | int,
        mr_iid: int,
        merge_commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Merge a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            kwargs: dict[str, Any] = {}
            if merge_commit_message is not None:
                kwargs["merge_commit_message"] = merge_commit_message
            try:
                mr.merge(**kwargs)
            except GitlabHttpError as he:
                # 406 is returned when branch cannot be merged (e.g. conflicts/unresolved discussions)
                if he.response_code == 406:
                    raise GitLabAPIError("Branch cannot be merged (406 Method Not Allowed)") from he
                raise

            mr = project.mergerequests.get(mr_iid)
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def close_merge_request(self, project_id: str | int, mr_iid: int) -> dict[str, Any]:
        """Close a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            mr.state_event = "close"
            mr.save()
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def reopen_merge_request(self, project_id: str | int, mr_iid: int) -> dict[str, Any]:
        """Reopen a closed merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            mr.state_event = "reopen"
            mr.save()
            return self._mr_to_dict(mr)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def approve_merge_request(self, project_id: str | int, mr_iid: int) -> dict[str, Any]:
        """Approve a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            approval_status = mr.approve()
            if isinstance(approval_status, dict):
                return approval_status
            return approval_status
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def unapprove_merge_request(self, project_id: str | int, mr_iid: int) -> None:
        """Remove approval from a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            mr.unapprove()
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_merge_request_changes(self, project_id: str | int, merge_request_iid: int) -> dict[str, Any]:
        """Get changes (diff) for a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(merge_request_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Merge request not found") from ge
                raise

            changes = mr.changes()
            if hasattr(changes, "asdict"):
                return changes.asdict()
            elif isinstance(changes, dict):
                return changes
            return dict(changes)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Merge request not found") from e
            raise GitLabAPIError(f"Failed to get merge request changes: {e}") from e
        except Exception as e:
            raise GitLabAPIError(f"Failed to get merge request changes: {e}") from e

    def get_merge_request_commits(self, project_id: str | int, merge_request_iid: int) -> list[dict[str, Any]]:
        """Get commits for a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(merge_request_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Merge request not found") from ge
                raise

            commits = mr.commits()
            result = []
            for commit in commits:
                if hasattr(commit, "asdict"):
                    result.append(commit.asdict())
                else:
                    result.append({
                        "id": getattr(commit, "id", ""),
                        "short_id": getattr(commit, "short_id", ""),
                        "title": getattr(commit, "title", ""),
                        "author_name": getattr(commit, "author_name", ""),
                        "author_email": getattr(commit, "author_email", ""),
                        "created_at": getattr(commit, "created_at", ""),
                        "message": getattr(commit, "message", ""),
                    })
            return result
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def get_merge_request_pipelines(self, project_id: str | int, merge_request_iid: int) -> list[dict[str, Any]]:
        """Get pipelines for a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(merge_request_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Merge request not found") from ge
                raise

            pipelines = mr.pipelines()
            result = []
            for pipeline in pipelines:
                if hasattr(pipeline, "asdict"):
                    result.append(pipeline.asdict())
                else:
                    result.append({
                        "id": getattr(pipeline, "id", None),
                        "project_id": getattr(pipeline, "project_id", None),
                        "status": getattr(pipeline, "status", ""),
                        "ref": getattr(pipeline, "ref", ""),
                        "sha": getattr(pipeline, "sha", ""),
                        "web_url": getattr(pipeline, "web_url", ""),
                    })
            return result
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def add_mr_comment(self, project_id: str | int, mr_iid: int, body: str) -> Any:
        """Add a comment to a merge request."""
        if not body or not body.strip():
            raise ValueError("Comment body cannot be empty")

        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            note = mr.notes.create({"body": body})
            return note
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e

    def list_mr_comments(
        self,
        project_id: str | int,
        mr_iid: int,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Any]:
        """List all comments on a merge request."""
        self._ensure_authenticated()
        if not self._gitlab:
            raise AuthenticationError(ERR_NOT_AUTHENTICATED)
        try:
            try:
                project = self._gitlab.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
            except GitlabGetError as ge:
                if getattr(ge, "response_code", None) == 404:
                    raise NotFoundError("Project or merge request not found") from ge
                raise

            notes = mr.notes.list(page=page, per_page=per_page)
            return list(notes)
        except NotFoundError:
            raise
        except GitlabGetError as e:
            if getattr(e, "response_code", None) == 404:
                raise NotFoundError("Project or merge request not found") from e
            raise self._convert_exception(e) from e
        except Exception as e:
            raise self._convert_exception(e) from e
