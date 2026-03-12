#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# QA Navigator Setup Wizard
# Walks you through adding AI-powered UI testing to your CI/CD.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║         QA Navigator Setup Wizard             ║${NC}"
    echo -e "${CYAN}${BOLD}║   AI-powered UI testing for your CI/CD        ║${NC}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════╝${NC}"
    echo ""
}

step() { echo -e "\n${GREEN}${BOLD}[$1]${NC} $2"; }
info() { echo -e "  ${DIM}$1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠  $1${NC}"; }
err()  { echo -e "  ${RED}✗  $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓  $1${NC}"; }

ask() {
    local prompt="$1" default="${2:-}"
    if [[ -n "$default" ]]; then
        echo -en "  ${BOLD}$prompt${NC} ${DIM}[$default]${NC}: "
    else
        echo -en "  ${BOLD}$prompt${NC}: "
    fi
    read -r answer
    echo "${answer:-$default}"
}

ask_yn() {
    local prompt="$1" default="${2:-y}"
    local hint="Y/n"
    [[ "$default" == "n" ]] && hint="y/N"
    echo -en "  ${BOLD}$prompt${NC} ${DIM}($hint)${NC}: "
    read -r answer
    answer="${answer:-$default}"
    [[ "${answer,,}" == "y" || "${answer,,}" == "yes" ]]
}

choose() {
    local prompt="$1"
    shift
    local options=("$@")
    echo -e "  ${BOLD}$prompt${NC}"
    for i in "${!options[@]}"; do
        echo -e "    ${CYAN}$((i+1))${NC}) ${options[$i]}"
    done
    echo -en "  ${DIM}Choice${NC}: "
    read -r choice
    echo "$((choice - 1))"
}

# ── Preflight ───────────────────────────────────────────────────
banner

step "1/6" "Checking prerequisites..."

# Git repo?
if git rev-parse --is-inside-work-tree &>/dev/null; then
    REPO_ROOT=$(git rev-parse --show-toplevel)
    REPO_NAME=$(basename "$REPO_ROOT")
    ok "Git repo detected: $REPO_NAME"

    # Remote?
    if REMOTE_URL=$(git remote get-url origin 2>/dev/null); then
        ok "Remote: $REMOTE_URL"
        # Extract owner/repo for GitHub
        if [[ "$REMOTE_URL" =~ github\.com[:/]([^/]+)/([^/.]+) ]]; then
            GH_OWNER="${BASH_REMATCH[1]}"
            GH_REPO="${BASH_REMATCH[2]}"
        fi
    else
        warn "No git remote set — you'll need to push before CI works"
    fi
else
    err "Not inside a git repository."
    info "Run this from the root of the repo you want to add QA testing to."
    exit 1
fi

# gh CLI?
HAS_GH=false
if command -v gh &>/dev/null; then
    if gh auth status &>/dev/null 2>&1; then
        HAS_GH=true
        ok "GitHub CLI authenticated"
    else
        warn "GitHub CLI found but not authenticated (gh auth login)"
    fi
else
    info "GitHub CLI not found — you'll set secrets manually"
fi

# Python?
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1)
    ok "$PY_VER"
else
    warn "Python 3 not found locally (CI will still work)"
fi

# ── What to test ────────────────────────────────────────────────
step "2/6" "What do you want to test?"

TARGET_URL=$(ask "Target URL to test" "https://your-app.example.com")

DEFAULT_INSTRUCTIONS="Test all interactive features of this application. Click buttons, fill forms, navigate pages, and verify the UI responds correctly."
echo -e "  ${BOLD}Testing instructions for the AI agent:${NC}"
info "(Press Enter for default, or type custom instructions)"
echo -en "  ${DIM}> ${NC}"
read -r CUSTOM_INSTRUCTIONS
INSTRUCTIONS="${CUSTOM_INSTRUCTIONS:-$DEFAULT_INSTRUCTIONS}"
ok "Target: $TARGET_URL"

# ── Trigger config ──────────────────────────────────────────────
step "3/6" "When should tests run?"

TRIGGERS=()
TRIGGER_YAML=""

if ask_yn "On pull requests?" "y"; then
    TRIGGERS+=("pull_request")
fi
if ask_yn "On push to main/master?" "y"; then
    TRIGGERS+=("push")
fi
if ask_yn "Manual trigger (workflow_dispatch)?" "y"; then
    TRIGGERS+=("workflow_dispatch")
fi

BRANCH=$(ask "Primary branch name" "main")

# ── CI platform ─────────────────────────────────────────────────
step "4/6" "CI/CD platform"

PLATFORM_IDX=$(choose "Select your CI platform:" \
    "GitHub Actions (recommended)" \
    "GitLab CI" \
    "Cloud Run API (standalone server)")

case $PLATFORM_IDX in
    0) PLATFORM="github" ;;
    1) PLATFORM="gitlab" ;;
    2) PLATFORM="cloudrun" ;;
    *) PLATFORM="github" ;;
esac

# ── API Key ─────────────────────────────────────────────────────
step "5/6" "Gemini API key"

