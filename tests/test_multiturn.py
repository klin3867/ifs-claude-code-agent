#!/usr/bin/env python3
"""
Multi-turn conversation tests for IFS Cloud ERP Agent.
Tests context retention, domain knowledge, and workflow completion.
"""

import requests
import json
import time
import sys
from typing import List, Dict, Any

BASE_URL = "http://127.0.0.1:5000"
DELAY_BETWEEN_TURNS = 2  # seconds - avoid rate limits
DELAY_BETWEEN_SESSIONS = 5  # seconds - clear rate limit window


def send_message(message: str, timeout: int = 120) -> Dict[str, Any]:
    """Send a message and return the response (handles SSE stream)."""
    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json={"message": message},
            timeout=timeout,
            stream=True
        )

        # Parse SSE events
        events = []
        tool_calls = []
        tool_results = []
        final_text = ""
        iterations = 0

        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    try:
                        event = json.loads(line[6:])
                        events.append(event)

                        if event.get("type") == "thinking":
                            iterations = event.get("step", iterations)
                        elif event.get("type") == "text":
                            final_text = event.get("content", "")
                        elif event.get("type") == "tool_call":
                            tool_calls.append({
                                "name": event.get("name"),
                                "input": event.get("args", {})
                            })
                        elif event.get("type") == "tool_result":
                            tool_results.append({
                                "name": event.get("name"),
                                "result": event.get("result", "")
                            })
                        elif event.get("type") == "done":
                            break
                        elif event.get("type") == "error":
                            return {"error": event.get("message", "Unknown error")}
                    except json.JSONDecodeError:
                        continue

        return {
            "final_response": final_text,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "iterations": iterations,
            "events": events
        }
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def clear_session():
    """Clear the conversation history."""
    try:
        requests.post(f"{BASE_URL}/clear", timeout=10)
    except:
        pass


def extract_response_text(response: Dict) -> str:
    """Extract the assistant's text response."""
    return response.get("final_response", "")


def extract_tool_calls(response: Dict) -> List[Dict]:
    """Extract tool calls from response."""
    return response.get("tool_calls", [])


def run_session(name: str, turns: List[str], checks: List[callable] = None) -> Dict:
    """Run a multi-turn session and return results."""
    print(f"\n{'='*60}")
    print(f"SESSION: {name}")
    print(f"{'='*60}")

    clear_session()
    time.sleep(1)

    results = {
        "name": name,
        "turns": [],
        "passed": True,
        "errors": []
    }

    for i, message in enumerate(turns):
        print(f"\n--- Turn {i+1}/{len(turns)} ---")
        print(f"USER: {message}")

        response = send_message(message)

        if "error" in response:
            print(f"ERROR: {response['error']}")
            results["errors"].append(f"Turn {i+1}: {response['error']}")
            results["passed"] = False
            break

        text = extract_response_text(response)
        tool_calls = extract_tool_calls(response)

        print(f"ASSISTANT: {text[:500]}..." if len(text) > 500 else f"ASSISTANT: {text}")
        print(f"TOOLS CALLED: {[tc['name'] for tc in tool_calls]}")

        turn_result = {
            "message": message,
            "response": text,
            "tool_calls": tool_calls,
            "iterations": response.get("iterations", 0)
        }
        results["turns"].append(turn_result)

        # Run checks if provided
        if checks and i < len(checks) and checks[i]:
            check_result = checks[i](response, text, tool_calls)
            if not check_result["passed"]:
                results["errors"].append(f"Turn {i+1}: {check_result['error']}")
                results["passed"] = False

        # Delay to avoid rate limits
        if i < len(turns) - 1:
            time.sleep(DELAY_BETWEEN_TURNS)

    return results


def check_tool_called(expected_tool: str):
    """Check that a specific tool was called."""
    def checker(response, text, tool_calls):
        tool_names = [tc["name"] for tc in tool_calls]
        if expected_tool in tool_names:
            return {"passed": True}
        return {"passed": False, "error": f"Expected {expected_tool} to be called, got {tool_names}"}
    return checker


def check_warehouse_value(param_name: str, expected_value: str):
    """Check that a warehouse parameter has the expected value."""
    def checker(response, text, tool_calls):
        for tc in tool_calls:
            if param_name in tc.get("input", {}):
                actual = tc["input"][param_name]
                if actual == expected_value:
                    return {"passed": True}
                return {"passed": False, "error": f"Expected {param_name}='{expected_value}', got '{actual}'"}
        return {"passed": True}  # Param not found, don't fail
    return checker


def check_response_contains(substring: str):
    """Check that response contains a substring."""
    def checker(response, text, tool_calls):
        if substring.lower() in text.lower():
            return {"passed": True}
        return {"passed": False, "error": f"Expected response to contain '{substring}'"}
    return checker


