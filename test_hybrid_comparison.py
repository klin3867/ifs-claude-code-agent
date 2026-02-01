#!/usr/bin/env python3
"""
Compare agent performance: Claude-only vs Hybrid (Claude + local gpt-oss-20b)

This script runs the same prompt twice:
1. All agents use Claude Sonnet (current setup)
2. Explore/Summarizer use local gpt-oss-20b, general-purpose uses Claude

Outputs: response quality, token usage, cost, duration
"""

import os
import sys
import time
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from llm_client import get_client
from prompt_loader import PromptLoader
from agent import Agent


def create_claude_only_agent(config_path: str) -> Agent:
    """Create agent where all agent types use Claude."""
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Primary model (Claude)
    llm = get_client(
        "anthropic",
        model=config.get("anthropic_model", "claude-sonnet-4-20250514"),
    )

    # Prompts
    prompts_dir = Path(config_path).parent / config.get("prompts_dir", "../ifs-prompts")
    prompt_loader = PromptLoader(str(prompts_dir))

    # MCP
    mcp = None
    try:
        from tools.mcp_client import MCPToolCaller
        import asyncio
        planning_url = config.get("mcp_planning_url", "http://localhost:8000/sse")
        customer_url = config.get("mcp_customer_url", "http://localhost:8001/sse")
        mcp = MCPToolCaller(planning_url=planning_url, customer_url=customer_url)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(mcp.initialize())
    except Exception as e:
        print(f"MCP init failed: {e}")

    return Agent(prompt_loader=prompt_loader, llm=llm, mcp=mcp)


def create_hybrid_agent(config_path: str) -> Agent:
    """Create agent with Claude for main + gpt-oss-20b for Explore/Summarizer."""
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Primary model (Claude) for general-purpose and Plan
    llm = get_client(
        "anthropic",
        model=config.get("anthropic_model", "claude-sonnet-4-20250514"),
    )

    # Aux model (local gpt-oss-20b) for Explore and Summarizer
    aux_llm = get_client(
        "openai",
        model=config.get("openai_model", "openai/gpt-oss-20b"),
        base_url=config.get("openai_base_url", "http://127.0.0.1:1234/v1"),
        reasoning_effort=config.get("openai_reasoning_effort", "high"),
    )

    # Prompts
    prompts_dir = Path(config_path).parent / config.get("prompts_dir", "../ifs-prompts")
    prompt_loader = PromptLoader(str(prompts_dir))

    # MCP
    mcp = None
    try:
        from tools.mcp_client import MCPToolCaller
        import asyncio
        planning_url = config.get("mcp_planning_url", "http://localhost:8000/sse")
        customer_url = config.get("mcp_customer_url", "http://localhost:8001/sse")
        mcp = MCPToolCaller(planning_url=planning_url, customer_url=customer_url)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(mcp.initialize())
    except Exception as e:
        print(f"MCP init failed: {e}")

    # Create agent with both LLMs
    agent = Agent(prompt_loader=prompt_loader, llm=llm, mcp=mcp)

    # Inject aux_llm and routing (monkey-patch until proper implementation)
    agent.aux_llm = aux_llm
    agent.model_routing = {
        "smart_agents": ["general-purpose", "Plan"],
        "aux_agents": ["Explore", "summarizer"],
    }

    # Override _get_llm_for_agent_type method
    def _get_llm_for_agent_type(self, agent_type: str):
        if agent_type in self.model_routing.get("aux_agents", []):
            print(f"  [Routing] {agent_type} -> aux model (gpt-oss-20b)")
            return self.aux_llm
        print(f"  [Routing] {agent_type} -> smart model (Claude)")
        return self.llm

    import types
    agent._get_llm_for_agent_type = types.MethodType(_get_llm_for_agent_type, agent)

    # Override _spawn_subagent to use routing
    original_spawn = agent._spawn_subagent
    def _spawn_subagent_with_routing(self, args):
        subagent_type = args.get("subagent_type", "general-purpose")
        prompt = args.get("prompt", "")

        print(f"\n[Spawning {subagent_type} subagent]")

        # Select LLM based on agent type
        selected_llm = self._get_llm_for_agent_type(subagent_type)

        subagent = Agent(
            prompt_loader=self.prompt_loader,
            llm=selected_llm,
            mcp=self.mcp,
            workdir=str(self.workdir),
        )

        return subagent.run(prompt, subagent_type)

    agent._spawn_subagent = types.MethodType(_spawn_subagent_with_routing, agent)

    return agent


