# Architecture

```
                          ┌─────────────────────┐
                          │   User / CI Trigger  │
                          │  URL + Instructions  │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Checklist Generator│
                          │   (Gemini Flash)     │
                          │                      │
                          │ Generates exhaustive │
                          │ test items from URL  │
                          │ or live UI snapshot  │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │     Orchestrator     │
                          │  (Deterministic FSM) │◄─── NOT an LLM.
                          │                      │     Python state machine.
                          │ Feeds items one by   │
                          │ one. Never skips.    │
                          └──────┬───────┬──────┘
                                 │       │
                    ┌────────────▼─┐   ┌─▼────────────┐
                    │  ADK Agent   │   │  Validator    │
                    │  (Gemini 3)  │   │  (Gemini      │
                    │              │   │   Flash)      │
                    │ Computer-use │   │ Before/After  │
                    │ tool calls   │   │ screenshot    │
                    └──────┬───────┘   │ comparison    │
                           │           └───────────────┘
              ┌────────────┴────────────┐
              │                         │
    ┌─────────▼─────────┐   ┌──────────▼──────────┐
    │ QAPlaywrightComp  │   │  WindowsComputer    │
    │ (Browser)         │   │  (Native Desktop)   │
    │                   │   │                     │
    │ Playwright API    │   │ Win32 + UIA APIs    │
    │ Cross-platform    │   │ Windows GCE VM      │
    └───────────────────┘   └─────────────────────┘
              │                         │
              └────────────┬────────────┘
                           │
                ┌──────────▼──────────┐
                │    HTML Report +    │
                │    CI Exit Code     │
                │    Screen Recording │
                └─────────────────────┘

 Google Cloud Services:
 ├── Gemini API (checklist generation, computer-use agent, vision validation)
 ├── Google ADK (agent framework + ComputerUseToolset)
 ├── Cloud Run (FastAPI server deployment)
 └── GCE Windows VM (native desktop testing)
```

## Data Flow

1. User provides a URL and test instructions (or an app executable for native desktop)
2. Checklist Generator calls Gemini Flash to produce an exhaustive list of testable items
3. For native desktop: captures live screenshot + UIA accessibility tree first, generates grounded items
4. Orchestrator iterates items deterministically — one fresh ADK agent per item
5. Agent uses computer-use tools (click, type, screenshot) to perform each test action
6. Validator compares before/after screenshots to judge PASS/FAIL
7. Results aggregated into an HTML report with evidence screenshots

## Key Design Decision

The orchestrator is intentionally **not** an LLM. An LLM orchestrator will test 10% of items and declare success. A deterministic state machine tests 100% because it cannot decide to stop early.
