# Claude Code Development Guide - GitLab MCP Server

## Ground Rules

### Test-Driven Development (TDD) - MANDATORY
**ALWAYS write tests first. Watch them fail. Then build code to pass them.**

1. **Red**: Write a failing test that defines desired behavior
2. **Green**: Write minimal code to make the test pass
3. **Refactor**: Improve code while keeping tests green

**No exceptions. No shortcuts. No "I'll add tests later."**

### Code Coverage Requirements
- **Minimum**: 80% code coverage per phase
- **Target**: 100% test pass rate
- **Gate**: Cannot proceed to next phase without meeting both criteria

### Phase Gate Criteria
Before moving to next phase, ALL must be true:
- ✅ All phase tests written (TDD process followed)
- ✅ 100% of tests passing (no failures, no skips)
- ✅ ≥80% code coverage for phase code
- ✅ Phase documentation complete
- ✅ Session logs updated
- ✅ `next_session_plan.md` updated

### Session Management Protocol
**CRITICAL**: Always maintain continuity between sessions.

#### Starting a Session (After Context Reset)
1. Read **this file** (`CLAUDE.md`) for ground rules
2. Read `docs/gitlab-mcp-server-prd.md` for product requirements and feature specs
3. Read `next_session_plan.md` for current state and next steps
4. Review current phase documentation
5. Check test coverage and status

#### During Session
- Follow TDD rigorously
- Update documentation as you go
- Track decisions and issues
- Run tests frequently

#### Ending a Session (MANDATORY BEFORE CONTEXT RESET)
1. Archive current session to `docs/session_management/sessions/`
2. Update `docs/session_management/session_index.md`
3. **MOST CRITICAL**: Update `next_session_plan.md` with:
   - Current phase and exact progress
   - What was completed this session
   - Current test coverage numbers
   - Any blockers or issues
   - **Specific** next steps (file names, function names, line numbers)
4. Ensure all tests are passing
5. Commit if at stable checkpoint

### Documentation Requirements

#### Code Documentation
- **Docstrings**: Every module, class, and function
- **Type Hints**: All function signatures
- **Inline Comments**: For complex logic only

#### Reference Documentation
Maintain these documents as code evolves:
- `docs/api/tools_reference.md` - All MCP tools
- `docs/api/gitlab_api_mapping.md` - GitLab API endpoints
- `docs/architecture/interfaces.md` - Interface contracts
- `docs/architecture/data_flow.md` - How data flows through system

#### User Documentation
- `docs/user/installation.md` - Setup instructions
- `docs/user/configuration.md` - Config guide
- `docs/user/usage_examples.md` - How to use each tool
- `docs/user/troubleshooting.md` - Common issues

### Testing Standards

#### Test Organization
```
tests/
├── unit/          # Fast, isolated tests (mock external deps)
├── integration/   # Test interactions with GitLab API (use test instance)
└── e2e/          # Full MCP protocol tests
```

#### Test Requirements
- **Unit tests**: Every function, every branch, edge cases
- **Integration tests**: Real API calls (where feasible)
- **E2E tests**: Complete user workflows
- **Fixtures**: Reusable test data in `tests/fixtures/`
- **Mocks**: Mock GitLab API responses for unit tests

#### Test Naming
```python
# Pattern: test_<function>_<scenario>_<expected>
def test_get_issue_valid_id_returns_issue():
    pass

def test_get_issue_invalid_id_raises_not_found():
    pass

def test_create_merge_request_missing_title_raises_validation_error():
    pass
```

### Code Quality Standards

#### Style
- **PEP 8**: Follow Python style guide
- **Line Length**: 100 characters max
- **Imports**: Organized (stdlib, third-party, local)
- **Formatting**: Use `black` or `ruff format`

#### Type Safety
- **Type hints**: Required on all functions
- **Pydantic**: Use for data validation
- **mypy**: Run type checking (target: no errors)

#### Error Handling
- **Specific exceptions**: Never bare `except:`
- **Custom exceptions**: In `client/exceptions.py`
- **Error messages**: Clear, actionable, user-friendly
- **Logging**: Log errors with context

### Git Commit Standards

#### Commit Message Format
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**: feat, fix, docs, test, refactor, chore
**Example**:
```
feat(repos): add search_code tool with pagination

- Implement search_code function in tools/repos/
- Add unit tests with mocked GitLab API responses
- Add integration test with test GitLab instance
- Update tools_reference.md with search_code documentation

Coverage: 85% (+5%)
Tests: 47 passing
```

