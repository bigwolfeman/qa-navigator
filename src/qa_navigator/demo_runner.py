"""Demo runner — single long exploration for screen recordings.

Unlike the orchestrator (which resets between items), this gives the agent
ONE comprehensive instruction and lets it explore continuously. Produces
much better footage for demos because the agent stays logged in, browses
products, adds to cart, checks out, etc. without returning to the login page.
"""

import asyncio
import base64
import sys
import time
import traceback

from google import genai
from google.genai import types
from rich.console import Console

from .computers.playwright_computer import QAPlaywrightComputer
from .config import settings

console = Console()

# All available tools for the demo agent
_DEMO_TOOLS = [
    types.FunctionDeclaration(
        name="screenshot",
        description="Take a screenshot of the current screen.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="get_ui_tree",
        description="Get interactive elements on the page (names, types, coordinates).",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="find_and_click",
        description="Click a UI element by its accessible name/text.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "element_name": {"type": "STRING", "description": "Name or text of the element"},
            },
            "required": ["element_name"],
        },
    ),
    types.FunctionDeclaration(
        name="find_and_type",
        description="Type text into an input element found by name/placeholder.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "element_name": {"type": "STRING", "description": "Name/placeholder of the input"},
                "text": {"type": "STRING", "description": "Text to type"},
                "press_enter": {"type": "BOOLEAN", "description": "Press Enter after typing"},
            },
            "required": ["element_name", "text"],
        },
    ),
    types.FunctionDeclaration(
        name="click_at",
        description="Click at pixel coordinates (x, y).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER", "description": "X coordinate"},
                "y": {"type": "INTEGER", "description": "Y coordinate"},
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="double_click_at",
        description="Double-click at pixel coordinates.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER", "description": "X coordinate"},
                "y": {"type": "INTEGER", "description": "Y coordinate"},
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="key_combination",
        description="Press key combination, e.g. ['ctrl','a'] or ['Return'].",
        parameters={
            "type": "OBJECT",
            "properties": {
                "keys": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Keys to press"},
            },
            "required": ["keys"],
        },
    ),
    types.FunctionDeclaration(
        name="type_text",
        description="Type raw text at cursor position.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "Text to type"},
            },
            "required": ["text"],
        },
    ),
    types.FunctionDeclaration(
        name="get_page_info",
        description="Get page title, URL, focused element, and all visible text.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
]

MAX_TURNS = 50


async def dispatch_tool(computer: QAPlaywrightComputer, name: str, args: dict) -> dict:
    """Route tool call to computer method."""
    if name == "screenshot":
        await computer.current_state()
        return {"status": "ok"}

    elif name == "get_ui_tree":
        tree = await computer.get_ui_tree()
        if isinstance(tree, dict) and "elements" in tree:
            # Keep compact — 25 elements max for demo to prevent context bloat
            return {"elements": tree["elements"][:25]}
        elif isinstance(tree, dict) and "aria_snapshot" in tree:
            return {"aria_snapshot": tree["aria_snapshot"][:1000]}
        return tree or {"elements": []}

    elif name == "find_and_click":
        await computer.find_and_click(args.get("element_name", ""))
        return {"status": "clicked", "element": args.get("element_name")}

    elif name == "find_and_type":
        await computer.find_and_type(
            args.get("element_name", ""),
            args.get("text", ""),
            press_enter=args.get("press_enter", False),
        )
        return {"status": "typed", "element": args.get("element_name")}

    elif name == "click_at":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        await computer.click_at(x, y)
        return {"status": "clicked", "x": x, "y": y}

    elif name == "double_click_at":
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        await computer.page.mouse.dblclick(x, y)
        return {"status": "double_clicked", "x": x, "y": y}

    elif name == "key_combination":
        await computer.key_combination(args.get("keys", []))
        return {"status": "pressed", "keys": args.get("keys")}

    elif name == "type_text":
        await computer.type_text(args.get("text", ""))
        return {"status": "typed"}

    elif name == "get_page_info":
        page = computer.page
        info = {}
        try:
            info["title"] = await page.title()
            info["url"] = page.url
            info["focused_element"] = await page.evaluate(
                "() => { const el = document.activeElement; "
                "return el ? {tag: el.tagName, id: el.id || ''} : null; }"
            )
            info["visible_text"] = await page.evaluate(
                "() => { const walk = document.createTreeWalker("
                "document.body, NodeFilter.SHOW_TEXT, null); "
                "const texts = []; let n; "
                "while ((n = walk.nextNode()) && texts.length < 50) { "
                "const t = n.textContent.trim(); "
                "if (t && t.length < 200) texts.push(t); } "
                "return texts; }"
            )
        except Exception as e:
            info["error"] = str(e)
        return info

    return {"error": f"Unknown tool: {name}"}