# =============================================================================
# TEST SESSIONS
# =============================================================================

def test_session_1_inventory():
    """Session 1: Inventory Deep Dive (3 turns)"""
    return run_session(
        "Inventory Deep Dive",
        [
            "What inventory do we have for part 10106105?",
            "Which warehouse has the most stock?",
            "Is there any at warehouse 205?"
        ],
        [
            check_tool_called("get_inventory_stock"),
            None,  # Analysis turn
            None   # Follow-up turn
        ]
    )


def test_session_2_shipment():
    """Session 2: Shipment Creation Flow (4 turns)"""
    return run_session(
        "Shipment Creation Flow",
        [
            "I need to move some parts from 105 to 205",
            "Use part 10106105, quantity 5",
            "Add the line to the shipment",
            "Release it"
        ],
        [
            check_tool_called("create_shipment_order"),
            check_tool_called("add_shipment_order_line"),
            None,
            check_tool_called("release_shipment_order")
        ]
    )


def test_session_3_warehouse_105():
    """Session 3: Warehouse 105 Handling (3 turns)"""
    return run_session(
        "Warehouse 105 Handling",
        [
            "Create a shipment from warehouse 105 to 205",
            "What warehouse ID did you use for the source?",
            "Send 10 units of part 10106105"
        ],
        [
            check_warehouse_value("from_warehouse", "AC"),  # 105 should map to AC
            check_response_contains("AC"),  # Should mention AC, not AC-A105
            check_tool_called("add_shipment_order_line")
        ]
    )


def test_session_4_orders():
    """Session 4: Order Lookup Chain (3 turns)"""
    return run_session(
        "Order Lookup Chain",
        [
            "Show me past due customer order lines",
            "What's the status of order *1063?",
            "Can you check inventory for that part?"
        ],
        [
            None,
            None,
            check_tool_called("get_inventory_stock")
        ]
    )


def test_session_5_error_recovery():
    """Session 5: Error Recovery (2 turns)"""
    return run_session(
        "Error Recovery",
        [
            "Move part ABC123 from 105 to 205",
            "Actually use part 10106105 instead"
        ],
        [
            None,  # May error or ask for clarification
            check_tool_called("create_shipment_order")
        ]
    )


def test_session_6_corrections():
    """Session 6: Multi-Step with Corrections (3 turns)"""
    return run_session(
        "Multi-Step with Corrections",
        [
            "Check stock for 10106105 at site AC",
            "Now create a shipment to move it to 110",
            "Wait, I meant warehouse 205, not 110"
        ],
        [
            check_tool_called("get_inventory_stock"),
            check_tool_called("create_shipment_order"),
            None  # Should handle correction
        ]
    )


def test_session_7_complex():
    """Session 7: Complex Workflow (2 turns)"""
    return run_session(
        "Complex Workflow",
        [
            "I need to transfer inventory - check what we have at 205, then move half to 110",
            "Complete the shipment"
        ],
        [
            check_tool_called("get_inventory_stock"),
            check_tool_called("release_shipment_order")
        ]
    )


def main():
    """Run all test sessions."""
    print("="*60)
    print("IFS CLOUD ERP AGENT - MULTI-TURN CONVERSATION TESTS")
    print("="*60)

    # Check server is running
    try:
        requests.get(BASE_URL, timeout=5)
    except:
        print("ERROR: Flask server not running at", BASE_URL)
        print("Start it with: python src/app_flask.py")
        sys.exit(1)

    all_results = []

    # Run all sessions
    sessions = [
        test_session_1_inventory,
        test_session_2_shipment,
        test_session_3_warehouse_105,
        test_session_4_orders,
        test_session_5_error_recovery,
        test_session_6_corrections,
        test_session_7_complex,
    ]

    for i, session_fn in enumerate(sessions):
        print(f"\n\n{'#'*60}")
        print(f"# RUNNING TEST {i+1}/{len(sessions)}")
        print(f"{'#'*60}")

        result = session_fn()
        all_results.append(result)

        # Delay between sessions
        if i < len(sessions) - 1:
            print(f"\nWaiting {DELAY_BETWEEN_SESSIONS}s before next session...")
            time.sleep(DELAY_BETWEEN_SESSIONS)

    # Summary
    print("\n\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    passed = sum(1 for r in all_results if r["passed"])
    failed = len(all_results) - passed

    for r in all_results:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        print(f"{status} - {r['name']}")
        if r["errors"]:
            for err in r["errors"]:
                print(f"       └─ {err}")

    print(f"\nTotal: {passed}/{len(all_results)} sessions passed")

    # Write detailed results to file
    with open("/tmp/ifs_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nDetailed results saved to /tmp/ifs_test_results.json")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
