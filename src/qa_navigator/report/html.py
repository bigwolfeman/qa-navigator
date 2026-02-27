"""HTML report generator for QA Navigator test results."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..checklist.models import Checklist, ChecklistItem, ItemStatus


_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }
.header { background: linear-gradient(135deg, #1a73e8, #0d47a1); color: white; padding: 2rem; }
.header h1 { font-size: 2rem; font-weight: 700; }
.header .subtitle { opacity: 0.85; margin-top: 0.4rem; font-size: 0.95rem; }
.summary { display: flex; gap: 1rem; padding: 1.5rem; flex-wrap: wrap; }
.stat-card { background: white; border-radius: 8px; padding: 1.2rem 1.5rem; flex: 1; min-width: 120px;
             box-shadow: 0 1px 4px rgba(0,0,0,0.1); text-align: center; }
.stat-card .number { font-size: 2.5rem; font-weight: 700; line-height: 1; }
.stat-card .label { font-size: 0.8rem; color: #666; margin-top: 0.3rem; text-transform: uppercase; letter-spacing: 0.05em; }
.stat-card.pass .number { color: #1e8e3e; }
.stat-card.fail .number { color: #d93025; }
.stat-card.error .number { color: #f29900; }
.stat-card.rate .number { color: #1a73e8; }
.progress-bar { height: 8px; background: #e0e0e0; border-radius: 4px; margin: 0 1.5rem 1rem; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #1e8e3e, #34a853); border-radius: 4px; transition: width 0.5s ease; }
.items { padding: 0 1.5rem 2rem; }
.items h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #444; }
.item { background: white; border-radius: 8px; margin-bottom: 1rem; box-shadow: 0 1px 4px rgba(0,0,0,0.08); overflow: hidden; }
.item-header { display: flex; align-items: center; gap: 1rem; padding: 1rem 1.2rem; cursor: pointer; }
.item-header:hover { background: #fafafa; }
.badge { padding: 0.2rem 0.7rem; border-radius: 12px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
.badge.pass { background: #e6f4ea; color: #1e8e3e; }
.badge.fail { background: #fce8e6; color: #d93025; }
.badge.error { background: #fef3e2; color: #f29900; }
.badge.pending { background: #e8eaf6; color: #3949ab; }
.item-id { font-family: monospace; font-size: 0.9rem; color: #666; min-width: 80px; }
.item-desc { flex: 1; font-size: 0.95rem; }
.item-cat { font-size: 0.75rem; color: #888; background: #f5f5f5; padding: 0.15rem 0.5rem; border-radius: 4px; }
.item-body { padding: 1rem 1.2rem 1.2rem; border-top: 1px solid #f0f0f0; display: none; }
.item-body.open { display: block; }
.screenshots { display: flex; gap: 1rem; margin: 1rem 0; }
.screenshot-box { flex: 1; }
.screenshot-box h4 { font-size: 0.8rem; color: #666; margin-bottom: 0.5rem; text-transform: uppercase; }
.screenshot-box img { width: 100%; border: 1px solid #ddd; border-radius: 4px; }
.detail-grid { display: grid; grid-template-columns: 120px 1fr; gap: 0.4rem 1rem; font-size: 0.9rem; }
.detail-grid .key { color: #666; font-weight: 500; }
.observation { background: #f9f9f9; border-left: 3px solid #ddd; padding: 0.5rem 0.8rem; border-radius: 0 4px 4px 0;
               font-size: 0.9rem; margin-top: 0.5rem; white-space: pre-wrap; }
.observation.pass { border-color: #1e8e3e; }
.observation.fail { border-color: #d93025; }
.video-section { padding: 0 1.5rem 2rem; }
.video-section h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #444; }
.video-section video { width: 100%; max-width: 900px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.footer { text-align: center; padding: 2rem; color: #888; font-size: 0.85rem; }
"""

_TOGGLE_JS = """
function toggle(id) {
  var el = document.getElementById(id);
  el.classList.toggle('open');
}
"""


def _status_badge(status: ItemStatus) -> str:
    cls = {"passed": "pass", "failed": "fail", "error": "error"}.get(status.value, "pending")
    label = status.value.upper()
    return f'<span class="badge {cls}">{label}</span>'