#### Git Repository Setup

**Repository**: https://gitlab.prod.thezephyrco.com/mcps/gitlab_mcp.git

**Initial Setup** (if not already done):
```bash
git init
git branch -m main
git remote add origin https://gitlab.prod.thezephyrco.com/mcps/gitlab_mcp.git
```

**Configure User** (repository-specific):
```bash
git config user.email "wade.woolwine@gmail.com"
git config user.name "wadew"
```

**Pushing to GitLab**:

The repository uses Personal Access Token (PAT) authentication. The PAT is stored in `.env` file (which is gitignored).

To push to GitLab:
```bash
# Configure remote URL with PAT from .env
source .env && git remote set-url origin "https://oauth2:${GITLAB_TOKEN}@gitlab.prod.thezephyrco.com/mcps/gitlab_mcp.git"

# Push to remote
git push -u origin main
```

**Note**: The `.env` file must contain `GITLAB_TOKEN=<your-token>` for this to work.

### Project Structure Reference
```
gitlab_mcp/
├── CLAUDE.md (this file - development guide)
├── next_session_plan.md (CRITICAL handoff doc)
├── docs/
│   ├── gitlab-mcp-server-prd.md (Product Requirements - THE SOURCE OF TRUTH)
│   ├── session_management/
│   ├── architecture/
│   ├── api/
│   ├── development/
│   ├── user/
│   └── phases/
├── src/gitlab_mcp/
│   ├── server.py
│   ├── config/
│   ├── client/
│   ├── tools/
│   ├── schemas/
│   └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── pyproject.toml
```

### Key Documentation Hierarchy

1. **`docs/gitlab-mcp-server-prd.md`** - Product Requirements Document
   - Source of truth for ALL features and requirements
   - Defines WHAT to build and WHY
   - Reference this for feature IDs (e.g., REPO-001, MR-005)

2. **`CLAUDE.md`** (this file) - Development Guide
   - HOW to build (processes, standards, workflows)
   - TDD requirements and quality gates

3. **`next_session_plan.md`** - Current State
   - WHERE we are in the implementation
   - WHAT to do next

### Development Workflow

#### 1. Pick a Task
- Reference `next_session_plan.md`
- Choose smallest testable unit
- Understand requirements fully

#### 2. Write Tests (TDD Red)
```bash
# Create test file if needed
touch tests/unit/test_<module>/test_<feature>.py

# Write failing test
pytest tests/unit/test_<module>/test_<feature>.py -v
# Verify it fails for the RIGHT reason
```

#### 3. Implement Code (TDD Green)
```bash
# Write minimal code to pass test
pytest tests/unit/test_<module>/test_<feature>.py -v
# Verify test passes
```

#### 4. Refactor (TDD Refactor)
```bash
# Improve code quality
pytest tests/ -v --cov=src/gitlab_mcp
# Ensure all tests still pass
```

#### 5. Check Coverage
```bash
# Must be ≥80%
pytest tests/ --cov=src/gitlab_mcp --cov-report=term-missing
```

#### 6. Update Documentation
- Update relevant docs in `docs/api/` and `docs/architecture/`
- Add usage examples if user-facing feature
- Document any design decisions

#### 7. Session Checkpoint
- Update session notes
- Update `next_session_plan.md` if significant progress

### Python Environment Management

**Use `uv` for all Python environment operations.**

uv is a fast Python package installer and virtual environment manager written in Rust. It's significantly faster than pip and venv.

#### Setup Environment
```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install project with dev dependencies
uv pip install -e ".[dev]"

# Or install specific packages
uv pip install pytest black ruff mypy
```

#### Update Dependencies
```bash
# Sync with latest versions in pyproject.toml
uv pip sync requirements.txt  # If you generate one

# Install new dependency
uv pip install package-name

# Update all packages
uv pip install --upgrade -e ".[dev]"
```

### Quick Reference Commands

```bash
# Setup (first time)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=src/gitlab_mcp --cov-report=term-missing

# Run specific test file
pytest tests/unit/test_config/test_settings.py -v

# Run specific test function
pytest tests/unit/test_config/test_settings.py::test_load_config_from_env -v

# Run only unit tests
pytest tests/unit/ -v

# Type check
mypy src/gitlab_mcp

# Format code
black src/ tests/

# Lint
ruff check src/ tests/
```

### Web Transport (claude.ai) & ngrok