info "QA Navigator uses Google Gemini for AI-powered testing."
info "Get a key at: https://aistudio.google.com/apikey"
echo ""

HAS_KEY=false
if [[ -n "${GOOGLE_API_KEY:-}" ]]; then
    ok "GOOGLE_API_KEY found in environment"
    HAS_KEY=true
fi

SET_SECRET=false
if [[ "$PLATFORM" == "github" ]] && $HAS_GH && [[ -n "${GH_OWNER:-}" ]]; then
    if ask_yn "Set GOOGLE_API_KEY as a GitHub repo secret now?" "y"; then
        SET_SECRET=true
        if $HAS_KEY; then
            if ask_yn "Use the key from your environment?" "y"; then
                API_KEY="$GOOGLE_API_KEY"
            else
                API_KEY=$(ask "Paste your Gemini API key" "")
            fi
        else
            API_KEY=$(ask "Paste your Gemini API key" "")
        fi
    fi
fi

# ── Generate files ──────────────────────────────────────────────
step "6/6" "Generating CI configuration..."

generate_github_actions() {
    local workflow_dir="$REPO_ROOT/.github/workflows"
    local workflow_file="$workflow_dir/qa-navigator.yml"

    mkdir -p "$workflow_dir"

    # Build trigger block
    local trigger_block="on:"
    for t in "${TRIGGERS[@]}"; do
        case $t in
            pull_request)
                trigger_block+=$'\n'"  pull_request:"
                trigger_block+=$'\n'"    branches: [$BRANCH]"
                ;;
            push)
                trigger_block+=$'\n'"  push:"
                trigger_block+=$'\n'"    branches: [$BRANCH]"
                ;;
            workflow_dispatch)
                trigger_block+=$'\n'"  workflow_dispatch:"
                trigger_block+=$'\n'"    inputs:"
                trigger_block+=$'\n'"      target_url:"
                trigger_block+=$'\n'"        description: \"URL to test\""
                trigger_block+=$'\n'"        required: true"
                trigger_block+=$'\n'"        default: \"$TARGET_URL\""
                trigger_block+=$'\n'"      instructions:"
                trigger_block+=$'\n'"        description: \"Testing instructions\""
                trigger_block+=$'\n'"        required: false"
                trigger_block+=$'\n'"        default: \"$INSTRUCTIONS\""
                ;;
        esac
    done

    cat > "$workflow_file" << YAML
name: QA Navigator — AI UI Testing

$trigger_block

env:
  GOOGLE_API_KEY: \${{ secrets.GOOGLE_API_KEY }}

jobs:
  qa-test:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install QA Navigator
        run: |
          pip install qa-navigator
          playwright install chromium

      - name: Run AI UI Tests
        run: |
          python -m qa_navigator \\
            --url "\${{ github.event.inputs.target_url || '$TARGET_URL' }}" \\
            --instructions "\${{ github.event.inputs.instructions || '$INSTRUCTIONS' }}" \\
            --ci \\
            --headless \\
            --report-dir reports/
        env:
          QA_NAV_HEADLESS: "true"
          QA_NAV_SCREEN_WIDTH: "1280"
          QA_NAV_SCREEN_HEIGHT: "900"

      - name: Upload Test Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: qa-report
          path: reports/
          retention-days: 30
YAML

    ok "Created $workflow_file"
    echo "$workflow_file"
}

generate_gitlab_ci() {
    local ci_file="$REPO_ROOT/.gitlab-ci.yml"
    local needs_merge=false

    if [[ -f "$ci_file" ]]; then
        warn ".gitlab-ci.yml already exists — appending QA stage"
        needs_merge=true
    fi

    local qa_block
    read -r -d '' qa_block << 'YAML' || true

# ── QA Navigator: AI-powered UI testing ──────────────────────
qa-test:
  stage: test
  image: python:3.12-slim
  variables:
    GOOGLE_API_KEY: $GOOGLE_API_KEY
    QA_NAV_HEADLESS: "true"
  before_script:
    - pip install qa-navigator
    - playwright install chromium
    - playwright install-deps chromium
  script:
    - |
      python -m qa_navigator \
        --url "TARGET_URL_PLACEHOLDER" \
        --instructions "INSTRUCTIONS_PLACEHOLDER" \
        --ci \
        --headless \
        --report-dir reports/
  artifacts:
    when: always
    paths:
      - reports/
    expire_in: 30 days
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "BRANCH_PLACEHOLDER"
    - when: manual
YAML

    # Substitute placeholders
    qa_block="${qa_block//TARGET_URL_PLACEHOLDER/$TARGET_URL}"
    qa_block="${qa_block//INSTRUCTIONS_PLACEHOLDER/$INSTRUCTIONS}"
    qa_block="${qa_block//BRANCH_PLACEHOLDER/$BRANCH}"

    if $needs_merge; then
        echo "$qa_block" >> "$ci_file"
    else
        echo "stages:" > "$ci_file"
        echo "  - test" >> "$ci_file"
        echo "$qa_block" >> "$ci_file"
    fi

    ok "Created/updated $ci_file"
    echo "$ci_file"
}

