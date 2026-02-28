#!/usr/bin/env bash
# demo.sh — Record a clean local QA Navigator demo
#
# Records BOTH the terminal (Rich orchestrator output) AND
# the Chromium browser (agent interacting with TodoMVC).
#
# Usage:
#   export GOOGLE_API_KEY=<your_key>
#   ./demo.sh
#
# Output:
#   recordings/demo_TIMESTAMP.mp4    — full screen recording
#   recordings/demo_TIMESTAMP.html   — HTML report
#   recordings/demo_TIMESTAMP_browser.webm — browser-only recording

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Config ──────────────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RECORDINGS_DIR="$SCRIPT_DIR/recordings"
REPORTS_DIR="$SCRIPT_DIR/reports"
DEMO_OUT="$RECORDINGS_DIR/demo_${TIMESTAMP}.mp4"
BROWSER_REC_DIR="$RECORDINGS_DIR/browser_${TIMESTAMP}"

mkdir -p "$RECORDINGS_DIR" "$REPORTS_DIR"

# ── Check requirements ───────────────────────────────────────────────────────
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "ERROR: GOOGLE_API_KEY not set."
    echo "  export GOOGLE_API_KEY=<your_key> && ./demo.sh"
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    echo "ERROR: ffmpeg not found."
    exit 1
fi

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
QA_NAV="$SCRIPT_DIR/.venv/bin/qa-navigator"
if [ ! -f "$QA_NAV" ]; then
    echo "ERROR: qa-navigator not found in .venv. Run: uv pip install -e ."
    exit 1
fi

# ── Display detection ────────────────────────────────────────────────────────
DISPLAY="${DISPLAY:-:0}"
SCREEN_RES=$(xdpyinfo -display "$DISPLAY" 2>/dev/null | grep dimensions | awk '{print $2}' | head -1)
# Capture a 1920x1080 region anchored at top-left
CAP_W=1920
CAP_H=1080
CAP_X=0
CAP_Y=0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  QA Navigator — Demo Recording"
echo "  Screen: ${SCREEN_RES}  Capture: ${CAP_W}x${CAP_H}+${CAP_X}+${CAP_Y}"
echo "  Output: $DEMO_OUT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Start ffmpeg screen recording ────────────────────────────────────────────
echo "[1/4] Starting screen recording..."
ffmpeg -y \
    -f x11grab \
    -video_size "${CAP_W}x${CAP_H}" \
    -framerate 25 \
    -i "${DISPLAY}+${CAP_X},${CAP_Y}" \
    -c:v libx264 \
    -preset ultrafast \
    -crf 23 \
    -pix_fmt yuv420p \
    "$DEMO_OUT" \
    &>/tmp/ffmpeg_demo.log &
FFMPEG_PID=$!
echo "  ffmpeg PID: $FFMPEG_PID"
sleep 2  # Let ffmpeg settle

# ── QA Navigator env ─────────────────────────────────────────────────────────
export QA_NAV_COMPUTER_USE_MODEL="gemini-3-flash-preview"
export QA_NAV_ANALYSIS_MODEL="gemini-3-flash-preview"
export QA_NAV_INTER_ITEM_DELAY_SECONDS="2"     # Fast demo (vs 10s in production)
export QA_NAV_MAX_CHECKLIST_ITEMS="5"
export QA_NAV_MIN_CHECKLIST_ITEMS="3"
export QA_NAV_MAX_RETRIES_PER_ITEM="1"
export PYTHONUNBUFFERED="1"

echo "[2/4] Running QA Navigator (this opens a browser window)..."
echo "  → Position the browser to fill the right 2/3 of your screen for best recording."
echo ""

"$QA_NAV" \
    --url "https://todomvc.com/examples/react/dist/" \
    --instructions "Test TodoMVC React: (1) Verify the app loads with an empty list and input placeholder 'What needs to be done?'. (2) Click the input and type 'Buy Milk', press Enter - verify the item appears in the list with '1 item left'. (3) Type 'Walk the Dog', press Enter - verify '2 items left'. (4) Click the circle/checkbox next to 'Buy Milk' to mark it complete - verify it gets a strikethrough. (5) Click 'Active' filter and verify only 'Walk the Dog' (uncompleted) is visible." \
    --chromium-executable /usr/bin/chromium \
    --recording-dir "$BROWSER_REC_DIR" \
    --report-dir "$REPORTS_DIR"

QA_EXIT=$?

# ── Stop recording ────────────────────────────────────────────────────────────
echo ""
echo "[3/4] Stopping screen recording..."
sleep 2
kill $FFMPEG_PID 2>/dev/null
wait $FFMPEG_PID 2>/dev/null || true

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "[4/4] Done!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -f "$DEMO_OUT" ]; then
    SIZE=$(du -sh "$DEMO_OUT" | cut -f1)
    DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$DEMO_OUT" 2>/dev/null || echo "?")
    echo "  Screen recording: $DEMO_OUT  (${SIZE})"
    printf "  Duration: %.1fs\n" "$DURATION"
else
    echo "  WARNING: Screen recording not found at $DEMO_OUT"
    echo "  ffmpeg log: $(tail -5 /tmp/ffmpeg_demo.log)"
fi

REPORT=$(ls -t "$REPORTS_DIR"/*.html 2>/dev/null | head -1)
[ -n "$REPORT" ] && echo "  HTML report: $REPORT"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  QA Navigator exit code: $QA_EXIT  (0=pass, 1=failures, 2=errors)"
exit $QA_EXIT