`scripts/start-web.ps1` exposes the MCP HTTP server through an ngrok tunnel for claude.ai's Custom Connector. Auth = OAuth + `MCP_PASSPHRASE` (gated form). Stop via `scripts/stop-web.ps1`.

Env vars used by the script (loaded from `.env`):
- `MCP_PASSPHRASE` (required, unless `-Insecure`) — gates the OAuth login form
- `MCP_PORT` (default 8500) — local port for the HTTP server
- `GITLAB_NGROK_AUTHTOKEN` (optional) — passed to ngrok as `--authtoken`. Falls back to `NGROK_AUTHTOKEN`, then ngrok global config.
- `GITLAB_NGROK_DOMAIN` (optional) — static domain (no `https://` prefix). Passed as `--url https://<domain>`.

#### Running alongside other ngrok-based MCPs

ngrok free plan = **1 static domain per account**. When this MCP runs in parallel with another (e.g. mcp-kanboard) on the same machine, both must NOT share the same ngrok account, otherwise the second agent fails with `ERR_NGROK_334` ("endpoint already online").

Setup:
1. Register a second ngrok account (separate email). It gets its own static domain like `junkman-undrafted-ninja.ngrok-free.dev`.
2. In `.env`, set both `GITLAB_NGROK_AUTHTOKEN` (that account's token) and `GITLAB_NGROK_DOMAIN` (that account's static domain).
3. `start-web.ps1` passes them as explicit CLI flags to ngrok (`--authtoken` + `--url https://<domain>`). CLI flags override ngrok global config — required because env-var-only setups can lose to a stale `ngrok config add-authtoken` from the other account.

Script behavior:
- `Stop-NgrokOnPort` only kills ngrok agents whose cmdline targets *this* MCP's `$Port` (matched with `\b` word boundary). Agents for other MCPs survive.
- When `GITLAB_NGROK_DOMAIN` is set, the script skips probing `/api/tunnels` and trusts the explicit URL.
- When it's NOT set, the script probes ports 4040–4044 (second agent's web inspector bumps to 4041, third to 4042, etc.) and filters tunnels by `config.addr` containing `:$Port` to avoid picking up the wrong agent's tunnel.
- ngrok stdout/stderr are captured to `ngrok.log` / `ngrok-err.log` so silent failures are diagnosable.

#### Things to NOT do in `scripts/*.ps1`

- **Don't put non-ASCII characters in `scripts/*.ps1`.** Windows PowerShell 5.1 reads `.ps1` files without BOM as Windows-1252, not UTF-8. Em dashes (`—`), multiplication signs (`×`), curly quotes, etc. get mis-decoded and the parser breaks with cryptic "Missing expression after ','" errors on innocent comment lines. Stick to plain ASCII (`--`, `x`, straight quotes).
- **Don't drop `--authtoken` / `--url` from the ngrok launch** when env vars are present. CLI flags are the only authoritative source — `NGROK_AUTHTOKEN` env var alone can lose to a previously-installed global config token (`ngrok config add-authtoken`), causing the wrong account to be used and `ERR_NGROK_334`.
- **Don't widen `Stop-NgrokOnPort`'s cmdline regex** beyond `http\s+$LocalPort\b`. Removing `\b` makes port `8500` match `85000`, `85001`, etc., and could kill ngrok agents for unrelated MCPs.

### Phase Progression

**Current Phase**: See `next_session_plan.md`

**Phase Order**:
1. Phase 1: Foundation (server, config, client, auth)
2. Phase 2: Repository & Issues tools
3. Phase 3: Merge Requests & Pipelines tools
4. Phase 4: Advanced features (security, wikis, etc.)

**Remember**: Cannot skip phases. Cannot proceed without meeting gate criteria.

### When in Doubt

1. **Check**: `next_session_plan.md` for current state
2. **Refer**: `docs/gitlab-mcp-server-prd.md` for feature requirements and specifications
3. **Read**: Relevant docs in `docs/architecture/` or `docs/api/`
4. **Test**: Write a test to clarify expected behavior
5. **Document**: Record decision in session log
6. **Ask**: User for clarification if truly ambiguous

---

## Philosophy

**Test-first is non-negotiable.** Tests are executable specifications. They document behavior, prevent regressions, and enable fearless refactoring.

**Documentation is love for future-you.** Context resets will happen. Session logs and plans are your lifeline.

**Quality over speed.** 80% coverage isn't a ceiling, it's a floor. Aim higher.

**Small, focused commits.** Each should tell a story and pass all tests.

---

**Last Updated**: 2025-10-22
**Version**: 1.0