generate_cloudrun() {
    local deploy_file="$REPO_ROOT/deploy-qa-navigator.sh"

    cat > "$deploy_file" << 'BASH'
#!/usr/bin/env bash
set -euo pipefail

# ── Config ──
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="qa-navigator"
REPO="qa-navigator-images"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}:latest"

echo "==> Creating Artifact Registry repo (if needed)"
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID" 2>/dev/null || true

echo "==> Building container"
# Build from the qa-navigator package directly
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cat > "$TMPDIR/Dockerfile" << 'DOCKERFILE'
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir qa-navigator
RUN playwright install chromium
EXPOSE 8080
CMD ["python", "-m", "qa_navigator.server", "--host", "0.0.0.0", "--port", "8080"]
DOCKERFILE

gcloud builds submit "$TMPDIR" \
  --tag "$IMAGE" \
  --project "$PROJECT_ID"

echo "==> Deploying to Cloud Run"
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --set-env-vars "GOOGLE_API_KEY=${GOOGLE_API_KEY:?Set GOOGLE_API_KEY}" \
  --allow-unauthenticated

URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" \
  --format='value(status.url)')

echo ""
echo "==> Deployed: $URL"
echo "==> Health:   curl $URL/health"
echo "==> Run test: curl -X POST $URL/run -H 'Content-Type: application/json' \\"
echo "              -d '{\"url\": \"https://your-app.com\", \"instructions\": \"Test everything\"}'"
BASH

    chmod +x "$deploy_file"
    ok "Created $deploy_file"
    echo "$deploy_file"
}

# ── Execute ─────────────────────────────────────────────────────
GENERATED_FILE=""
case $PLATFORM in
    github)  GENERATED_FILE=$(generate_github_actions) ;;
    gitlab)  GENERATED_FILE=$(generate_gitlab_ci) ;;
    cloudrun) GENERATED_FILE=$(generate_cloudrun) ;;
esac

# ── Set GitHub secret ───────────────────────────────────────────
if $SET_SECRET && [[ -n "${API_KEY:-}" ]]; then
    echo ""
    info "Setting GitHub secret..."
    if echo "$API_KEY" | gh secret set GOOGLE_API_KEY --repo="$GH_OWNER/$GH_REPO"; then
        ok "GOOGLE_API_KEY secret set on $GH_OWNER/$GH_REPO"
    else
        err "Failed to set secret — do it manually:"
        info "gh secret set GOOGLE_API_KEY --repo=$GH_OWNER/$GH_REPO"
    fi
fi

# ── Summary ─────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════${NC}"
echo ""

case $PLATFORM in
    github)
        echo -e "  ${BOLD}What was created:${NC}"
        echo -e "    ${GREEN}✓${NC} .github/workflows/qa-navigator.yml"
        echo ""
        echo -e "  ${BOLD}Next steps:${NC}"
        if ! $SET_SECRET; then
            echo -e "    1. Add your Gemini API key as a GitHub secret:"
            echo -e "       ${DIM}gh secret set GOOGLE_API_KEY${NC}"
            echo -e "       ${DIM}or: Settings → Secrets → Actions → New repository secret${NC}"
            echo ""
        fi
        echo -e "    ${CYAN}•${NC} Commit and push the workflow file"
        echo -e "    ${CYAN}•${NC} Tests will run automatically on ${TRIGGERS[*]}"
        echo -e "    ${CYAN}•${NC} Reports appear as build artifacts"
        if [[ " ${TRIGGERS[*]} " =~ "workflow_dispatch" ]]; then
            echo -e "    ${CYAN}•${NC} Manual trigger: Actions → QA Navigator → Run workflow"
        fi
        ;;
    gitlab)
        echo -e "  ${BOLD}What was created:${NC}"
        echo -e "    ${GREEN}✓${NC} .gitlab-ci.yml (qa-test job)"
        echo ""
        echo -e "  ${BOLD}Next steps:${NC}"
        echo -e "    1. Add your Gemini API key as a CI variable:"
        echo -e "       ${DIM}Settings → CI/CD → Variables → GOOGLE_API_KEY${NC}"
        echo -e "    2. Commit and push"
        echo -e "    3. Reports appear as job artifacts"
        ;;
    cloudrun)
        echo -e "  ${BOLD}What was created:${NC}"
        echo -e "    ${GREEN}✓${NC} deploy-qa-navigator.sh"
        echo ""
        echo -e "  ${BOLD}Next steps:${NC}"
        echo -e "    1. Set environment variables:"
        echo -e "       ${DIM}export GCP_PROJECT_ID=your-project${NC}"
        echo -e "       ${DIM}export GOOGLE_API_KEY=your-key${NC}"
        echo -e "    2. Run: ${DIM}./deploy-qa-navigator.sh${NC}"
        echo -e "    3. Use the API endpoint to trigger tests from any CI system"
        ;;
esac

echo ""
echo -e "  ${DIM}Target:       $TARGET_URL${NC}"
echo -e "  ${DIM}Instructions: ${INSTRUCTIONS:0:60}...${NC}"
echo -e "  ${DIM}Docs:         https://github.com/bigwolfeman/qa-navigator${NC}"
echo ""
