"""WCAG 2.1 accessibility auditor using Playwright DOM inspection.

Runs a battery of automated checks against a live page to detect
common accessibility violations. No external dependencies — all checks
are implemented as page.evaluate() JavaScript snippets.

Covers WCAG 2.1 Level A and AA success criteria that can be detected
programmatically (structural/semantic checks, not subjective ones).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from playwright.async_api import Page


class Severity(str, Enum):
    CRITICAL = "critical"  # Blocks access entirely
    SERIOUS = "serious"    # Major barrier
    MODERATE = "moderate"  # Inconvenient but workaround exists
    MINOR = "minor"        # Best practice


class WCAGLevel(str, Enum):
    A = "A"
    AA = "AA"
    AAA = "AAA"


@dataclass
class Violation:
    rule_id: str
    description: str
    wcag_criteria: str
    level: WCAGLevel
    severity: Severity
    selector: str = ""
    context: str = ""  # HTML snippet or element description
    help_url: str = ""


@dataclass
class WCAGReport:
    url: str = ""
    title: str = ""
    violations: list[Violation] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)  # rule_ids that passed
    page_stats: dict = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.CRITICAL)

    @property
    def serious_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.SERIOUS)

    @property
    def total_violations(self) -> int:
        return len(self.violations)

    @property
    def score(self) -> float:
        """0-100 accessibility score based on violations weighted by severity."""
        if not self.page_stats.get("total_elements", 0):
            return 0.0
        weights = {Severity.CRITICAL: 10, Severity.SERIOUS: 5, Severity.MODERATE: 2, Severity.MINOR: 1}
        penalty = sum(weights[v.severity] for v in self.violations)
        total = self.page_stats.get("total_elements", 1)
        raw = max(0, 100 - (penalty / total) * 100)
        return round(raw, 1)


class WCAGAuditor:
    """Runs WCAG 2.1 accessibility checks against a Playwright page."""

    async def audit(self, page: Page) -> WCAGReport:
        """Run all checks and return a report."""
        report = WCAGReport()
        try:
            report.url = page.url
            report.title = await page.title()
        except Exception:
            pass

        # Collect page stats first
        report.page_stats = await self._page_stats(page)

        # Run all checks concurrently
        checks = [
            self._check_images_alt(page, report),
            self._check_form_labels(page, report),
            self._check_buttons_names(page, report),
            self._check_links(page, report),
            self._check_heading_order(page, report),
            self._check_html_lang(page, report),
            self._check_page_title(page, report),
            self._check_color_contrast(page, report),
            self._check_focus_visible(page, report),
            self._check_skip_nav(page, report),
            self._check_duplicate_ids(page, report),
            self._check_aria_valid(page, report),
            self._check_tabindex(page, report),
            self._check_autocomplete(page, report),
            self._check_meta_viewport(page, report),
        ]
        await asyncio.gather(*checks, return_exceptions=True)

        return report

    # ── Page stats ────────────────────────────────────────────────

    async def _page_stats(self, page: Page) -> dict:
        try:
            return await page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                const interactive = document.querySelectorAll(
                    'a, button, input, select, textarea, [role="button"], [role="link"], [tabindex]'
                );
                const images = document.querySelectorAll('img, [role="img"]');
                const forms = document.querySelectorAll('form');
                const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
                return {
                    total_elements: all.length,
                    interactive_elements: interactive.length,
                    images: images.length,
                    forms: forms.length,
                    headings: headings.length,
                };
            }""")
        except Exception:
            return {"total_elements": 1}

    # ── WCAG 1.1.1: Non-text Content ─────────────────────────────

    async def _check_images_alt(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            document.querySelectorAll('img').forEach(img => {
                if (!img.hasAttribute('alt')) {
                    violations.push({
                        selector: img.outerHTML.slice(0, 120),
                        src: img.src || '',
                    });
                } else if (img.alt === '' && !img.getAttribute('role')
                           && !img.closest('[aria-hidden="true"]')) {
                    // Empty alt without role=presentation — might be decorative, might not
                    const isDecorative = img.closest('a, button') !== null;
                    if (!isDecorative && img.width > 50 && img.height > 50) {
                        violations.push({
                            selector: img.outerHTML.slice(0, 120),
                            src: img.src || '',
                            empty: true,
                        });
                    }
                }
            });
            // SVG without accessible name
            document.querySelectorAll('svg:not([aria-hidden="true"])').forEach(svg => {
                const hasTitle = svg.querySelector('title');
                const hasLabel = svg.getAttribute('aria-label') || svg.getAttribute('aria-labelledby');
                if (!hasTitle && !hasLabel && svg.closest('a, button')) {
                    violations.push({
                        selector: '<svg> inside interactive element',
                        src: 'inline SVG',
                    });
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="img-alt",
                    description=f"Image missing alt text: {v.get('src', '')[:60]}",
                    wcag_criteria="1.1.1",
                    level=WCAGLevel.A,
                    severity=Severity.CRITICAL,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("img-alt")

    # ── WCAG 1.3.1: Info and Relationships (Form Labels) ─────────

    async def _check_form_labels(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            document.querySelectorAll('input, select, textarea').forEach(el => {
                if (['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type)) return;
                if (el.closest('[aria-hidden="true"]')) return;

                const hasLabel = el.labels && el.labels.length > 0;
                const hasAriaLabel = el.getAttribute('aria-label');
                const hasAriaLabelledBy = el.getAttribute('aria-labelledby');
                const hasTitle = el.title;
                const hasPlaceholder = el.placeholder;

                if (!hasLabel && !hasAriaLabel && !hasAriaLabelledBy && !hasTitle) {
                    violations.push({
                        selector: el.outerHTML.slice(0, 120),
                        type: el.type || el.tagName.toLowerCase(),
                        name: el.name || el.id || '',
                        hasPlaceholder: !!hasPlaceholder,
                    });
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                desc = f"Form {v['type']} missing label"
                if v.get("hasPlaceholder"):
                    desc += " (has placeholder but placeholder is not a label substitute)"
                report.violations.append(Violation(
                    rule_id="form-label",
                    description=desc,
                    wcag_criteria="1.3.1",
                    level=WCAGLevel.A,
                    severity=Severity.SERIOUS,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("form-label")

    # ── WCAG 4.1.2: Name, Role, Value (Buttons) ─────────────────

    async def _check_buttons_names(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            const buttons = document.querySelectorAll(
                'button, [role="button"], input[type="button"], input[type="submit"]'
            );
            buttons.forEach(btn => {
                if (btn.closest('[aria-hidden="true"]')) return;
                const name = btn.textContent?.trim()
                    || btn.getAttribute('aria-label')
                    || btn.getAttribute('aria-labelledby')
                    || btn.title
                    || btn.value;
                if (!name) {
                    violations.push({
                        selector: btn.outerHTML.slice(0, 120),
                    });
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="button-name",
                    description="Button has no accessible name",
                    wcag_criteria="4.1.2",
                    level=WCAGLevel.A,
                    severity=Severity.CRITICAL,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("button-name")

    # ── WCAG 2.4.4: Link Purpose ─────────────────────────────────

    async def _check_links(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            const GENERIC = new Set(['click here', 'read more', 'more', 'link', 'here', 'learn more']);
            document.querySelectorAll('a[href]').forEach(a => {
                if (a.closest('[aria-hidden="true"]')) return;
                const text = (a.textContent || '').trim().toLowerCase();
                const ariaLabel = a.getAttribute('aria-label');
                const name = ariaLabel || text;
                if (!name) {
                    violations.push({
                        selector: a.outerHTML.slice(0, 120),
                        issue: 'empty',
                    });
                } else if (GENERIC.has(name.toLowerCase()) && !ariaLabel) {
                    violations.push({
                        selector: a.outerHTML.slice(0, 120),
                        issue: 'generic',
                        text: name,
                    });
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                issue = v.get("issue", "")
                if issue == "empty":
                    desc = "Link has no accessible name"
                    sev = Severity.CRITICAL
                else:
                    desc = f"Link has generic text: \"{v.get('text', '')}\""
                    sev = Severity.MODERATE
                report.violations.append(Violation(
                    rule_id="link-name",
                    description=desc,
                    wcag_criteria="2.4.4",
                    level=WCAGLevel.A,
                    severity=sev,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("link-name")

    # ── WCAG 1.3.1: Heading Order ────────────────────────────────

    async def _check_heading_order(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
            const violations = [];
            let prevLevel = 0;
            let h1Count = 0;
            for (const h of headings) {
                const level = parseInt(h.tagName[1]);
                if (level === 1) h1Count++;
                if (prevLevel > 0 && level > prevLevel + 1) {
                    violations.push({
                        selector: h.outerHTML.slice(0, 80),
                        prevLevel,
                        level,
                    });
                }
                prevLevel = level;
            }
            if (h1Count === 0 && headings.length > 0) {
                violations.push({ selector: 'document', prevLevel: 0, level: -1, issue: 'no-h1' });
            }
            if (h1Count > 1) {
                violations.push({ selector: 'document', prevLevel: 0, level: -1, issue: 'multiple-h1' });
            }
            return violations;
        }""")
        if results:
            for v in results:
                issue = v.get("issue", "")
                if issue == "no-h1":
                    desc = "Page has headings but no <h1>"
                elif issue == "multiple-h1":
                    desc = "Page has multiple <h1> elements"
                else:
                    desc = f"Heading level skipped: h{v['prevLevel']} to h{v['level']}"
                report.violations.append(Violation(
                    rule_id="heading-order",
                    description=desc,
                    wcag_criteria="1.3.1",
                    level=WCAGLevel.A,
                    severity=Severity.MODERATE,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("heading-order")

    # ── WCAG 3.1.1: Language of Page ─────────────────────────────

    async def _check_html_lang(self, page: Page, report: WCAGReport):
        lang = await page.evaluate("() => document.documentElement.lang || ''")
        if not lang:
            report.violations.append(Violation(
                rule_id="html-lang",
                description="<html> element missing lang attribute",
                wcag_criteria="3.1.1",
                level=WCAGLevel.A,
                severity=Severity.SERIOUS,
                selector="<html>",
            ))
        elif len(lang) < 2:
            report.violations.append(Violation(
                rule_id="html-lang-valid",
                description=f"<html> lang attribute value is invalid: \"{lang}\"",
                wcag_criteria="3.1.1",
                level=WCAGLevel.A,
                severity=Severity.SERIOUS,
                selector="<html>",
            ))
        else:
            report.passes.append("html-lang")

    # ── WCAG 2.4.2: Page Titled ──────────────────────────────────

    async def _check_page_title(self, page: Page, report: WCAGReport):
        title = await page.title()
        if not title or not title.strip():
            report.violations.append(Violation(
                rule_id="page-title",
                description="Page has no <title>",
                wcag_criteria="2.4.2",
                level=WCAGLevel.A,
                severity=Severity.SERIOUS,
                selector="<head>",
            ))
        else:
            report.passes.append("page-title")

    # ── WCAG 1.4.3: Contrast (Minimum) ──────────────────────────

    async def _check_color_contrast(self, page: Page, report: WCAGReport):
        """Check text color contrast ratios via computed styles."""
        results = await page.evaluate("""() => {
            function luminance(r, g, b) {
                const [rs, gs, bs] = [r, g, b].map(c => {
                    c = c / 255;
                    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
                });
                return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
            }
            function parseColor(str) {
                const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                return m ? [+m[1], +m[2], +m[3]] : null;
            }
            function contrastRatio(fg, bg) {
                const l1 = luminance(...fg) + 0.05;
                const l2 = luminance(...bg) + 0.05;
                return l1 > l2 ? l1 / l2 : l2 / l1;
            }

            const violations = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            let checked = 0;
            let node;
            while ((node = walker.nextNode()) && checked < 100) {
                const text = node.textContent.trim();
                if (!text || text.length < 2) continue;
                const el = node.parentElement;
                if (!el || el.closest('[aria-hidden="true"]')) continue;

                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;

                const fg = parseColor(style.color);
                const bg = parseColor(style.backgroundColor);
                if (!fg || !bg) continue;
                // Skip transparent backgrounds
                const bgA = style.backgroundColor.match(/rgba.*,\\s*(\\d*\\.?\\d+)\\)/);
                if (bgA && parseFloat(bgA[1]) < 0.1) continue;

                const ratio = contrastRatio(fg, bg);
                const fontSize = parseFloat(style.fontSize);
                const isBold = parseInt(style.fontWeight) >= 700;
                const isLarge = fontSize >= 24 || (fontSize >= 18.66 && isBold);
                const minRatio = isLarge ? 3.0 : 4.5;

                if (ratio < minRatio) {
                    violations.push({
                        text: text.slice(0, 40),
                        ratio: Math.round(ratio * 100) / 100,
                        required: minRatio,
                        selector: el.tagName.toLowerCase() +
                            (el.className ? '.' + el.className.split(' ')[0] : ''),
                    });
                }
                checked++;
            }
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="color-contrast",
                    description=f"Contrast ratio {v['ratio']}:1 below {v['required']}:1 — \"{v['text']}\"",
                    wcag_criteria="1.4.3",
                    level=WCAGLevel.AA,
                    severity=Severity.SERIOUS,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("color-contrast")

    # ── WCAG 2.4.7: Focus Visible ────────────────────────────────

    async def _check_focus_visible(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            const interactive = document.querySelectorAll(
                'a[href], button, input, select, textarea, [tabindex="0"]'
            );
            // Sample up to 20 elements
            const sample = Array.from(interactive).slice(0, 20);
            for (const el of sample) {
                if (el.closest('[aria-hidden="true"]')) continue;
                const style = window.getComputedStyle(el);
                // Check if outline is explicitly removed
                if (style.outlineStyle === 'none' && style.outlineWidth === '0px') {
                    // Check if there's a box-shadow or border fallback
                    const hasFocusStyle = style.boxShadow !== 'none'
                        || style.borderColor !== style.color;
                    // We can only flag elements with outline:none in their computed style
                    // This is a heuristic — can't check :focus pseudo without actually focusing
                }
            }
            // Check for global outline:none in stylesheets
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of sheet.cssRules || []) {
                        if (rule.selectorText && rule.selectorText.includes(':focus')
                            && rule.style && rule.style.outline === 'none'
                            && !rule.selectorText.includes(':focus-visible')) {
                            violations.push({
                                selector: rule.selectorText,
                                issue: 'outline-none',
                            });
                        }
                    }
                } catch(e) { /* cross-origin stylesheet */ }
            }
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="focus-visible",
                    description=f"Focus indicator removed: {v['selector'][:60]}",
                    wcag_criteria="2.4.7",
                    level=WCAGLevel.AA,
                    severity=Severity.SERIOUS,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("focus-visible")

    # ── WCAG 2.4.1: Bypass Blocks (Skip Navigation) ─────────────

    async def _check_skip_nav(self, page: Page, report: WCAGReport):
        has_skip = await page.evaluate("""() => {
            const links = document.querySelectorAll('a[href^="#"]');
            for (const a of links) {
                const text = (a.textContent || '').toLowerCase();
                if (text.includes('skip') || text.includes('jump to')
                    || text.includes('main content')) {
                    return true;
                }
            }
            // Also check for landmark roles
            const main = document.querySelector('main, [role="main"]');
            const nav = document.querySelector('nav, [role="navigation"]');
            return !!(main && nav);
        }""")
        if not has_skip:
            report.violations.append(Violation(
                rule_id="skip-nav",
                description="No skip navigation link or landmark structure (main + nav)",
                wcag_criteria="2.4.1",
                level=WCAGLevel.A,
                severity=Severity.MODERATE,
                selector="<body>",
            ))
        else:
            report.passes.append("skip-nav")

    # ── WCAG 4.1.1: Parsing (Duplicate IDs) ─────────────────────

    async def _check_duplicate_ids(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const ids = {};
            document.querySelectorAll('[id]').forEach(el => {
                const id = el.id;
                if (id) ids[id] = (ids[id] || 0) + 1;
            });
            return Object.entries(ids)
                .filter(([_, count]) => count > 1)
                .map(([id, count]) => ({ id, count }));
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="duplicate-id",
                    description=f"Duplicate id=\"{v['id']}\" ({v['count']} occurrences)",
                    wcag_criteria="4.1.1",
                    level=WCAGLevel.A,
                    severity=Severity.MODERATE,
                    selector=f"#{v['id']}",
                ))
        else:
            report.passes.append("duplicate-id")

    # ── WCAG 4.1.2: ARIA Valid ───────────────────────────────────

    async def _check_aria_valid(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const VALID_ROLES = new Set([
                'alert','alertdialog','application','article','banner','button',
                'cell','checkbox','columnheader','combobox','complementary',
                'contentinfo','definition','dialog','directory','document',
                'feed','figure','form','grid','gridcell','group','heading',
                'img','link','list','listbox','listitem','log','main',
                'marquee','math','menu','menubar','menuitem','menuitemcheckbox',
                'menuitemradio','navigation','none','note','option','presentation',
                'progressbar','radio','radiogroup','region','row','rowgroup',
                'rowheader','scrollbar','search','searchbox','separator',
                'slider','spinbutton','status','switch','tab','table',
                'tablist','tabpanel','term','textbox','timer','toolbar',
                'tooltip','tree','treegrid','treeitem'
            ]);
            const violations = [];
            document.querySelectorAll('[role]').forEach(el => {
                const role = el.getAttribute('role');
                if (role && !VALID_ROLES.has(role.toLowerCase())) {
                    violations.push({
                        selector: el.outerHTML.slice(0, 80),
                        role,
                    });
                }
            });
            // Check aria-labelledby references exist
            document.querySelectorAll('[aria-labelledby]').forEach(el => {
                const ids = el.getAttribute('aria-labelledby').split(/\\s+/);
                for (const id of ids) {
                    if (id && !document.getElementById(id)) {
                        violations.push({
                            selector: el.outerHTML.slice(0, 80),
                            role: 'broken-aria-labelledby: #' + id,
                        });
                    }
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="aria-valid",
                    description=f"Invalid ARIA: {v.get('role', '')}",
                    wcag_criteria="4.1.2",
                    level=WCAGLevel.A,
                    severity=Severity.SERIOUS,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("aria-valid")

    # ── WCAG 2.1.1: Keyboard (Positive tabindex) ────────────────

    async def _check_tabindex(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const violations = [];
            document.querySelectorAll('[tabindex]').forEach(el => {
                const val = parseInt(el.getAttribute('tabindex'));
                if (val > 0) {
                    violations.push({
                        selector: el.outerHTML.slice(0, 80),
                        tabindex: val,
                    });
                }
            });
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="tabindex-positive",
                    description=f"Positive tabindex={v['tabindex']} disrupts natural tab order",
                    wcag_criteria="2.1.1",
                    level=WCAGLevel.A,
                    severity=Severity.MODERATE,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("tabindex-positive")

    # ── WCAG 1.3.5: Identify Input Purpose (autocomplete) ───────

    async def _check_autocomplete(self, page: Page, report: WCAGReport):
        results = await page.evaluate("""() => {
            const FIELDS = {
                'email': 'email', 'username': 'username', 'password': 'current-password',
                'new-password': 'new-password', 'tel': 'tel', 'phone': 'tel',
                'first-name': 'given-name', 'last-name': 'family-name',
                'firstname': 'given-name', 'lastname': 'family-name',
                'address': 'street-address', 'zip': 'postal-code',
                'postal': 'postal-code', 'city': 'address-level2',
                'state': 'address-level1', 'country': 'country-name',
                'cc-number': 'cc-number', 'credit-card': 'cc-number',
            };
            const violations = [];
            document.querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], input[type="password"]')
                .forEach(input => {
                    if (input.closest('[aria-hidden="true"]')) return;
                    if (input.autocomplete && input.autocomplete !== 'off') return;
                    const name = (input.name || input.id || input.placeholder || '').toLowerCase();
                    for (const [key, val] of Object.entries(FIELDS)) {
                        if (name.includes(key)) {
                            violations.push({
                                selector: input.outerHTML.slice(0, 100),
                                field: key,
                                suggested: val,
                            });
                            break;
                        }
                    }
                });
            return violations;
        }""")
        if results:
            for v in results:
                report.violations.append(Violation(
                    rule_id="autocomplete",
                    description=f"Input \"{v['field']}\" missing autocomplete=\"{v['suggested']}\"",
                    wcag_criteria="1.3.5",
                    level=WCAGLevel.AA,
                    severity=Severity.MINOR,
                    selector=v.get("selector", ""),
                ))
        else:
            report.passes.append("autocomplete")

    # ── WCAG 1.4.4: Resize Text (viewport meta) ─────────────────

    async def _check_meta_viewport(self, page: Page, report: WCAGReport):
        result = await page.evaluate("""() => {
            const meta = document.querySelector('meta[name="viewport"]');
            if (!meta) return null;
            const content = meta.getAttribute('content') || '';
            const noScale = /maximum-scale\\s*=\\s*1(\\.0)?/.test(content)
                || /user-scalable\\s*=\\s*(no|0)/.test(content);
            return { content, noScale };
        }""")
        if result and result.get("noScale"):
            report.violations.append(Violation(
                rule_id="meta-viewport",
                description="Viewport meta disables user scaling",
                wcag_criteria="1.4.4",
                level=WCAGLevel.AA,
                severity=Severity.SERIOUS,
                selector=f"<meta name=\"viewport\" content=\"{result.get('content', '')}\">"[:100],
            ))
        else:
            report.passes.append("meta-viewport")
