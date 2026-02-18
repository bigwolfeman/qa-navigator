# QA Navigator

AI-powered Visual QA Testing Agent that **forces exhaustive UI testing**.

When you give an AI agent instructions to test a UI, it tests ~10% and declares victory. QA Navigator generates a massive checklist of every testable element and drives a Gemini computer-use agent through each item individually, collecting evidence for every test.

## Quick Start

```bash
# Install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,server]"
playwright install chromium

# Set your Gemini API key
export GOOGLE_API_KEY="your-key-here"

# Run
python -m qa_navigator --url https://example.com --instructions "Test all interactive elements"
```

## Architecture

```
Instructions → [Checklist Generator] → Master Checklist → [Orchestrator] → [ADK Agent] → [Report]
```

The orchestrator is a **deterministic state machine** (not an LLM). It feeds the Gemini computer-use agent one test item at a time and won't stop until every item has been addressed.

## Built With
- Google ADK (Agent Development Kit) + ComputerUseToolset
- Gemini 3 Pro (computer use) + Gemini 3 Flash (analysis)
- Playwright for browser automation
- Deployed on Google Cloud Run