def run_test(agent: Agent, prompt: str, label: str) -> dict:
    """Run a prompt and collect metrics."""
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"{'='*60}")

    metrics = {
        "label": label,
        "input_tokens": 0,
        "output_tokens": 0,
        "turns": 0,
        "tool_calls": 0,
        "duration_ms": 0,
        "response": "",
        "success": False,
        "error": None,
    }

    start = time.perf_counter()

    try:
        for event in agent.run_streaming(prompt):
            event_type = event.get("type")

            if event_type == "token_usage":
                metrics["input_tokens"] += event.get("input_tokens", 0)
                metrics["output_tokens"] += event.get("output_tokens", 0)
                metrics["turns"] += 1
            elif event_type == "tool_call":
                metrics["tool_calls"] += 1
                print(f"  [{metrics['tool_calls']}] {event.get('name')}")
            elif event_type == "response":
                metrics["response"] = event.get("text", "")[:500]
            elif event_type == "done":
                metrics["success"] = True
                break

    except Exception as e:
        metrics["error"] = str(e)
        print(f"  ERROR: {e}")

    metrics["duration_ms"] = (time.perf_counter() - start) * 1000

    return metrics


def calculate_cost(metrics: dict, is_hybrid: bool = False) -> dict:
    """Calculate cost based on token usage."""
    # Claude Sonnet pricing
    claude_input_rate = 3.00 / 1_000_000
    claude_output_rate = 15.00 / 1_000_000

    # For hybrid, assume 70% of tokens go to aux model (Explore subagent)
    if is_hybrid:
        # Main agent tokens (30%) at Claude rates
        main_input = metrics["input_tokens"] * 0.3
        main_output = metrics["output_tokens"] * 0.3
        main_cost = (main_input * claude_input_rate) + (main_output * claude_output_rate)

        # Subagent tokens (70%) are free (local model)
        aux_cost = 0

        return {
            "total": main_cost + aux_cost,
            "claude_portion": main_cost,
            "aux_portion": aux_cost,
            "savings_vs_claude_only": None,  # Will be calculated later
        }
    else:
        total = (metrics["input_tokens"] * claude_input_rate) + \
                (metrics["output_tokens"] * claude_output_rate)
        return {
            "total": total,
            "claude_portion": total,
            "aux_portion": 0,
        }