def _item_html(item: ChecklistItem, idx: int) -> str:
    body_id = f"item-body-{idx}"
    badge = _status_badge(item.status)
    cat = item.category.value.replace("_", " ")

    # Screenshots
    screenshots = ""
    if item.evidence:
        ev = item.evidence
        before_img = ""
        after_img = ""
        if ev.before_screenshot_b64:
            before_img = f'<img src="data:image/png;base64,{ev.before_screenshot_b64}" alt="Before" loading="lazy">'
        if ev.after_screenshot_b64:
            after_img = f'<img src="data:image/png;base64,{ev.after_screenshot_b64}" alt="After" loading="lazy">'
        if before_img or after_img:
            screenshots = f"""
<div class="screenshots">
  <div class="screenshot-box"><h4>Before</h4>{before_img or "<em>No screenshot</em>"}</div>
  <div class="screenshot-box"><h4>After</h4>{after_img or "<em>No screenshot</em>"}</div>
</div>"""

    # Observation
    obs_text = ""
    obs_cls = ""
    if item.evidence and item.evidence.observed_result:
        obs_cls = "pass" if item.status == ItemStatus.PASSED else "fail"
        obs_text = f'<div class="observation {obs_cls}">{_escape(item.evidence.observed_result)}</div>'
    elif item.error_message:
        obs_text = f'<div class="observation fail">{_escape(item.error_message)}</div>'

    # Duration
    dur = ""
    if item.evidence and item.evidence.duration_ms:
        dur = f"{item.evidence.duration_ms / 1000:.1f}s"

    details = f"""
<div class="detail-grid">
  <span class="key">Action</span><span>{_escape(item.action)}</span>
  <span class="key">Expected</span><span>{_escape(item.expected_outcome)}</span>
  <span class="key">Priority</span><span>{item.priority.value}</span>
  {"<span class='key'>Duration</span><span>" + dur + "</span>" if dur else ""}
</div>
{screenshots}
{obs_text}
"""

    return f"""
<div class="item">
  <div class="item-header" onclick="toggle('{body_id}')">
    <span class="item-id">{_escape(item.id)}</span>
    {badge}
    <span class="item-desc">{_escape(item.description)}</span>
    <span class="item-cat">{cat}</span>
  </div>
  <div class="item-body" id="{body_id}">
    {details}
  </div>
</div>"""


def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_html_report(
    checklist: Checklist,
    recording_path: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> str:
    """Generate a self-contained HTML report from a completed test run.

    Args:
        checklist: The completed checklist with results and evidence.
        recording_path: Optional path to the screen recording file (.webm).
        output_path: If provided, write the report to this file.

    Returns:
        The HTML string.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    target = checklist.target_url or checklist.target_app or "Unknown"
    total = checklist.total
    passed = checklist.passed
    failed = checklist.failed
    errored = checklist.errored
    pass_rate = checklist.pass_rate
    pass_pct = f"{pass_rate:.0%}"
    bar_pct = f"{pass_rate * 100:.1f}%"

    # Summary cards
    summary = f"""
<div class="summary">
  <div class="stat-card">
    <div class="number">{total}</div><div class="label">Total Items</div>
  </div>
  <div class="stat-card pass">
    <div class="number">{passed}</div><div class="label">Passed</div>
  </div>
  <div class="stat-card fail">
    <div class="number">{failed}</div><div class="label">Failed</div>
  </div>
  <div class="stat-card error">
    <div class="number">{errored}</div><div class="label">Errors</div>
  </div>
  <div class="stat-card rate">
    <div class="number">{pass_pct}</div><div class="label">Pass Rate</div>
  </div>
</div>
<div class="progress-bar">
  <div class="progress-fill" style="width:{bar_pct}"></div>
</div>"""

    # Test items
    items_html = "\n".join(_item_html(item, i) for i, item in enumerate(checklist.items))

    # Video section
    video_section = ""
    if recording_path:
        video_section = f"""
<div class="video-section">
  <h2>Screen Recording</h2>
  <video controls>
    <source src="{_escape(recording_path)}" type="video/webm">
    Your browser doesn't support video playback.
  </video>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>QA Navigator Report — {_escape(checklist.id)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="header">
    <h1>QA Navigator Report</h1>
    <div class="subtitle">
      Run ID: {_escape(checklist.id)} &nbsp;|&nbsp;
      Target: {_escape(target)} &nbsp;|&nbsp;
      Generated: {now}
    </div>
  </div>
  {summary}
  {video_section}
  <div class="items">
    <h2>Test Items ({total})</h2>
    {items_html}
  </div>
  <div class="footer">
    Generated by QA Navigator &nbsp;·&nbsp; Powered by Gemini
  </div>
  <script>{_TOGGLE_JS}</script>
</body>
</html>"""

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    return html