async def run_demo(url: str, instruction: str, recording_dir: str):
    """Run a single long demo exploration with screen recording."""
    computer = QAPlaywrightComputer(
        screen_size=(1280, 900),
        initial_url=url,
        headless=True,
        settle_time=0.5,
        recording_dir=recording_dir,
    )
    await computer.initialize()

    # Take initial screenshot
    state = await computer.current_state()
    scr_b64 = base64.b64encode(state.screenshot).decode() if state.screenshot else None

    # Build initial message
    initial_parts = []
    if scr_b64:
        initial_parts.append(
            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=scr_b64))
        )
    initial_parts.append(types.Part(text=instruction))

    contents = [types.Content(role="user", parts=initial_parts)]

    client = genai.Client()
    gen_config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=_DEMO_TOOLS)],
        temperature=0.1,
    )

    console.print(f"[bold green]Demo started — {MAX_TURNS} turns max[/]")
    console.print(f"[dim]URL: {url}[/]")
    console.print(f"[dim]Recording: {recording_dir}[/]")

    start = time.monotonic()
    consecutive_timeouts = 0

    for turn in range(MAX_TURNS):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.computer_use_model,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=180.0,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            console.print(f"  [red]API error (turn {turn+1}): {err}[/]")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                console.print("  [yellow]Quota hit — waiting 60s[/]")
                await asyncio.sleep(60)
            elif "TimeoutError" in type(e).__name__:
                consecutive_timeouts += 1
                console.print(f"  [yellow]Timeout ({consecutive_timeouts}/3) — trimming context[/]")
                if consecutive_timeouts >= 3:
                    console.print("  [red]3 consecutive timeouts — ending demo[/]")
                    break
                if len(contents) > 3:
                    contents = [contents[0]] + contents[-2:]
            else:
                break
            continue

        consecutive_timeouts = 0  # Reset on successful response

        # Filter thought parts
        model_parts = [
            p for p in (response.candidates[0].content.parts or [])
            if not getattr(p, "thought", False)
        ]

        has_fc = any(hasattr(p, "function_call") and p.function_call for p in model_parts)

        if not has_fc:
            # Model is done — print final text
            for p in model_parts:
                if hasattr(p, "text") and p.text:
                    console.print(f"\n[bold]{p.text}[/]")
            break

        contents.append(types.Content(role="model", parts=model_parts))

        # Execute tool calls
        fn_response_parts = []
        for part in model_parts:
            if not (hasattr(part, "function_call") and part.function_call):
                continue

            fc = part.function_call
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            safe_args = str(fn_args).encode("ascii", errors="replace").decode("ascii")
            console.print(f"  [dim]Turn {turn+1}: {fn_name}({safe_args})[/]")

            try:
                result = await dispatch_tool(computer, fn_name, fn_args)
                fn_response_parts.append(types.Part(
                    function_response=types.FunctionResponse(name=fn_name, response=result)
                ))
            except Exception as e:
                fn_response_parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fn_name, response={"error": str(e)}
                    )
                ))

        # Inject screenshot after tool calls
        try:
            post_state = await computer.current_state()
            if post_state.screenshot:
                scr_b64 = base64.b64encode(post_state.screenshot).decode()
                fn_response_parts.append(
                    types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=scr_b64))
                )
        except Exception:
            pass

        contents.append(types.Content(role="user", parts=fn_response_parts))

        # Aggressive context trim: keep first + last 4 turns
        if len(contents) > 6:
            contents = [contents[0]] + contents[-4:]

    elapsed = time.monotonic() - start
    console.print(f"\n[bold green]Demo complete — {elapsed:.0f}s elapsed[/]")

    await computer.close()
    if computer.video_path:
        console.print(f"[bold green]Recording: {computer.video_path}[/]")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="QA Navigator Demo Runner")
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--instruction", required=True, help="Agent instruction")
    parser.add_argument("--recording-dir", required=True, help="Recording output dir")
    args = parser.parse_args()

    asyncio.run(run_demo(args.url, args.instruction, args.recording_dir))


if __name__ == "__main__":
    main()
