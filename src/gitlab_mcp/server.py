"""
GitLab MCP Server.

This module implements the Model Context Protocol (MCP) server for GitLab integration.
It provides tools for interacting with GitLab repositories, issues, merge requests, and more.

Supports multiple transports:
- stdio (default): For local CLI clients like Claude Code
- http: Streamable HTTP for remote clients like IBM ContextForge
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from typing import Any, Literal

from mcp.server import Server
from mcp.server.stdio import stdio_server

from gitlab_mcp import tools
from gitlab_mcp.client.gitlab_client import GitLabClient
from gitlab_mcp.config.settings import GitLabConfig, load_config
from gitlab_mcp.prompts.registry import PromptRegistry
from gitlab_mcp.resources.handlers import read_resource as read_resource_handler
from gitlab_mcp.resources.registry import ResourceRegistry


# Helper functions to reduce cognitive complexity in async_main (SonarQube S3776)
def _build_resources_list(registry: ResourceRegistry) -> list[Any]:
    """Build list of MCP Resource objects from registry."""
    from mcp.types import Resource

    return [
        Resource(
            uri=res["uri"],
            name=res["name"],
            description=res.get("description"),
            mimeType=res.get("mime_type"),
        )
        for res in registry.get_static_resources()
    ]


def _build_resource_templates_list(registry: ResourceRegistry) -> list[Any]:
    """Build list of MCP ResourceTemplate objects from registry."""
    from mcp.types import ResourceTemplate

    return [
        ResourceTemplate(
            uriTemplate=tmpl["uri_template"],
            name=tmpl["name"],
            description=tmpl.get("description"),
            mimeType=tmpl.get("mime_type"),
        )
        for tmpl in registry.get_resource_templates()
    ]


def _build_prompts_list(registry: PromptRegistry) -> list[Any]:
    """Build list of MCP Prompt objects from registry."""
    from mcp.types import Prompt, PromptArgument

    prompts = []
    for prompt_def in registry.list_prompts():
        arguments = [
            PromptArgument(
                name=arg["name"],
                description=arg.get("description"),
                required=arg.get("required", False),
            )
            for arg in prompt_def.get("arguments", [])
        ]
        prompts.append(
            Prompt(
                name=prompt_def["name"],
                description=prompt_def.get("description"),
                arguments=arguments if arguments else None,
            )
        )
    return prompts


def _build_prompt_messages(registry: PromptRegistry, name: str, arguments: dict[str, str]) -> Any:
    """Build GetPromptResult from registry prompt messages."""
    from mcp.types import GetPromptResult, PromptMessage, TextContent

    messages = registry.get_prompt_messages(name, arguments)
    prompt_messages = [
        PromptMessage(
            role=msg["role"],
            content=TextContent(type="text", text=msg["content"]),
        )
        for msg in messages
    ]
    return GetPromptResult(messages=prompt_messages)


_CANONICAL_ACCEPT = b"application/json, text/event-stream"


def _normalize_accept_asgi(app: Any) -> Any:
    """Force Accept to MCP's required value.

    Some clients (claude.ai backend) send 'Accept: */*' and Streamable HTTP handler 406s them
    because the streamable-http handler insists on both 'application/json' and
    'text/event-stream'. MCP only ever responds with those, so the rewrite is
    semantically safe.
    """

    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        rewritten = [(k, v) for k, v in (scope.get("headers") or []) if k.lower() != b"accept"]
        rewritten.append((b"accept", _CANONICAL_ACCEPT))
        scope = dict(scope)
        scope["headers"] = rewritten
        await app(scope, receive, send)

    return middleware


async def _run_http_server(
    mcp_server: Server, host: str, port: int, insecure_no_auth: bool = False
) -> None:
    """
    Run the MCP server with Streamable HTTP transport.

    This enables remote clients like IBM ContextForge to connect via HTTP.

    Args:
        mcp_server: The configured MCP Server instance
        host: Host to bind to (e.g., "0.0.0.0" for all interfaces)
        port: Port to listen on
        insecure_no_auth: Skip OAuth/passphrase in HTTP mode
    """
    import contextlib
    import os
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.types import Receive, Scope, Send

    # Create session manager for handling HTTP sessions
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=False,  # Use SSE streaming for responses
        stateless=False,  # Maintain session state
    )

    # Create ASGI handler that delegates to session manager
    class StreamableHTTPASGIApp:
        """ASGI application for Streamable HTTP server transport."""

        def __init__(self, manager: StreamableHTTPSessionManager) -> None:
            self.session_manager = manager

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await self.session_manager.handle_request(scope, receive, send)

    asgi_handler = StreamableHTTPASGIApp(session_manager)

    # Create lifespan context manager for session manager
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info("GitLab MCP Server started on http://%s:%d/mcp", host, port)
            yield

    # Create Starlette app with route to MCP endpoint
    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/mcp", endpoint=asgi_handler, methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )

    mcp_app = _normalize_accept_asgi(starlette_app)

    if insecure_no_auth:
        print(
            "[gitlab-mcp-server] WARNING: --insecure-no-auth set; the HTTP endpoint has NO authentication.",
            file=sys.stderr,
        )
        app = mcp_app
    else:
        passphrase = os.environ.get("MCP_PASSPHRASE", "").strip()
        if not passphrase:
            print(
                "[gitlab-mcp-server] MCP_PASSPHRASE env var is required in --transport http mode "
                "(or pass --insecure-no-auth to disable auth).",
                file=sys.stderr,
            )
            sys.exit(2)

        from gitlab_mcp.oauth import (
            OAuthState,
            build_oauth_app,
            compose_app,
            oauth_bearer_asgi,
        )

        state = OAuthState(passphrase)
        oauth_app = build_oauth_app(state)
        protected_mcp = oauth_bearer_asgi(mcp_app, state)
        app = compose_app(protected_mcp, oauth_app)
        print(
            "[gitlab-mcp-server] OAuth passphrase auth enabled. "
            "Login form at /authorize. MCP at /mcp.",
            file=sys.stderr,
        )

    # Run with uvicorn
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


# Schema description constants (SonarQube S1192 compliance)
DESC_PROJECT_ID = "Project ID or path (e.g., 'group/project')"
DESC_PAGE = "Page number (optional, default: 1)"
DESC_PER_PAGE = "Results per page (optional, default: 20)"
DESC_MR_IID = "Merge request IID (internal ID)"
DESC_ISSUE_IID = "Issue IID (internal ID)"
DESC_PIPELINE_ID = "Pipeline ID"
DESC_JOB_ID = "Job ID"
DESC_NEW_TITLE = "New title (optional)"
DESC_NEW_DESC = "New description (optional)"
DESC_WIKI_SLUG = "Wiki page slug (URL-friendly identifier)"
DESC_SNIPPET_ID = "Snippet ID"
DESC_TAG_NAME = "Git tag name"
DESC_TAG_RELEASE = "Git tag name associated with release"
DESC_SEARCH_QUERY = "Search query string"
DESC_AUTHOR_EMAIL = "Email of commit author (optional)"
DESC_AUTHOR_NAME = "Name of commit author (optional)"
DESC_SOURCE_REF = "Source branch, tag, or commit SHA"

# Module logger for security-safe error logging
logger = logging.getLogger(__name__)

# Tool annotations for MCP SDK v1.25.0 (SEP-986)
# Maps tool names to behavior hints for client safety prompts
TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    # Context tools - read-only
    "get_current_context": {"destructive": False, "readOnly": True},
    # Repository tools - read-only
    "list_repository_tree": {"destructive": False, "readOnly": True},
    "get_file_contents": {"destructive": False, "readOnly": True},
    "search_code": {"destructive": False, "readOnly": True},
    "list_branches": {"destructive": False, "readOnly": True},
    "get_branch": {"destructive": False, "readOnly": True},
    "list_commits": {"destructive": False, "readOnly": True},
    "get_commit": {"destructive": False, "readOnly": True},
    "compare_branches": {"destructive": False, "readOnly": True},
    "list_tags": {"destructive": False, "readOnly": True},
    "get_tag": {"destructive": False, "readOnly": True},
    # Repository tools - mutating
    "create_file": {"destructive": False, "readOnly": False},
    "update_file": {"destructive": False, "readOnly": False},
    "delete_file": {"destructive": True, "readOnly": False},
    "create_branch": {"destructive": False, "readOnly": False},
    "delete_branch": {"destructive": True, "readOnly": False},
    "create_tag": {"destructive": False, "readOnly": False},
    # Issue tools - read-only
    "list_issues": {"destructive": False, "readOnly": True},
    "get_issue": {"destructive": False, "readOnly": True},
    "list_issue_comments": {"destructive": False, "readOnly": True},
    # Issue tools - mutating
    "create_issue": {"destructive": False, "readOnly": False},
    "update_issue": {"destructive": False, "readOnly": False},
    "close_issue": {"destructive": False, "readOnly": False},
    "reopen_issue": {"destructive": False, "readOnly": False},
    "add_issue_comment": {"destructive": False, "readOnly": False},
    # Merge Request tools - read-only
    "list_merge_requests": {"destructive": False, "readOnly": True},
    "get_merge_request": {"destructive": False, "readOnly": True},
    "get_merge_request_changes": {"destructive": False, "readOnly": True},
    "get_merge_request_commits": {"destructive": False, "readOnly": True},
    "get_merge_request_pipelines": {"destructive": False, "readOnly": True},
    "list_mr_comments": {"destructive": False, "readOnly": True},
    # Merge Request tools - mutating
    "create_merge_request": {"destructive": False, "readOnly": False},
    "update_merge_request": {"destructive": False, "readOnly": False},
    "close_merge_request": {"destructive": False, "readOnly": False},
    "reopen_merge_request": {"destructive": False, "readOnly": False},
    "merge_merge_request": {"destructive": False, "readOnly": False},
    "approve_merge_request": {"destructive": False, "readOnly": False},
    "unapprove_merge_request": {"destructive": False, "readOnly": False},
    "add_mr_comment": {"destructive": False, "readOnly": False},
    # Pipeline tools - read-only
    "list_pipelines": {"destructive": False, "readOnly": True},
    "get_pipeline": {"destructive": False, "readOnly": True},
    "list_pipeline_jobs": {"destructive": False, "readOnly": True},
    "get_job": {"destructive": False, "readOnly": True},
    "get_job_trace": {"destructive": False, "readOnly": True},
    "list_pipeline_variables": {"destructive": False, "readOnly": True},
    # Pipeline tools - mutating
    "create_pipeline": {"destructive": False, "readOnly": False},
    "retry_pipeline": {"destructive": False, "readOnly": False},
    "cancel_pipeline": {"destructive": True, "readOnly": False},
    "delete_pipeline": {"destructive": True, "readOnly": False},
    "retry_job": {"destructive": False, "readOnly": False},
    "cancel_job": {"destructive": True, "readOnly": False},
    "play_job": {"destructive": False, "readOnly": False},
    "download_job_artifacts": {"destructive": False, "readOnly": False},
    # Project tools - read-only
    "list_projects": {"destructive": False, "readOnly": True},
    "get_project": {"destructive": False, "readOnly": True},
    "search_projects": {"destructive": False, "readOnly": True},
    "list_project_members": {"destructive": False, "readOnly": True},
    "get_project_statistics": {"destructive": False, "readOnly": True},
    "list_milestones": {"destructive": False, "readOnly": True},
    "get_milestone": {"destructive": False, "readOnly": True},
    # Project tools - mutating
    "create_project": {"destructive": False, "readOnly": False},
    "create_milestone": {"destructive": False, "readOnly": False},
    "update_milestone": {"destructive": False, "readOnly": False},
    # Label tools - read-only
    "list_labels": {"destructive": False, "readOnly": True},
    # Label tools - mutating
    "create_label": {"destructive": False, "readOnly": False},
    "update_label": {"destructive": False, "readOnly": False},
    "delete_label": {"destructive": True, "readOnly": False},
    # Wiki tools - read-only
    "list_wiki_pages": {"destructive": False, "readOnly": True},
    "get_wiki_page": {"destructive": False, "readOnly": True},
    # Wiki tools - mutating
    "create_wiki_page": {"destructive": False, "readOnly": False},
    "update_wiki_page": {"destructive": False, "readOnly": False},
    "delete_wiki_page": {"destructive": True, "readOnly": False},
    # Snippet tools - read-only
    "list_snippets": {"destructive": False, "readOnly": True},
    "get_snippet": {"destructive": False, "readOnly": True},
    # Snippet tools - mutating
    "create_snippet": {"destructive": False, "readOnly": False},
    "update_snippet": {"destructive": False, "readOnly": False},
    "delete_snippet": {"destructive": True, "readOnly": False},
    # Release tools - read-only
    "list_releases": {"destructive": False, "readOnly": True},
    "get_release": {"destructive": False, "readOnly": True},
    # Release tools - mutating
    "create_release": {"destructive": False, "readOnly": False},
    "update_release": {"destructive": False, "readOnly": False},
    "delete_release": {"destructive": True, "readOnly": False},
    # User tools - read-only
    "get_user": {"destructive": False, "readOnly": True},
    "search_users": {"destructive": False, "readOnly": True},
    "list_user_projects": {"destructive": False, "readOnly": True},
    # Group tools - read-only
    "list_groups": {"destructive": False, "readOnly": True},
    "get_group": {"destructive": False, "readOnly": True},
    "list_group_members": {"destructive": False, "readOnly": True},
}

# Tool icons for visual metadata in MCP SDK v1.25.0
# Maps tool categories to emoji icons for better UI representation
TOOL_ICONS: dict[str, str] = {
    "pipeline": "🔄",
    "merge_request": "🔀",
    "mr": "🔀",
    "issue": "📋",
    "project": "📦",
    "repository": "📁",
    "repo": "📁",
    "branch": "🌿",
    "commit": "📝",
    "tag": "🏷️",
    "file": "📄",
    "user": "👤",
    "group": "👥",
    "label": "🏷️",
    "wiki": "📖",
    "snippet": "📝",
    "release": "🚀",
    "job": "⚙️",
    "milestone": "🎯",
    "context": "🔍",
}


def get_tool_icon(tool_name: str) -> str | None:
    """
    Get the icon for a tool based on its category.

    The icon is determined by matching keywords in the tool name
    against known categories.

    Args:
        tool_name: Name of the tool (e.g., 'list_pipelines', 'get_issue')

    Returns:
        Emoji icon string or None if no category matches.
    """
    if not tool_name:
        return None

    # Check for exact category matches in tool name
    # Order matters - more specific matches should come first
    category_priority = [
        "merge_request",
        "pipeline",
        "milestone",
        "repository",
        "release",
        "snippet",
        "project",
        "branch",
        "commit",
        "context",
        "issue",
        "label",
        "group",
        "file",
        "wiki",
        "user",
        "job",
        "tag",
        "mr",
    ]

    tool_lower = tool_name.lower()
    for category in category_priority:
        if category in tool_lower:
            return TOOL_ICONS.get(category)

    return None


def get_tool_annotations(tool_name: str) -> dict[str, bool]:
    """
    Get annotations for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Dictionary with 'destructive' and 'readOnly' boolean fields.
        Returns default (non-destructive, non-readonly) for unknown tools.
    """
    return TOOL_ANNOTATIONS.get(tool_name, {"destructive": False, "readOnly": False})


class GitLabMCPServer:
    """
    GitLab MCP Server.

    This server implements the MCP protocol and provides tools for GitLab operations.

    Attributes:
        config: GitLabConfig instance with server configuration
        gitlab_client: GitLabClient instance for GitLab API operations
        name: Server name (default: "gitlab-mcp-server")
    """

    def __init__(self, config: GitLabConfig, name: str = "gitlab-mcp-server") -> None:
        """
        Initialize the GitLab MCP Server.

        Args:
            config: GitLabConfig instance with server settings
            name: Server name (default: "gitlab-mcp-server")
        """
        self.config = config
        self.name = name
        self.gitlab_client = GitLabClient(config)
        self._tools: dict[str, dict[str, Any]] = {}

    def register_all_tools(self) -> None:
        """
        Register all available MCP tools.

        This method registers all 87 MCP tools organized by category:
        - Context tools (1)
        - Repository tools (16) - files, branches, commits, tags
        - Issue tools (8) - list, get, create, update, close, reopen, comments
        - Merge Request tools (14) - CRUD, approve, comments, changes, commits, pipelines
        - Pipeline tools (14)
        - Project tools (9)
        - Label tools (4)
        - Wiki tools (5)
        - Snippet tools (5)
        - Release tools (5)
        - User tools (3)
        - Group tools (3)
        """
        # Context tools
        self.register_tool(
            "get_current_context",
            "Get current GitLab user and server context information",
            lambda **kwargs: tools.get_current_context(self.gitlab_client, **kwargs),
        )

        # Repository tools
        self.register_tool(
            "list_repository_tree",
            "List files and directories in a repository tree",
            lambda **kwargs: tools.list_repository_tree(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_file_contents",
            "Get the contents of a file from a repository",
            lambda **kwargs: tools.get_file_contents(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "search_code",
            "Search for code in project repositories",
            lambda **kwargs: tools.search_code(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_file",
            "Create a new file in a repository with commit",
            lambda **kwargs: tools.create_file(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_file",
            "Update existing file content with commit",
            lambda **kwargs: tools.update_file(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_file",
            "Delete a file from repository with commit",
            lambda **kwargs: tools.delete_file(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_branches",
            "List all branches in a repository",
            lambda **kwargs: tools.list_branches(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_branch",
            "Get details of a specific branch",
            lambda **kwargs: tools.get_branch(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_branch",
            "Create a new branch",
            lambda **kwargs: tools.create_branch(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_branch",
            "Delete a branch",
            lambda **kwargs: tools.delete_branch(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_commit",
            "Get details of a specific commit",
            lambda **kwargs: tools.get_commit(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_commits",
            "List commits for a project or branch",
            lambda **kwargs: tools.list_commits(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "compare_branches",
            "Compare two branches, tags, or commits",
            lambda **kwargs: tools.compare_branches(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_tags",
            "List repository tags",
            lambda **kwargs: tools.list_tags(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_tag",
            "Get details of a specific tag",
            lambda **kwargs: tools.get_tag(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_tag",
            "Create a new tag",
            lambda **kwargs: tools.create_tag(self.gitlab_client, **kwargs),
        )

        # Issue tools
        self.register_tool(
            "list_issues",
            "List issues for a project",
            lambda **kwargs: tools.list_issues(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_issue",
            "Get details of a specific issue",
            lambda **kwargs: tools.get_issue(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_issue",
            "Create a new issue in a project",
            lambda **kwargs: tools.create_issue(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_issue",
            "Update an existing issue",
            lambda **kwargs: tools.update_issue(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "close_issue",
            "Close an issue",
            lambda **kwargs: tools.close_issue(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "reopen_issue",
            "Reopen a closed issue",
            lambda **kwargs: tools.reopen_issue(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "add_issue_comment",
            "Add a comment to an issue",
            lambda **kwargs: tools.add_issue_comment(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_issue_comments",
            "List all comments on an issue",
            lambda **kwargs: tools.list_issue_comments(self.gitlab_client, **kwargs),
        )

        # Merge Request tools
        self.register_tool(
            "list_merge_requests",
            "List merge requests for a project",
            lambda **kwargs: tools.list_merge_requests(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_merge_request",
            "Get details of a specific merge request",
            lambda **kwargs: tools.get_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_merge_request",
            "Create a new merge request",
            lambda **kwargs: tools.create_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_merge_request",
            "Update an existing merge request",
            lambda **kwargs: tools.update_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "merge_merge_request",
            "Merge an approved merge request",
            lambda **kwargs: tools.merge_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "close_merge_request",
            "Close a merge request without merging",
            lambda **kwargs: tools.close_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "reopen_merge_request",
            "Reopen a closed merge request",
            lambda **kwargs: tools.reopen_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "approve_merge_request",
            "Approve a merge request",
            lambda **kwargs: tools.approve_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "unapprove_merge_request",
            "Remove approval from a merge request",
            lambda **kwargs: tools.unapprove_merge_request(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_merge_request_changes",
            "Get the file changes in a merge request",
            lambda **kwargs: tools.get_merge_request_changes(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_merge_request_commits",
            "Get commits in a merge request",
            lambda **kwargs: tools.get_merge_request_commits(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_merge_request_pipelines",
            "Get pipelines for a merge request",
            lambda **kwargs: tools.get_merge_request_pipelines(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "add_mr_comment",
            "Add a comment to a merge request",
            lambda **kwargs: tools.add_mr_comment(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_mr_comments",
            "List all comments on a merge request",
            lambda **kwargs: tools.list_mr_comments(self.gitlab_client, **kwargs),
        )

        # Pipeline tools
        self.register_tool(
            "list_pipelines",
            "List pipelines for a project",
            lambda **kwargs: tools.list_pipelines(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_pipeline",
            "Get details of a specific pipeline",
            lambda **kwargs: tools.get_pipeline(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_pipeline",
            "Create a new pipeline",
            lambda **kwargs: tools.create_pipeline(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "retry_pipeline",
            "Retry a failed pipeline",
            lambda **kwargs: tools.retry_pipeline(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "cancel_pipeline",
            "Cancel a running pipeline",
            lambda **kwargs: tools.cancel_pipeline(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_pipeline",
            "Delete a pipeline",
            lambda **kwargs: tools.delete_pipeline(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_pipeline_jobs",
            "List jobs in a pipeline",
            lambda **kwargs: tools.list_pipeline_jobs(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_job",
            "Get details of a specific job",
            lambda **kwargs: tools.get_job(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_job_trace",
            "Get the trace log of a job",
            lambda **kwargs: tools.get_job_trace(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "retry_job",
            "Retry a failed job",
            lambda **kwargs: tools.retry_job(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "cancel_job",
            "Cancel a running job",
            lambda **kwargs: tools.cancel_job(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "play_job",
            "Play a manual job",
            lambda **kwargs: tools.play_job(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "download_job_artifacts",
            "Download artifacts from a job",
            lambda **kwargs: tools.download_job_artifacts(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_pipeline_variables",
            "List variables for a pipeline",
            lambda **kwargs: tools.list_pipeline_variables(self.gitlab_client, **kwargs),
        )

        # Project tools
        self.register_tool(
            "list_projects",
            "List projects accessible by the user",
            lambda **kwargs: tools.list_projects(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_project",
            "Get details of a specific project",
            lambda **kwargs: tools.get_project(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_project",
            "Create a new project in GitLab",
            lambda **kwargs: tools.create_project(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "search_projects",
            "Search for projects by name or description",
            lambda **kwargs: tools.search_projects(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_project_members",
            "List members of a project",
            lambda **kwargs: tools.list_project_members(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_project_statistics",
            "Get statistics for a project",
            lambda **kwargs: tools.get_project_statistics(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_milestones",
            "List milestones for a project",
            lambda **kwargs: tools.list_milestones(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_milestone",
            "Get details of a specific milestone",
            lambda **kwargs: tools.get_milestone(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_milestone",
            "Create a new milestone",
            lambda **kwargs: tools.create_milestone(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_milestone",
            "Update an existing milestone",
            lambda **kwargs: tools.update_milestone(self.gitlab_client, **kwargs),
        )

        # Label tools
        self.register_tool(
            "list_labels",
            "List labels for a project",
            lambda **kwargs: tools.list_labels(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_label",
            "Create a new label",
            lambda **kwargs: tools.create_label(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_label",
            "Update an existing label",
            lambda **kwargs: tools.update_label(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_label",
            "Delete a label",
            lambda **kwargs: tools.delete_label(self.gitlab_client, **kwargs),
        )

        # Wiki tools
        self.register_tool(
            "list_wiki_pages",
            "List wiki pages for a project",
            lambda **kwargs: tools.list_wiki_pages(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_wiki_page",
            "Get content of a specific wiki page",
            lambda **kwargs: tools.get_wiki_page(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_wiki_page",
            "Create a new wiki page",
            lambda **kwargs: tools.create_wiki_page(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_wiki_page",
            "Update an existing wiki page",
            lambda **kwargs: tools.update_wiki_page(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_wiki_page",
            "Delete a wiki page",
            lambda **kwargs: tools.delete_wiki_page(self.gitlab_client, **kwargs),
        )

        # Snippet tools
        self.register_tool(
            "list_snippets",
            "List snippets for a project",
            lambda **kwargs: tools.list_snippets(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_snippet",
            "Get content of a specific snippet",
            lambda **kwargs: tools.get_snippet(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_snippet",
            "Create a new snippet",
            lambda **kwargs: tools.create_snippet(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_snippet",
            "Update an existing snippet",
            lambda **kwargs: tools.update_snippet(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_snippet",
            "Delete a snippet",
            lambda **kwargs: tools.delete_snippet(self.gitlab_client, **kwargs),
        )

        # Release tools
        self.register_tool(
            "list_releases",
            "List releases for a project",
            lambda **kwargs: tools.list_releases(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_release",
            "Get details of a specific release",
            lambda **kwargs: tools.get_release(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "create_release",
            "Create a new release",
            lambda **kwargs: tools.create_release(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "update_release",
            "Update an existing release",
            lambda **kwargs: tools.update_release(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "delete_release",
            "Delete a release",
            lambda **kwargs: tools.delete_release(self.gitlab_client, **kwargs),
        )

        # User tools
        self.register_tool(
            "get_user",
            "Get details of a specific user",
            lambda **kwargs: tools.get_user(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "search_users",
            "Search for users by username or email",
            lambda **kwargs: tools.search_users(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_user_projects",
            "List projects for a specific user",
            lambda **kwargs: tools.list_user_projects(self.gitlab_client, **kwargs),
        )

        # Group tools
        self.register_tool(
            "list_groups",
            "List groups accessible by the user",
            lambda **kwargs: tools.list_groups(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "get_group",
            "Get details of a specific group",
            lambda **kwargs: tools.get_group(self.gitlab_client, **kwargs),
        )
        self.register_tool(
            "list_group_members",
            "List members of a group",
            lambda **kwargs: tools.list_group_members(self.gitlab_client, **kwargs),
        )

    async def startup(self) -> None:
        """
        Start the MCP server.

        Authenticates with GitLab and prepares the server for handling requests.

        Raises:
            NetworkError: If connection to GitLab fails
            AuthenticationError: If authentication fails
        """
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        # Authenticate with GitLab
        self.gitlab_client.authenticate()

    async def shutdown(self) -> None:
        """
        Shutdown the MCP server gracefully.

        Performs cleanup operations before server shutdown.
        """
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        # Currently no cleanup needed, but method exists for future use

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        List all available MCP tools.

        Returns:
            List of tool dictionaries with name and description
        """
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        return [
            {"name": name, "description": tool["description"]} for name, tool in self._tools.items()
        ]

    def register_tool(self, name: str, description: str, function: Callable[..., Any]) -> None:
        """
        Register a new MCP tool.

        Args:
            name: Tool name
            description: Tool description
            function: Tool function to execute
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "function": function,
        }

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """
        Call a registered MCP tool.

        Args:
            name: Tool name to call
            arguments: Tool arguments as a dictionary

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool is not found
        """
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' not found")

        tool_function = self._tools[name]["function"]
        return await tool_function(**arguments)

    def get_info(self) -> dict[str, str]:
        """
        Get server information and metadata.

        Returns:
            Dictionary with server name, version, and description
        """
        return {
            "name": self.name,
            "version": "0.1.0",
            "description": "GitLab MCP Server - Model Context Protocol server for GitLab",
        }


def _get_tool_definitions() -> list[tuple[str, str, dict[str, Any]]]:
    """
    Get tool definitions with JSON schemas for all 87 GitLab MCP tools.

    Returns:
        List of tuples: (name, description, input_schema)
    """
    return [
        # Context tools (1)
        ("get_current_context", "Get current GitLab user and server context information", {}),
        # Repository tools (6)
        (
            "list_repository_tree",
            "List files and directories in a repository tree",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "path": {"type": "string", "description": "Path in repository (optional)"},
                "ref": {
                    "type": "string",
                    "description": "Branch, tag, or commit SHA (optional, default: HEAD)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List recursively (optional, default: false)",
                },
            },
        ),
        (
            "get_file_contents",
            "Get the contents of a file from a repository",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "file_path": {"type": "string", "description": "Path to file in repository"},
                "ref": {
                    "type": "string",
                    "description": "Branch, tag, or commit SHA (optional, default: HEAD)",
                },
            },
        ),
        (
            "search_code",
            "Search for code in project repositories",
            {
                "search_term": {"type": "string", "description": DESC_SEARCH_QUERY},
                "project_id": {
                    "type": "string",
                    "description": "Project ID or path (optional, search all accessible projects if not specified)",
                },
            },
        ),
        (
            "create_file",
            "Create a new file in a repository with commit",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "file_path": {
                    "type": "string",
                    "description": "Full path for the new file (e.g., 'src/main.py')",
                },
                "branch": {
                    "type": "string",
                    "description": "Name of the branch to create the file in",
                },
                "content": {
                    "type": "string",
                    "description": "Content of the file (text or base64-encoded)",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message for the file creation",
                },
                "author_email": {
                    "type": "string",
                    "description": DESC_AUTHOR_EMAIL,
                },
                "author_name": {
                    "type": "string",
                    "description": DESC_AUTHOR_NAME,
                },
                "encoding": {
                    "type": "string",
                    "description": "Content encoding: 'text' or 'base64' (optional, default: text)",
                },
            },
        ),
        (
            "update_file",
            "Update existing file content with commit",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "file_path": {"type": "string", "description": "Full path to the file to update"},
                "branch": {
                    "type": "string",
                    "description": "Name of the branch containing the file",
                },
                "content": {
                    "type": "string",
                    "description": "New content for the file (text or base64-encoded)",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message for the file update",
                },
                "author_email": {
                    "type": "string",
                    "description": DESC_AUTHOR_EMAIL,
                },
                "author_name": {
                    "type": "string",
                    "description": DESC_AUTHOR_NAME,
                },
                "encoding": {
                    "type": "string",
                    "description": "Content encoding: 'text' or 'base64' (optional, default: text)",
                },
            },
        ),
        (
            "delete_file",
            "Delete a file from repository with commit",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "file_path": {"type": "string", "description": "Full path to the file to delete"},
                "branch": {
                    "type": "string",
                    "description": "Name of the branch containing the file",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message for the file deletion",
                },
                "author_email": {
                    "type": "string",
                    "description": DESC_AUTHOR_EMAIL,
                },
                "author_name": {
                    "type": "string",
                    "description": DESC_AUTHOR_NAME,
                },
            },
        ),
        (
            "list_branches",
            "List all branches in a repository",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "search": {
                    "type": "string",
                    "description": "Search term to filter branches (optional)",
                },
                "page": {"type": "integer", "description": DESC_PAGE},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_branch",
            "Get details of a specific branch",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "branch_name": {"type": "string", "description": "Name of the branch"},
            },
        ),
        (
            "create_branch",
            "Create a new branch",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "branch_name": {"type": "string", "description": "Name for the new branch"},
                "ref": {"type": "string", "description": DESC_SOURCE_REF},
            },
        ),
        (
            "delete_branch",
            "Delete a branch",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "branch_name": {"type": "string", "description": "Name of branch to delete"},
            },
        ),
        (
            "get_commit",
            "Get details of a specific commit",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "commit_sha": {"type": "string", "description": "Commit SHA"},
            },
        ),
        (
            "list_commits",
            "List commits for a project or branch",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "ref": {"type": "string", "description": "Branch/tag name (optional)"},
                "since": {
                    "type": "string",
                    "description": "Only commits after this date (ISO 8601, optional)",
                },
                "until": {
                    "type": "string",
                    "description": "Only commits before this date (ISO 8601, optional)",
                },
                "path": {
                    "type": "string",
                    "description": "Only commits affecting this file path (optional)",
                },
                "page": {"type": "integer", "description": DESC_PAGE},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "compare_branches",
            "Compare two branches, tags, or commits",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "from_ref": {"type": "string", "description": DESC_SOURCE_REF},
                "to_ref": {"type": "string", "description": "Target branch, tag, or commit SHA"},
                "straight": {
                    "type": "boolean",
                    "description": "Compare refs directly without merge base (optional)",
                },
            },
        ),
        (
            "list_tags",
            "List repository tags",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "search": {
                    "type": "string",
                    "description": "Search pattern to filter tags (optional)",
                },
                "page": {"type": "integer", "description": DESC_PAGE},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_tag",
            "Get details of a specific tag",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {"type": "string", "description": "Name of the tag"},
            },
        ),
        (
            "create_tag",
            "Create a new tag",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {"type": "string", "description": "Name for the new tag"},
                "ref": {"type": "string", "description": DESC_SOURCE_REF},
                "message": {
                    "type": "string",
                    "description": "Optional tag message (creates annotated tag)",
                },
            },
        ),
        # Issue tools (3)
        (
            "list_issues",
            "List issues for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: opened, closed, all (optional, default: opened)",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by labels (optional)",
                },
                "milestone": {
                    "type": "string",
                    "description": "Filter by milestone title (optional)",
                },
                "author_id": {"type": "integer", "description": "Filter by author ID (optional)"},
                "assignee_id": {
                    "type": "integer",
                    "description": "Filter by assignee ID (optional)",
                },
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_issue",
            "Get details of a specific issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
            },
        ),
        (
            "create_issue",
            "Create a new issue in a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "title": {"type": "string", "description": "Issue title"},
                "description": {"type": "string", "description": "Issue description (optional)"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels (optional)",
                },
                "assignee_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Assignee user IDs (optional)",
                },
                "milestone_id": {"type": "integer", "description": "Milestone ID (optional)"},
            },
        ),
        (
            "update_issue",
            "Update an existing issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
                "title": {"type": "string", "description": DESC_NEW_TITLE},
                "description": {"type": "string", "description": DESC_NEW_DESC},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New labels (optional)",
                },
                "assignee_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "New assignee user IDs (optional)",
                },
                "milestone_id": {"type": "integer", "description": "New milestone ID (optional)"},
                "state_event": {
                    "type": "string",
                    "description": "State event: close, reopen (optional)",
                },
            },
        ),
        (
            "close_issue",
            "Close an issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
            },
        ),
        (
            "reopen_issue",
            "Reopen a closed issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
            },
        ),
        (
            "add_issue_comment",
            "Add a comment to an issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
                "body": {"type": "string", "description": "Comment text (supports Markdown)"},
            },
        ),
        (
            "list_issue_comments",
            "List all comments on an issue",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "issue_iid": {"type": "integer", "description": DESC_ISSUE_IID},
                "page": {"type": "integer", "description": DESC_PAGE},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        # Merge Request tools (12)
        (
            "list_merge_requests",
            "List merge requests for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: opened, closed, merged, all (optional, default: opened)",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by labels (optional)",
                },
                "milestone": {
                    "type": "string",
                    "description": "Filter by milestone title (optional)",
                },
                "author_id": {"type": "integer", "description": "Filter by author ID (optional)"},
                "assignee_id": {
                    "type": "integer",
                    "description": "Filter by assignee ID (optional)",
                },
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_merge_request",
            "Get details of a specific merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "create_merge_request",
            "Create a new merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "source_branch": {"type": "string", "description": "Source branch name"},
                "target_branch": {"type": "string", "description": "Target branch name"},
                "title": {"type": "string", "description": "MR title"},
                "description": {"type": "string", "description": "MR description (optional)"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels (optional)",
                },
                "assignee_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Assignee user IDs (optional)",
                },
                "milestone_id": {"type": "integer", "description": "Milestone ID (optional)"},
            },
        ),
        (
            "update_merge_request",
            "Update an existing merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
                "title": {"type": "string", "description": DESC_NEW_TITLE},
                "description": {"type": "string", "description": DESC_NEW_DESC},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New labels (optional)",
                },
                "assignee_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "New assignee user IDs (optional)",
                },
                "milestone_id": {"type": "integer", "description": "New milestone ID (optional)"},
            },
        ),
        (
            "merge_merge_request",
            "Merge an approved merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
                "merge_commit_message": {
                    "type": "string",
                    "description": "Merge commit message (optional)",
                },
                "should_remove_source_branch": {
                    "type": "boolean",
                    "description": "Remove source branch after merge (optional)",
                },
            },
        ),
        (
            "close_merge_request",
            "Close a merge request without merging",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "reopen_merge_request",
            "Reopen a closed merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "approve_merge_request",
            "Approve a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "unapprove_merge_request",
            "Remove approval from a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "get_merge_request_changes",
            "Get the file changes in a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "get_merge_request_commits",
            "Get commits in a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "get_merge_request_pipelines",
            "Get pipelines for a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
            },
        ),
        (
            "add_mr_comment",
            "Add a comment to a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
                "body": {"type": "string", "description": "Comment text (supports Markdown)"},
            },
        ),
        (
            "list_mr_comments",
            "List all comments on a merge request",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "mr_iid": {"type": "integer", "description": DESC_MR_IID},
                "page": {"type": "integer", "description": DESC_PAGE},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        # Pipeline tools (14)
        (
            "list_pipelines",
            "List pipelines for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "ref": {"type": "string", "description": "Filter by branch/tag (optional)"},
                "status": {
                    "type": "string",
                    "description": "Filter by status: running, pending, success, failed, canceled (optional)",
                },
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_pipeline",
            "Get details of a specific pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        (
            "create_pipeline",
            "Create a new pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "ref": {"type": "string", "description": "Branch or tag name"},
                "variables": {"type": "object", "description": "Pipeline variables (optional)"},
            },
        ),
        (
            "retry_pipeline",
            "Retry a failed pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        (
            "cancel_pipeline",
            "Cancel a running pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        (
            "delete_pipeline",
            "Delete a pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        (
            "list_pipeline_jobs",
            "List jobs in a pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        (
            "get_job",
            "Get details of a specific job",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
            },
        ),
        (
            "get_job_trace",
            "Get the trace log of a job. Use tail_lines parameter to limit output for large logs.",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
                "tail_lines": {
                    "type": "integer",
                    "description": "Optional: Number of lines to return from end of log (e.g., 500-1000 for error analysis). Prevents exceeding token limits on large logs.",
                },
            },
        ),
        (
            "retry_job",
            "Retry a failed job",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
            },
        ),
        (
            "cancel_job",
            "Cancel a running job",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
            },
        ),
        (
            "play_job",
            "Play a manual job",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
            },
        ),
        (
            "download_job_artifacts",
            "Download artifacts from a job",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "job_id": {"type": "integer", "description": DESC_JOB_ID},
                "artifact_path": {
                    "type": "string",
                    "description": "Path to specific artifact (optional, download all if not specified)",
                },
            },
        ),
        (
            "list_pipeline_variables",
            "List variables for a pipeline",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "pipeline_id": {"type": "integer", "description": DESC_PIPELINE_ID},
            },
        ),
        # Project tools (9)
        (
            "list_projects",
            "List projects accessible by the user",
            {
                "visibility": {
                    "type": "string",
                    "description": "Filter by visibility: public, internal, private (optional)",
                },
                "owned": {"type": "boolean", "description": "Limit to owned projects (optional)"},
                "membership": {
                    "type": "boolean",
                    "description": "Limit to projects where user is a member (optional)",
                },
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_project",
            "Get details of a specific project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "create_project",
            "Create a new project in GitLab",
            {
                "name": {
                    "type": "string",
                    "description": "Project name (required)",
                },
                "path": {
                    "type": "string",
                    "description": "Project path/slug (optional, defaults to name if not provided)",
                },
                "namespace_id": {
                    "type": "integer",
                    "description": "ID of the namespace/group to create project in (optional)",
                },
                "description": {
                    "type": "string",
                    "description": "Project description (optional)",
                },
                "visibility": {
                    "type": "string",
                    "description": "Project visibility: 'private', 'internal', or 'public' (optional, default: private)",
                },
                "initialize_with_readme": {
                    "type": "boolean",
                    "description": "Initialize project with a README.md (optional, default: false)",
                },
            },
        ),
        (
            "search_projects",
            "Search for projects by name or description",
            {
                "search_term": {"type": "string", "description": DESC_SEARCH_QUERY},
            },
        ),
        (
            "list_project_members",
            "List members of a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "get_project_statistics",
            "Get statistics for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "list_milestones",
            "List milestones for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: active, closed, all (optional, default: active)",
                },
            },
        ),
        (
            "get_milestone",
            "Get details of a specific milestone",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "milestone_id": {"type": "integer", "description": "Milestone ID"},
            },
        ),
        (
            "create_milestone",
            "Create a new milestone",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "title": {"type": "string", "description": "Milestone title"},
                "description": {
                    "type": "string",
                    "description": "Milestone description (optional)",
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date (YYYY-MM-DD format, optional)",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD format, optional)",
                },
            },
        ),
        (
            "update_milestone",
            "Update an existing milestone",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "milestone_id": {"type": "integer", "description": "Milestone ID"},
                "title": {"type": "string", "description": DESC_NEW_TITLE},
                "description": {"type": "string", "description": DESC_NEW_DESC},
                "due_date": {
                    "type": "string",
                    "description": "New due date (YYYY-MM-DD format, optional)",
                },
                "start_date": {
                    "type": "string",
                    "description": "New start date (YYYY-MM-DD format, optional)",
                },
                "state_event": {
                    "type": "string",
                    "description": "State event: close, activate (optional)",
                },
            },
        ),
        # Label tools (4)
        (
            "list_labels",
            "List labels for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "create_label",
            "Create a new label",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "name": {"type": "string", "description": "Label name"},
                "color": {
                    "type": "string",
                    "description": "Label color (hex format, e.g., '#FF0000')",
                },
                "description": {"type": "string", "description": "Label description (optional)"},
            },
        ),
        (
            "update_label",
            "Update an existing label",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "name": {"type": "string", "description": "Current label name"},
                "new_name": {"type": "string", "description": "New label name (optional)"},
                "color": {"type": "string", "description": "New color (hex format, optional)"},
                "description": {"type": "string", "description": DESC_NEW_DESC},
            },
        ),
        (
            "delete_label",
            "Delete a label",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "name": {"type": "string", "description": "Label name"},
            },
        ),
        # Wiki tools (5)
        (
            "list_wiki_pages",
            "List wiki pages for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "get_wiki_page",
            "Get content of a specific wiki page",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "slug": {
                    "type": "string",
                    "description": DESC_WIKI_SLUG,
                },
            },
        ),
        (
            "create_wiki_page",
            "Create a new wiki page",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "title": {"type": "string", "description": "Page title"},
                "content": {"type": "string", "description": "Page content (Markdown format)"},
            },
        ),
        (
            "update_wiki_page",
            "Update an existing wiki page",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "slug": {
                    "type": "string",
                    "description": DESC_WIKI_SLUG,
                },
                "title": {"type": "string", "description": "New page title (optional)"},
                "content": {"type": "string", "description": "New page content (optional)"},
            },
        ),
        (
            "delete_wiki_page",
            "Delete a wiki page",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "slug": {
                    "type": "string",
                    "description": DESC_WIKI_SLUG,
                },
            },
        ),
        # Snippet tools (5)
        (
            "list_snippets",
            "List snippets for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "get_snippet",
            "Get content of a specific snippet",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "snippet_id": {"type": "integer", "description": DESC_SNIPPET_ID},
            },
        ),
        (
            "create_snippet",
            "Create a new snippet",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "title": {"type": "string", "description": "Snippet title"},
                "file_name": {"type": "string", "description": "File name"},
                "content": {"type": "string", "description": "Snippet content"},
                "visibility": {
                    "type": "string",
                    "description": "Visibility: private, internal, public (optional, default: private)",
                },
            },
        ),
        (
            "update_snippet",
            "Update an existing snippet",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "snippet_id": {"type": "integer", "description": DESC_SNIPPET_ID},
                "title": {"type": "string", "description": DESC_NEW_TITLE},
                "file_name": {"type": "string", "description": "New file name (optional)"},
                "content": {"type": "string", "description": "New content (optional)"},
                "visibility": {"type": "string", "description": "New visibility (optional)"},
            },
        ),
        (
            "delete_snippet",
            "Delete a snippet",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "snippet_id": {"type": "integer", "description": DESC_SNIPPET_ID},
            },
        ),
        # Release tools (5)
        (
            "list_releases",
            "List releases for a project",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
            },
        ),
        (
            "get_release",
            "Get details of a specific release",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {
                    "type": "string",
                    "description": DESC_TAG_RELEASE,
                },
            },
        ),
        (
            "create_release",
            "Create a new release",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {"type": "string", "description": DESC_TAG_NAME},
                "name": {"type": "string", "description": "Release name"},
                "description": {"type": "string", "description": "Release description (optional)"},
                "ref": {
                    "type": "string",
                    "description": "Commit SHA, branch, or tag (optional, default: default branch)",
                },
            },
        ),
        (
            "update_release",
            "Update an existing release",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {"type": "string", "description": DESC_TAG_NAME},
                "name": {"type": "string", "description": "New release name (optional)"},
                "description": {
                    "type": "string",
                    "description": "New release description (optional)",
                },
            },
        ),
        (
            "delete_release",
            "Delete a release",
            {
                "project_id": {
                    "type": "string",
                    "description": DESC_PROJECT_ID,
                },
                "tag_name": {"type": "string", "description": DESC_TAG_NAME},
            },
        ),
        # User tools (3)
        (
            "get_user",
            "Get details of a specific user",
            {
                "user_id": {"type": "integer", "description": "User ID"},
            },
        ),
        (
            "search_users",
            "Search for users by username or email",
            {
                "search": {"type": "string", "description": DESC_SEARCH_QUERY},
            },
        ),
        (
            "list_user_projects",
            "List projects for a specific user",
            {
                "user_id": {"type": "integer", "description": "User ID"},
            },
        ),
        # Group tools (3)
        (
            "list_groups",
            "List groups accessible by the user",
            {
                "owned": {"type": "boolean", "description": "Limit to owned groups (optional)"},
                "per_page": {
                    "type": "integer",
                    "description": DESC_PER_PAGE,
                },
            },
        ),
        (
            "get_group",
            "Get details of a specific group",
            {
                "group_id": {"type": "string", "description": "Group ID or path"},
            },
        ),
        (
            "list_group_members",
            "List members of a group",
            {
                "group_id": {"type": "string", "description": "Group ID or path"},
            },
        ),
    ]


def _get_meta_tool_definitions() -> list[tuple[str, str, dict[str, Any]]]:
    """
    Get meta-tool definitions for slim mode (3 tools instead of 87).

    These meta-tools enable lazy loading of the full tool set,
    reducing context window usage by ~95%.

    Returns:
        List of tuples: (name, description, input_schema)
    """
    return [
        (
            "discover_tools",
            "Discover available GitLab tools by category. Returns tool names and descriptions. "
            "Categories: context, repositories, issues, merge_requests, pipelines, projects, "
            "labels, wikis, snippets, releases, users, groups",
            {
                "category": {
                    "type": "string",
                    "description": "Category to filter (optional). If omitted, returns all categories.",
                },
            },
        ),
        (
            "get_tool_schema",
            "Get the full JSON schema for a specific GitLab tool. "
            "Use after discover_tools to get parameter details before calling execute_tool.",
            {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to get schema for (e.g., 'list_merge_requests')",
                },
            },
        ),
        (
            "execute_tool",
            "Execute any GitLab tool by name with the provided arguments. "
            "Use after getting the schema to understand required parameters.",
            {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to execute (e.g., 'list_merge_requests')",
                },
                "arguments": {
                    "type": "object",
                    "description": "Tool-specific arguments (optional). See get_tool_schema for details.",
                },
            },
        ),
    ]


def _build_tool_schema(params_schema: dict[str, Any]) -> dict[str, Any]:
    """
    Build JSON schema for a tool from its parameter definitions.

    Args:
        params_schema: Dictionary of parameter definitions

    Returns:
        JSON schema dictionary with properties and required fields
    """
    properties = {}
    required = []

    for param_name, param_def in params_schema.items():
        properties[param_name] = param_def.copy()
        # Mark parameters as required if they don't have "optional" in description
        if "optional" not in param_def.get("description", "").lower():
            required.append(param_name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }

    if required:
        input_schema["required"] = required

    return input_schema


async def async_main(
    transport: Literal["stdio", "http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8500,
    mode: Literal["full", "slim"] = "full",
    insecure_no_auth: bool = False,
) -> None:
    """
    Async main entry point for the GitLab MCP Server.

    Args:
        transport: Transport protocol - "stdio" for local clients, "http" for remote
        host: Host to bind HTTP server to (only used with http transport)
        port: Port for HTTP server (only used with http transport)
        mode: Tool mode - "full" for all 87 tools, "slim" for 3 meta-tools

    Supports two transports:
    - stdio: Standard I/O transport for local CLI clients (Claude Code)
    - http: Streamable HTTP transport for remote clients (IBM ContextForge)
    """
    # Load configuration from environment
    config = load_config()

    # Create the MCP server instance
    server = Server("gitlab-mcp-server")

    # Create GitLab client
    client = GitLabClient(config)

    # Authenticate client on startup
    try:
        client.authenticate()
    except Exception as e:
        # Log detailed error for debugging (not exposed to users)
        logger.error("GitLab authentication failed: %s", e, exc_info=True)
        # Print generic message to stderr (no sensitive details)
        print(
            "Failed to authenticate with GitLab. Check your credentials and URL.", file=sys.stderr
        )
        sys.exit(1)

    # Get tool definitions based on mode
    if mode == "slim":
        tool_defs = _get_meta_tool_definitions()
        logger.info("Starting in SLIM mode with 3 meta-tools (lazy loading)")
    else:
        tool_defs = _get_tool_definitions()
        logger.info("Starting in FULL mode with %d tools", len(tool_defs))

    # Register list_tools handler
    @server.list_tools()
    async def list_tools() -> list[Any]:
        """List all available GitLab tools."""
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        from mcp.types import Tool

        return [
            Tool(
                name=name,
                description=description,
                inputSchema=_build_tool_schema(params_schema),
            )
            for name, description, params_schema in tool_defs
        ]

    # Register call_tool handler
    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        """Execute a GitLab tool by name with the provided arguments."""
        from mcp.types import TextContent

        # Route tool calls to appropriate functions
        tool_func = getattr(tools, name, None)
        if tool_func is None:
            raise ValueError(f"Unknown tool: {name}")

        try:
            # Call the tool function with client and arguments
            result = await tool_func(client, **arguments)

            # Convert result to MCP response format
            import json

            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            # Log detailed error for debugging (not exposed to clients)
            logger.error("Tool '%s' execution failed: %s", name, e, exc_info=True)
            # Return generic error message (no sensitive details)
            return [TextContent(type="text", text=f"Error executing {name}: operation failed")]

    # Initialize registries for MCP protocol features
    resource_registry = ResourceRegistry()
    prompt_registry = PromptRegistry()

    # Register list_resources handler (MCP Resources feature)
    @server.list_resources()
    async def list_resources() -> list[Any]:
        """List all available GitLab resources."""
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        return _build_resources_list(resource_registry)

    # Register list_resource_templates handler (MCP Resources feature)
    @server.list_resource_templates()
    async def list_resource_templates() -> list[Any]:
        """List all available GitLab resource templates."""
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        return _build_resource_templates_list(resource_registry)

    # Register read_resource handler (MCP Resources feature)
    @server.read_resource()
    async def read_resource(uri: str) -> Any:
        """Read a GitLab resource by URI."""
        import json

        from mcp.types import TextResourceContents
        from pydantic import AnyUrl

        result = await read_resource_handler(uri, client)
        content = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
        return TextResourceContents(uri=AnyUrl(uri), mimeType="application/json", text=content)

    # Register list_prompts handler (MCP Prompts feature)
    @server.list_prompts()
    async def list_prompts() -> list[Any]:
        """List all available GitLab workflow prompts."""
        await asyncio.sleep(0)  # Allow event loop to process other tasks
        return _build_prompts_list(prompt_registry)

    # Register get_prompt handler (MCP Prompts feature)
    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> Any:
        """Get a GitLab workflow prompt with formatted messages."""
        return _build_prompt_messages(prompt_registry, name, arguments or {})

    # Run the server with the selected transport
    if transport == "stdio":
        # Standard I/O transport for local CLI clients
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    elif transport == "http":
        # Streamable HTTP transport for remote clients
        await _run_http_server(server, host, port, insecure_no_auth=insecure_no_auth)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the GitLab MCP Server."""
    parser = argparse.ArgumentParser(
        description="GitLab MCP Server - Model Context Protocol server for GitLab",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with stdio transport (default, for Claude Code)
  gitlab-mcp-server

  # Run with Streamable HTTP transport (for ContextForge)
  gitlab-mcp-server --transport http --port 8000

  # Run in slim mode with only 3 meta-tools
  gitlab-mcp-server --transport http --port 8000 --mode slim
        """,
    )

    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol: stdio (default) for local clients, http for remote clients",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind HTTP server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8500,
        help="Port for HTTP server (default: 8500)",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "slim"],
        default="full",
        help="Tool mode: full (87 tools) or slim (3 meta-tools for lazy loading)",
    )
    parser.add_argument(
        "--insecure-no-auth",
        action="store_true",
        help="Skip OAuth/passphrase in http mode. ONLY for short manual tests.",
    )

    return parser.parse_args()


def main() -> None:
    """CLI entry point for the GitLab MCP Server."""
    args = parse_args()
    asyncio.run(
        async_main(
            transport=args.transport,
            host=args.host,
            port=args.port,
            mode=args.mode,
            insecure_no_auth=args.insecure_no_auth,
        )
    )


if __name__ == "__main__":
    main()
