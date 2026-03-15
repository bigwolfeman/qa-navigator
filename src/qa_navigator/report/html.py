"""HTML report generator for QA Navigator test results."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..accessibility.auditor import WCAGReport, Severity
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
.wcag-section { padding: 0 1.5rem 2rem; }
.wcag-section h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #444; }
.wcag-score { display: flex; align-items: center; gap: 1.5rem; margin-bottom: 1.5rem; }
.wcag-score .score-circle { width: 90px; height: 90px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: 1.8rem; font-weight: 700; color: white; flex-shrink: 0; }
.score-high { background: linear-gradient(135deg, #1e8e3e, #34a853); }
.score-mid { background: linear-gradient(135deg, #f29900, #fbbc04); }
.score-low { background: linear-gradient(135deg, #d93025, #ea4335); }
.wcag-stats { display: flex; gap: 0.8rem; flex-wrap: wrap; }
.wcag-stat { background: white; border-radius: 6px; padding: 0.5rem 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    font-size: 0.85rem; }
.wcag-stat .count { font-weight: 700; margin-right: 0.3rem; }
.wcag-stat.critical .count { color: #d93025; }
.wcag-stat.serious .count { color: #e37400; }
.wcag-stat.moderate .count { color: #f29900; }
.wcag-stat.minor .count { color: #5f6368; }
.wcag-stat.passed .count { color: #1e8e3e; }
.violation { background: white; border-radius: 8px; margin-bottom: 0.6rem; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    padding: 0.8rem 1.2rem; display: flex; gap: 0.8rem; align-items: flex-start; }
.violation .sev { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; padding: 0.15rem 0.5rem;
    border-radius: 3px; white-space: nowrap; flex-shrink: 0; margin-top: 0.1rem; }
.sev.critical { background: #fce8e6; color: #d93025; }
.sev.serious { background: #fef3e2; color: #e37400; }
.sev.moderate { background: #fff8e1; color: #f29900; }
.sev.minor { background: #f5f5f5; color: #5f6368; }
.violation .v-body { flex: 1; }
.violation .v-desc { font-size: 0.9rem; }
.violation .v-meta { font-size: 0.75rem; color: #888; margin-top: 0.2rem; }
.violation .v-selector { font-family: monospace; font-size: 0.75rem; color: #666; background: #f9f9f9;
    padding: 0.2rem 0.4rem; border-radius: 3px; margin-top: 0.3rem; display: inline-block; max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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


def _wcag_section_html(wcag: WCAGReport) -> str:
    """Build the WCAG accessibility audit section."""
    score = wcag.score
    if score >= 80:
        score_cls = "score-high"
    elif score >= 50:
        score_cls = "score-mid"
    else:
        score_cls = "score-low"

    critical = wcag.critical_count
    serious = wcag.serious_count
    moderate = sum(1 for v in wcag.violations if v.severity == Severity.MODERATE)
    minor = sum(1 for v in wcag.violations if v.severity == Severity.MINOR)
    passed = len(wcag.passes)

    stats = f"""<div class="wcag-stats">
  <div class="wcag-stat critical"><span class="count">{critical}</span>Critical</div>
  <div class="wcag-stat serious"><span class="count">{serious}</span>Serious</div>
  <div class="wcag-stat moderate"><span class="count">{moderate}</span>Moderate</div>
  <div class="wcag-stat minor"><span class="count">{minor}</span>Minor</div>
  <div class="wcag-stat passed"><span class="count">{passed}</span>Passed</div>
</div>"""

    # Sort violations: critical first, then serious, moderate, minor
    severity_order = {Severity.CRITICAL: 0, Severity.SERIOUS: 1, Severity.MODERATE: 2, Severity.MINOR: 3}
    sorted_violations = sorted(wcag.violations, key=lambda v: severity_order[v.severity])

    violations_html = ""
    for v in sorted_violations:
        sev_cls = v.severity.value
        selector = f'<div class="v-selector">{_escape(v.selector[:100])}</div>' if v.selector else ""
        violations_html += f"""
<div class="violation">
  <span class="sev {sev_cls}">{v.severity.value}</span>
  <div class="v-body">
    <div class="v-desc">{_escape(v.description)}</div>
    <div class="v-meta">WCAG {v.wcag_criteria} Level {v.level.value} &middot; {v.rule_id}</div>
    {selector}
  </div>
</div>"""

    page_info = ""
    if wcag.page_stats:
        ps = wcag.page_stats
        page_info = f"""<div style="font-size:0.85rem;color:#666;margin-bottom:1rem;">
  {ps.get('total_elements', 0)} elements &middot;
  {ps.get('interactive_elements', 0)} interactive &middot;
  {ps.get('images', 0)} images &middot;
  {ps.get('headings', 0)} headings &middot;
  {ps.get('forms', 0)} forms
</div>"""

    return f"""
<div class="wcag-section">
  <h2>WCAG 2.1 Accessibility Audit</h2>
  <div class="wcag-score">
    <div class="score-circle {score_cls}">{score:.0f}</div>
    {stats}
  </div>
  {page_info}
  {violations_html if violations_html else '<div class="observation pass">No accessibility violations detected.</div>'}
</div>"""


def generate_html_report(
    checklist: Checklist,
    recording_path: Optional[str] = None,
    output_path: Optional[Path] = None,
    wcag_report: Optional[WCAGReport] = None,
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

    # WCAG section
    wcag_html = _wcag_section_html(wcag_report) if wcag_report else ""

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
  {wcag_html}
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
