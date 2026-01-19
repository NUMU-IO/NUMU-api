# Pre-commit and CI/CD Setup Guide

This document explains the pre-commit hooks and GitHub Actions workflows configured for the NUMU API project.

## Pre-commit Hooks

Pre-commit hooks automatically run code quality checks before each commit, ensuring code consistency and catching issues early.

### Installation

1. **Install pre-commit** (if not already installed):
   ```bash
   pip install pre-commit
   ```

2. **Install the git hooks**:
   ```bash
   pre-commit install
   pre-commit install --hook-type commit-msg
   ```

3. **Run hooks manually** (optional):
   ```bash
   pre-commit run --all-files
   ```

### Configured Hooks

The `.pre-commit-config.yaml` file includes the following hooks:

#### 1. **General File Checks**
- Trailing whitespace removal
- End-of-file fixer
- YAML, JSON, and TOML validation
- Large file detection (max 1MB)
- Merge conflict detection
- Mixed line ending fixes

#### 2. **Python Code Quality**
- **Ruff**: Fast Python linter and formatter
  - Automatically fixes code style issues
  - Enforces import sorting
  - Checks for common bugs and anti-patterns
- **MyPy**: Static type checking
  - Ensures type safety across the codebase
  - Excludes tests and alembic migrations

#### 3. **Security Checks**
- **Bandit**: Security vulnerability scanner
  - Detects common security issues in Python code
  - Configuration in `pyproject.toml`
- **Safety**: Dependency vulnerability checker
  - Scans dependencies for known security vulnerabilities

#### 4. **Infrastructure**
- **Hadolint**: Dockerfile linter
  - Ensures Docker best practices

#### 5. **Commit Message Linting**
- **Conventional Commits**: Enforces conventional commit message format
  - Format: `type(scope): description`
  - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`
  - Example: `feat(auth): add JWT refresh token support`

### Skipping Hooks

If you need to skip hooks temporarily (not recommended):
```bash
git commit --no-verify -m "your message"
```

### Updating Hooks

To update all hooks to their latest versions:
```bash
pre-commit autoupdate
```

## GitHub Actions Workflows

### 1. Pull Request Notifications (`notify-pr.yml`)

**Triggers:**
- Pull request opened
- Pull request reopened
- Pull request closed (merged or not)
- Pull request marked as ready for review

**Target Branches:**
- `dev`
- `master`
- `stage`
- `prod`

**Slack Notification Includes:**
- PR title and description
- Author information
- Source and target branches
- PR status (opened, merged, closed, ready for review)
- Code statistics (additions, deletions, files changed)
- Direct links to PR and changes

**Status Indicators:**
- ✅ Merged (green)
- ❌ Closed without merge (red)
- 👀 Ready for review (blue)
- ♻️ Reopened (orange)
- 🚀 Opened (blue)

### 2. Commit Notifications (`notify-commit.yml`)

**Triggers:**
- Push to main branches: `dev`, `master`, `stage`, `prod`
- Push to feature branches: `feat/**`, `fix/**`, `hotfix/**`

**Slack Notification Includes:**
- Commit message
- Author information
- Branch name
- Short commit SHA
- Number of files changed
- Commit type detection with emoji
- Direct links to commit and repository

**Commit Type Detection:**
- ✨ Feature (`feat:`, `feature:`)
- 🐛 Bug Fix (`fix:`)
- 📝 Documentation (`docs:`, `doc:`)
- ♻️ Refactor (`refactor:`)
- ✅ Test (`test:`)
- 🔧 Chore (`chore:`, `build:`, `ci:`)
- ⚡ Performance (`perf:`)
- 🔥 Hotfix (`hotfix:`, `urgent:`)
- 📦 Update (default)

## Setup Requirements

### Slack Webhook Configuration

To enable Slack notifications, you need to configure a Slack webhook:

1. **Create a Slack App:**
   - Go to https://api.slack.com/apps
   - Click "Create New App" → "From scratch"
   - Name your app (e.g., "NUMU GitHub Notifications")
   - Select your workspace

2. **Enable Incoming Webhooks:**
   - In your app settings, go to "Incoming Webhooks"
   - Toggle "Activate Incoming Webhooks" to On
   - Click "Add New Webhook to Workspace"
   - Select the channel where notifications should be posted
   - Copy the webhook URL

3. **Add Webhook to GitHub Secrets:**
   - Go to your GitHub repository
   - Navigate to Settings → Secrets and variables → Actions
   - Click "New repository secret"
   - Name: `SLACK_WEBHOOK_URL`
   - Value: Paste your webhook URL
   - Click "Add secret"

### Testing Workflows

After setup, test the workflows:

1. **Test commit notifications:**
   ```bash
   git commit --allow-empty -m "test: verify commit notifications"
   git push
   ```

2. **Test PR notifications:**
   - Create a new branch
   - Make changes and push
   - Open a pull request to `dev`
   - Check your Slack channel for notifications

## Best Practices

### Commit Messages

Follow the conventional commits format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Examples:**
```
feat(auth): add JWT refresh token support

Implement refresh token rotation for enhanced security.
Tokens expire after 7 days and can be refreshed once.

Closes #123
```

```
fix(orders): resolve duplicate order creation bug

Fixed race condition in order creation endpoint that caused
duplicate orders when users clicked submit multiple times.
```

### Code Quality

- Run pre-commit hooks before pushing: `pre-commit run --all-files`
- Fix all linting and type errors
- Ensure tests pass
- Keep commits atomic and focused
- Write descriptive commit messages

### Pull Requests

- Provide clear PR descriptions
- Link related issues
- Request reviews from team members
- Ensure CI checks pass
- Keep PRs focused and reasonably sized

## Troubleshooting

### Pre-commit hooks failing

1. **Check Python version:**
   ```bash
   python --version  # Should be 3.11+
   ```

2. **Reinstall hooks:**
   ```bash
   pre-commit clean
   pre-commit install
   pre-commit install --hook-type commit-msg
   ```

3. **Update hooks:**
   ```bash
   pre-commit autoupdate
   ```

### Slack notifications not working

1. **Verify webhook URL is correct** in GitHub secrets
2. **Check workflow runs** in GitHub Actions tab
3. **Ensure webhook is active** in Slack app settings
4. **Verify channel permissions** for the Slack app

### MyPy type errors

If you encounter type checking errors:

1. Add type hints to your code
2. Use `# type: ignore` comments sparingly for third-party issues
3. Update type stubs: `pip install types-redis types-boto3`

## Maintenance

### Regular Updates

- **Monthly**: Update pre-commit hooks with `pre-commit autoupdate`
- **Quarterly**: Review and update GitHub Actions versions
- **As needed**: Update Python dependencies and type stubs

### Monitoring

- Review GitHub Actions workflow runs regularly
- Monitor Slack notifications for false positives
- Gather team feedback on hook strictness

## Additional Resources

- [Pre-commit Documentation](https://pre-commit.com/)
- [Conventional Commits](https://www.conventionalcommits.org/)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [MyPy Documentation](https://mypy.readthedocs.io/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Slack API Documentation](https://api.slack.com/)