def print_comparison(claude_metrics: dict, hybrid_metrics: dict):
    """Print side-by-side comparison."""
    print(f"\n{'='*60}")
    print("COMPARISON RESULTS")
    print(f"{'='*60}")

    claude_cost = calculate_cost(claude_metrics, is_hybrid=False)
    hybrid_cost = calculate_cost(hybrid_metrics, is_hybrid=True)

    if claude_cost["total"] > 0:
        savings_pct = ((claude_cost["total"] - hybrid_cost["total"]) / claude_cost["total"]) * 100
    else:
        savings_pct = 0

    print(f"\n{'Metric':<25} {'Claude-Only':>15} {'Hybrid':>15} {'Diff':>15}")
    print("-" * 70)
    print(f"{'Input Tokens':<25} {claude_metrics['input_tokens']:>15,} {hybrid_metrics['input_tokens']:>15,} {hybrid_metrics['input_tokens'] - claude_metrics['input_tokens']:>+15,}")
    print(f"{'Output Tokens':<25} {claude_metrics['output_tokens']:>15,} {hybrid_metrics['output_tokens']:>15,} {hybrid_metrics['output_tokens'] - claude_metrics['output_tokens']:>+15,}")
    print(f"{'Total Tokens':<25} {claude_metrics['input_tokens'] + claude_metrics['output_tokens']:>15,} {hybrid_metrics['input_tokens'] + hybrid_metrics['output_tokens']:>15,}")
    print(f"{'LLM Turns':<25} {claude_metrics['turns']:>15} {hybrid_metrics['turns']:>15}")
    print(f"{'Tool Calls':<25} {claude_metrics['tool_calls']:>15} {hybrid_metrics['tool_calls']:>15}")
    print(f"{'Duration (sec)':<25} {claude_metrics['duration_ms']/1000:>15.1f} {hybrid_metrics['duration_ms']/1000:>15.1f}")
    print(f"{'Estimated Cost':<25} ${claude_cost['total']:>14.4f} ${hybrid_cost['total']:>14.4f} {savings_pct:>+14.1f}%")
    print(f"{'Success':<25} {str(claude_metrics['success']):>15} {str(hybrid_metrics['success']):>15}")

    print(f"\n{'='*60}")
    print("RESPONSE PREVIEW")
    print(f"{'='*60}")
    print(f"\nClaude-Only Response:\n{claude_metrics['response'][:300]}...")
    print(f"\nHybrid Response:\n{hybrid_metrics['response'][:300]}...")

    if hybrid_metrics["error"]:
        print(f"\nHybrid Error: {hybrid_metrics['error']}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compare Claude-only vs Hybrid agent performance")
    parser.add_argument("--config", default="config/base_config.yaml", help="Config file path")
    parser.add_argument("--prompt", default=None, help="Custom prompt to test")
    parser.add_argument("--claude-only", action="store_true", help="Run only Claude test")
    parser.add_argument("--hybrid-only", action="store_true", help="Run only Hybrid test")
    args = parser.parse_args()

    # Default test prompt
    prompt = args.prompt or """Check Customer Order that need to deliver this week. For those orders, determine if we have sufficient inventory available in the 205 warehouse. If we do not have sufficient inventory in stock, determine if there is sufficient inventory available in the 110 and 105 warehouses. For the inventory available in those warehouses arrange shipment orders to move them to the 205 warehouse."""

    print("="*60)
    print("HYBRID MODEL COMPARISON TEST")
    print("="*60)
    print(f"Prompt: {prompt[:100]}...")

    config_path = str(Path(__file__).parent / args.config)

    # Ensure LM Studio is running for hybrid test
    if not args.claude_only:
        print("\nNOTE: For hybrid test, ensure LM Studio is running with gpt-oss-20b loaded.")
        print("      Server should be at http://127.0.0.1:1234")

    claude_metrics = None
    hybrid_metrics = None

    # Run Claude-only test
    if not args.hybrid_only:
        os.environ["OPENAI_API_KEY"] = "not-needed"  # Prevent OpenAI errors
        agent_claude = create_claude_only_agent(config_path)
        claude_metrics = run_test(agent_claude, prompt, "Claude-Only (All Agents)")

    # Run Hybrid test
    if not args.claude_only:
        os.environ["OPENAI_API_KEY"] = "lm-studio"  # For local model
        agent_hybrid = create_hybrid_agent(config_path)
        hybrid_metrics = run_test(agent_hybrid, prompt, "Hybrid (Claude + gpt-oss-20b)")

    # Print comparison if both ran
    if claude_metrics and hybrid_metrics:
        print_comparison(claude_metrics, hybrid_metrics)
    elif claude_metrics:
        print(f"\nClaude-Only Results:")
        print(f"  Tokens: {claude_metrics['input_tokens']:,} in / {claude_metrics['output_tokens']:,} out")
        print(f"  Duration: {claude_metrics['duration_ms']/1000:.1f}s")
        print(f"  Success: {claude_metrics['success']}")
    elif hybrid_metrics:
        print(f"\nHybrid Results:")
        print(f"  Tokens: {hybrid_metrics['input_tokens']:,} in / {hybrid_metrics['output_tokens']:,} out")
        print(f"  Duration: {hybrid_metrics['duration_ms']/1000:.1f}s")
        print(f"  Success: {hybrid_metrics['success']}")


if __name__ == "__main__":
    main()
