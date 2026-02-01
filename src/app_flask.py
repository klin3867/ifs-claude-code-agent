"""
Flask Chat UI for IFS Cloud ERP Agent.
Thin wrapper around agent.py - all logic lives there.

Run with: python src/app_flask.py
"""
import json
import queue
import threading
from pathlib import Path

from flask import Flask, render_template_string, request, Response

# Import the Agent
from agent import Agent

app = Flask(__name__)

# Global state
_agent = None
_conversation_history = []
MAX_HISTORY_MESSAGES = 20  # Keep last 20 messages (10 turns) to prevent context bloat


def format_error_message(error: str) -> str:
    """Format raw API errors into user-friendly messages."""
    error_lower = error.lower()

    # Anthropic credit/billing errors
    if "credit balance" in error_lower or "billing" in error_lower:
        return ("API Credit Error: Your Anthropic account has insufficient credits. "
                "Please add credits at https://console.anthropic.com/settings/billing")

    # Rate limiting
    if "rate limit" in error_lower or "too many requests" in error_lower:
        return "Rate Limited: Too many requests. Please wait a moment and try again."

    # Authentication errors
    if "invalid api key" in error_lower or "authentication" in error_lower:
        return "Authentication Error: Invalid API key. Please check your ANTHROPIC_API_KEY."

    # Connection errors
    if "connection" in error_lower or "timeout" in error_lower:
        return "Connection Error: Could not reach the API. Please check your network connection."

    # MCP/tool errors
    if "mcp" in error_lower or "tool" in error_lower:
        return f"Tool Error: {error}"

    # Default: return original but with prefix
    return f"Error: {error}"


def get_agent() -> Agent:
    """Get or create the singleton agent instance."""
    global _agent
    if _agent is None:
        config_path = Path(__file__).parent.parent / "config" / "base_config.yaml"
        _agent = Agent.from_config(str(config_path))
    return _agent


def process_message(user_message: str, event_queue: queue.Queue):
    """Process message using Agent.run_streaming() and emit events."""
    global _conversation_history

    try:
        agent = get_agent()

        # Collect assistant response for history
        assistant_response = ""

        # Run agent with conversation history for continuity (like Claude Code)
        for event in agent.run_streaming(user_message, conversation_history=_conversation_history):
            event_queue.put(event)
            # Capture final response text
            if event.get("type") == "response":
                assistant_response += event.get("content", "")

        # Store both user and assistant messages in history
        _conversation_history.append({"role": "user", "content": user_message})
        if assistant_response:
            _conversation_history.append({"role": "assistant", "content": assistant_response})

        # Trim history to prevent unbounded growth (like Claude Code does)
        if len(_conversation_history) > MAX_HISTORY_MESSAGES:
            _conversation_history = _conversation_history[-MAX_HISTORY_MESSAGES:]

    except Exception as e:
        event_queue.put({"type": "error", "message": format_error_message(str(e))})
        event_queue.put({"type": "done"})


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '')

    if not user_message.strip():
        return Response("data: {\"type\": \"error\", \"message\": \"Empty message\"}\n\n",
                       mimetype='text/event-stream')

    event_queue = queue.Queue()

    # Start processing in background thread
    thread = threading.Thread(
        target=process_message,
        args=(user_message, event_queue)
    )
    thread.start()

    def generate():
        while True:
            try:
                event = event_queue.get(timeout=120)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Timeout'})}\n\n"
                break

    return Response(generate(), mimetype='text/event-stream')


@app.route('/clear', methods=['POST'])
def clear():
    global _conversation_history, _agent
    _conversation_history = []
    # Reset agent to clear its internal state
    _agent = None
    return {"status": "ok"}


@app.route('/health')
def health():
    """Health check endpoint for evaluation framework."""
    return {"status": "ok", "agent": "ifs-claude-code-agent"}


@app.route('/eval', methods=['POST'])
def eval_endpoint():
    """
    Evaluation endpoint that returns structured metadata.

    Request: {"query": "...", "return_metadata": true}
    Response: {
        "response": "...",
        "success": true/false,
        "error": null or "error message",
        "metadata": {
            "duration_ms": 1234,
            "tokens": {"input": 100, "output": 50, "total": 150},
            "turns": 3,
            "tool_calls": [{"name": "...", "args": {...}, "result": "..."}]
        }
    }
    """
    import time

    data = request.json
    query = data.get('query', '')

    if not query.strip():
        return {"error": "Empty query", "success": False}, 400

    start_time = time.perf_counter()

    # Track metrics
    metrics = {
        "turns": 0,
        "tool_calls": [],
        "tokens": {"input": 0, "output": 0, "total": 0}
    }

    final_response = ""
    error_message = None

    try:
        agent = get_agent()

        # Run agent and collect metrics from streaming events
        for event in agent.run_streaming(query):
            event_type = event.get("type")

            if event_type == "thinking":
                metrics["turns"] += 1
            elif event_type == "token_usage":
                # Accumulate token usage across all LLM calls
                metrics["tokens"]["input"] += event.get("input_tokens", 0)
                metrics["tokens"]["output"] += event.get("output_tokens", 0)
                metrics["tokens"]["total"] = metrics["tokens"]["input"] + metrics["tokens"]["output"]
            elif event_type == "tool_call":
                metrics["tool_calls"].append({
                    "name": event.get("name", ""),
                    "args": event.get("arguments", {}),
                    "result": ""  # Will be filled by tool_result event
                })
            elif event_type == "tool_result":
                # Update the last tool call with its result
                if metrics["tool_calls"]:
                    result = event.get("result", "")
                    # Truncate long results
                    metrics["tool_calls"][-1]["result"] = result[:500] if len(result) > 500 else result
            elif event_type == "response":
                final_response += event.get("content", "")
            elif event_type == "error":
                error_message = event.get("message", "Unknown error")

    except Exception as e:
        error_message = str(e)

    end_time = time.perf_counter()
    duration_ms = (end_time - start_time) * 1000

    # Determine success - must have actual LLM execution
    success = (
        error_message is None and
        metrics["turns"] > 0 and
        metrics["tokens"]["total"] > 0 and
        len(final_response.strip()) > 0
    )

    return {
        "response": final_response,
        "success": success,
        "error": error_message,
        "metadata": {
            "duration_ms": duration_ms,
            "tokens": metrics["tokens"],
            "turns": metrics["turns"],
            "tool_calls": metrics["tool_calls"]
        }
    }


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IFS Cloud ERP Agent</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg-primary: #f5f7fa;
            --bg-secondary: #ffffff;
            --bg-tertiary: #e8ecf0;
            --text-primary: #1a1a2e;
            --text-secondary: #4a5568;
            --text-tertiary: #718096;
            --accent: #3182ce;
            --accent-hover: #2c5282;
            --accent-light: #ebf8ff;
            --border: #e2e8f0;
            --user-bg: #e2e8f0;
            --assistant-bg: #ffffff;
            --success: #38a169;
            --success-bg: #c6f6d5;
            --error: #e53e3e;
            --error-bg: #fed7d7;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
            --shadow-md: 0 4px 6px rgba(0,0,0,0.07);
            --radius-sm: 8px;
            --radius-md: 12px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            display: flex;
            flex-direction: column;
            font-size: 15px;
            line-height: 1.6;
        }
        header {
            background: var(--bg-secondary);
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: var(--shadow-sm);
        }
        .header-brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        .header-logo {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #3182ce 0%, #63b3ed 100%);
            border-radius: var(--radius-sm);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 16px;
        }
        header h1 {
            font-size: 1.25rem;
            font-weight: 600;
        }
        header p {
            color: var(--text-tertiary);
            font-size: 0.8rem;
        }
        #main-container { flex: 1; display: flex; overflow: hidden; }

        /* Floating Tasks Panel */
        #todo-panel {
            position: fixed;
            top: 80px;
            right: 1rem;
            width: 280px;
            background: var(--bg-secondary);
            border: 2px solid var(--accent);
            border-radius: var(--radius-md);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 1000;
            max-height: 300px;
            overflow-y: auto;
        }
        #todo-header {
            padding: 0.75rem 1rem;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-tertiary);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        #todo-list {
            padding: 0.5rem 1rem;
        }
        .todo-item {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.25rem 0;
            font-size: 0.8rem;
        }
        .todo-checkbox {
            width: 14px;
            height: 14px;
            border: 2px solid var(--border);
            border-radius: 3px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            flex-shrink: 0;
        }
        .todo-item.completed .todo-checkbox {
            background: var(--success);
            border-color: var(--success);
            color: white;
        }
        .todo-item.in_progress .todo-checkbox {
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }
        .todo-item.completed .todo-text {
            text-decoration: line-through;
            color: var(--text-tertiary);
        }
        .todo-item.in_progress .todo-text {
            font-weight: 500;
            color: var(--accent);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-tertiary);
        }
        .status-dot.active {
            background: var(--success);
            animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Chat Panel */
        #chat-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        .quick-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            padding: 0.75rem 1.5rem;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
        }
        .quick-action {
            background: var(--bg-primary);
            color: var(--text-secondary);
            padding: 0.5rem 0.75rem;
            border-radius: 20px;
            font-size: 0.8rem;
            border: 1px solid var(--border);
            cursor: pointer;
        }
        .quick-action:hover {
            background: var(--bg-tertiary);
            color: var(--text-primary);
        }
        #chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            max-width: 900px;
            margin: 0 auto;
            width: 100%;
        }
        .message {
            padding: 1rem 1.25rem;
            border-radius: var(--radius-md);
            line-height: 1.6;
            box-shadow: var(--shadow-sm);
        }
        .user {
            background: var(--user-bg);
            align-self: flex-end;
            max-width: 70%;
        }
        .assistant {
            background: var(--assistant-bg);
            align-self: flex-start;
            max-width: 85%;
            border: 1px solid var(--border);
        }
        .assistant.streaming {
            border-color: var(--accent);
        }
        .assistant p { margin-bottom: 0.75rem; }
        .assistant p:last-child { margin-bottom: 0; }
        .assistant pre {
            background: #2d2d2d;
            color: #f8f8f2;
            padding: 1rem;
            border-radius: var(--radius-sm);
            overflow-x: auto;
            font-size: 0.85rem;
            margin: 0.75rem 0;
        }
        .assistant code {
            background: var(--bg-tertiary);
            padding: 0.1rem 0.3rem;
            border-radius: 4px;
            font-size: 0.85em;
        }
        .assistant pre code {
            background: none;
            padding: 0;
        }
        .assistant ul, .assistant ol {
            margin: 0.5rem 0 0.5rem 1.25rem;
        }

        /* Input Area */
        #input-area {
            background: var(--bg-secondary);
            padding: 1rem 1.5rem;
            border-top: 1px solid var(--border);
        }
        #input-wrapper {
            max-width: 900px;
            margin: 0 auto;
            display: flex;
            gap: 0.75rem;
        }
        #message-input {
            flex: 1;
            padding: 0.75rem 1rem;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            background: var(--bg-primary);
            font-size: 0.95rem;
            font-family: inherit;
            resize: none;
            min-height: 44px;
            max-height: 150px;
        }
        #message-input:focus {
            outline: none;
            border-color: var(--accent);
        }
        button {
            padding: 0.75rem 1.25rem;
            border: none;
            border-radius: var(--radius-sm);
            cursor: pointer;
            font-size: 0.9rem;
            font-weight: 500;
            font-family: inherit;
        }
        #send-btn {
            background: var(--accent);
            color: white;
        }
        #send-btn:hover { background: var(--accent-hover); }
        #send-btn:disabled {
            background: var(--border);
            color: var(--text-tertiary);
            cursor: not-allowed;
        }
        #clear-btn {
            background: transparent;
            color: var(--text-secondary);
            border: 1px solid var(--border);
        }
        #clear-btn:hover { background: var(--bg-tertiary); }

        /* Inline Tool Call Blocks */
        .tool-call-block {
            background: var(--bg-tertiary);
            border-radius: var(--radius-sm);
            margin: 0.75rem 0;
            font-size: 0.85rem;
            overflow: hidden;
        }
        .tool-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.5rem 0.75rem;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
        }
        .tool-name {
            font-weight: 600;
            color: var(--accent);
        }
        .tool-call-block.done .tool-name {
            color: var(--success);
        }
        .tool-call-block.error .tool-name {
            color: var(--error);
        }
        .tool-desc {
            color: var(--text-secondary);
            font-size: 0.8rem;
        }
        .tool-section {
            padding: 0.5rem 0.75rem;
            border-bottom: 1px solid var(--border);
        }
        .tool-section:last-child {
            border-bottom: none;
        }
        .tool-section-label {
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-tertiary);
            margin-bottom: 0.25rem;
        }
        .tool-section pre {
            margin: 0;
            padding: 0;
            font-size: 0.8rem;
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: var(--text-primary);
            background: transparent;
            max-height: 200px;
            overflow-y: auto;
        }
        .tool-section.out-section {
            background: var(--bg-secondary);
        }
        .tool-section.out-section pre {
            color: var(--text-secondary);
            background: transparent;
        }
        .tool-call-block pre {
            background: transparent;
            color: inherit;
            padding: 0;
            margin: 0;
        }

        /* Thinking Indicator */
        .thinking-indicator {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.85rem;
            color: var(--text-tertiary);
            font-style: italic;
            margin: 0.5rem 0;
        }
        .thinking-dot {
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            animation: pulse 1s ease-in-out infinite;
        }

        /* Token Usage Footer */
        .token-footer {
            display: flex;
            align-items: center;
            gap: 0.35rem;
            font-size: 0.75rem;
            color: var(--text-tertiary);
            margin-top: 0.75rem;
            padding-top: 0.5rem;
            border-top: 1px solid var(--border);
            opacity: 0.7;
        }
        .token-icon {
            font-size: 0.8rem;
        }

        /* Progress Display (cleaner CoT) */
        .progress-container {
            background: var(--bg-tertiary);
            border-radius: var(--radius-sm);
            padding: 0.75rem 1rem;
            margin: 0.5rem 0;
            font-family: 'SF Mono', Monaco, 'Consolas', monospace;
            font-size: 0.85rem;
        }
        .progress-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }
        .progress-spinner {
            display: inline-block;
            width: 14px;
            text-align: center;
        }
        .progress-list {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .progress-item {
            display: flex;
            align-items: flex-start;
            gap: 0.5rem;
            padding: 0.25rem 0;
            color: var(--text-secondary);
        }
        .progress-item.active {
            color: var(--accent);
        }
        .progress-item.done {
            color: var(--success);
        }
        .progress-item.error {
            color: var(--error);
        }
        .progress-arrow {
            flex-shrink: 0;
        }
        .progress-text {
            flex: 1;
        }
        .progress-details-toggle {
            font-size: 0.7rem;
            color: var(--text-tertiary);
            cursor: pointer;
            margin-top: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }
        .progress-details-toggle:hover {
            color: var(--text-secondary);
        }
        .progress-details {
            display: none;
            margin-top: 0.5rem;
            padding: 0.5rem;
            background: var(--bg-secondary);
            border-radius: var(--radius-sm);
            font-size: 0.75rem;
            max-height: 200px;
            overflow-y: auto;
        }
        .progress-details.expanded {
            display: block;
        }
        .progress-details pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .progress-done .progress-spinner {
            animation: none;
        }
        .progress-done .progress-header {
            color: var(--success);
        }

        /* Markdown Tables */
        .assistant table {
            border-collapse: collapse;
            width: 100%;
            margin: 0.75rem 0;
            font-size: 0.9rem;
        }
        .assistant th, .assistant td {
            border: 1px solid var(--border);
            padding: 0.5rem 0.75rem;
            text-align: left;
        }
        .assistant th {
            background: var(--bg-tertiary);
            font-weight: 600;
        }
        .assistant tr:nth-child(even) {
            background: var(--bg-secondary);
        }
    </style>
</head>
<body>
    <header>
        <div class="header-brand">
            <div class="header-logo">IFS</div>
            <div>
                <h1>IFS Cloud ERP Agent</h1>
                <p>Powered by MCP</p>
            </div>
        </div>
    </header>

    <div class="quick-actions">
        <button class="quick-action" onclick="sendExample('What inventory do we have for part 10106105?')">Check Inventory</button>
        <button class="quick-action" onclick="sendExample('Show me past due customer order lines')">Past Due Orders</button>
        <button class="quick-action" onclick="sendExample('Search for customers with Costco in the name')">Find Customers</button>
        <button class="quick-action" onclick="sendExample('What shop orders are available to release?')">Shop Orders</button>
    </div>

    <!-- Floating Tasks Panel -->
    <div id="todo-panel" style="display: none;">
        <div id="todo-header">
            <span class="status-dot" id="status-dot"></span>
            Tasks
        </div>
        <div id="todo-list"></div>
    </div>

    <div id="main-container">
        <div id="chat-panel">
            <div id="chat-container"></div>
            <div id="input-area">
                <div id="input-wrapper">
                    <textarea id="message-input" rows="1" placeholder="Ask about inventory, orders, customers..."></textarea>
                    <button id="send-btn" onclick="sendMessage()">Send</button>
                    <button id="clear-btn" onclick="clearChat()">Clear</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const chatContainer = document.getElementById('chat-container');
        const statusDot = document.getElementById('status-dot');
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const todoPanel = document.getElementById('todo-panel');
        const todoList = document.getElementById('todo-list');

        let currentAssistantMessage = null;
        let isProcessing = false;

        // Spinner animation
        const spinnerFrames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
        let spinnerIndex = 0;
        let spinnerInterval = null;

        function startSpinner() {
            if (spinnerInterval) return;
            spinnerInterval = setInterval(() => {
                const spinners = document.querySelectorAll('.progress-spinner');
                spinnerIndex = (spinnerIndex + 1) % spinnerFrames.length;
                spinners.forEach(s => {
                    if (!s.closest('.progress-done')) {
                        s.textContent = spinnerFrames[spinnerIndex];
                    }
                });
            }, 80);
        }

        function stopSpinner() {
            if (spinnerInterval) {
                clearInterval(spinnerInterval);
                spinnerInterval = null;
            }
        }

        function updateTodos(todos) {
            if (!todos || todos.length === 0) {
                todoPanel.style.display = 'none';
                statusDot.classList.remove('active');
                return;
            }
            todoPanel.style.display = 'block';
            const hasActive = todos.some(t => t.status === 'in_progress');
            if (hasActive) {
                statusDot.classList.add('active');
            } else {
                statusDot.classList.remove('active');
            }
            todoList.innerHTML = todos.map(t => {
                const status = t.status || 'pending';
                const icon = status === 'completed' ? '✓' : status === 'in_progress' ? '►' : '';
                return `<div class="todo-item ${status}">
                    <div class="todo-checkbox">${icon}</div>
                    <span class="todo-text">${t.content || ''}</span>
                </div>`;
            }).join('');
        }

        function addMessage(role, content) {
            const msg = document.createElement('div');
            msg.className = `message ${role}`;
            if (role === 'assistant') {
                msg.innerHTML = marked.parse(content);
            } else {
                msg.textContent = content;
            }
            chatContainer.appendChild(msg);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            return msg;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function getToolDescription(name) {
            const descriptions = {
                'MCPSearch': 'Search for MCP tools',
                'TodoWrite': 'Update task list',
                'get_inventory_stock': 'Query inventory levels',
                'create_shipment_order': 'Create shipment order',
                'add_shipment_order_line': 'Add line to shipment',
                'release_shipment_order': 'Release shipment order',
                'search_customer_orders': 'Search customer orders',
                'get_order_details': 'Get order details',
            };
            return descriptions[name] || name.replace(/_/g, ' ');
        }

        function formatArgsForDisplay(args) {
            if (!args || Object.keys(args).length === 0) return '';
            return JSON.stringify(args, null, 2);
        }

        // Translate tool call into human-readable progress message
        function getProgressMessage(name, args) {
            // MCPSearch patterns
            if (name === 'MCPSearch') {
                const query = args?.query || '';
                if (query.startsWith('select:') || query.startsWith('load:')) {
                    const toolName = query.split(':')[1];
                    return `Loading tool: ${toolName}`;
                }
                return `Finding tools for: ${query}`;
            }

            // TodoWrite
            if (name === 'TodoWrite') {
                const count = args?.todos?.length || 0;
                return `Updating task list (${count} items)`;
            }

            // Inventory tools
            if (name === 'get_inventory_stock') {
                const part = args?.part_no || args?.part || 'parts';
                const site = args?.site || 'all sites';
                return `Checking inventory: ${part} at ${site}`;
            }
            if (name === 'search_inventory_by_warehouse') {
                const wh = args?.warehouse || args?.warehouse_id || 'warehouse';
                return `Searching inventory in ${wh}`;
            }
            if (name === 'analyze_unreserved_demand_by_warehouse') {
                const target = args?.target_warehouse || '?';
                const days = args?.days_ahead || 7;
                if (args?.auto_create_shipments) {
                    return `Analyzing ${days}-day demand + creating shipments to ${target}`;
                }
                return `Analyzing ${days}-day demand for warehouse ${target}`;
            }

            // Shipment tools
            if (name === 'create_shipment_order') {
                const from = args?.from_warehouse || args?.from || '?';
                const to = args?.to_warehouse || args?.to || '?';
                return `Creating shipment: ${from} → ${to}`;
            }
            if (name === 'add_shipment_order_line') {
                const part = args?.part_no || '?';
                const qty = args?.qty_to_ship || args?.qty || '?';
                return `Adding line: ${qty}x ${part}`;
            }
            if (name === 'release_shipment_order') {
                const id = args?.shipment_order_id || '?';
                return `Releasing shipment #${id}`;
            }

            // Order tools
            if (name === 'search_customer_orders' || name === 'search_orders') {
                return `Searching customer orders`;
            }
            if (name === 'get_order_details') {
                const order = args?.order_no || '?';
                return `Getting details for order ${order}`;
            }
            if (name === 'get_order_lines') {
                const order = args?.order_no || '?';
                return `Getting lines for order ${order}`;
            }

            // Default: humanize the tool name
            return name.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
        }

        // Get or create the progress container
        function getProgressContainer() {
            if (!currentAssistantMessage) return null;

            let container = currentAssistantMessage.querySelector('.progress-container');
            if (!container) {
                container = document.createElement('div');
                container.className = 'progress-container';
                container.innerHTML = `
                    <div class="progress-header">
                        <span class="progress-spinner">⠋</span>
                        <span>Agent working...</span>
                    </div>
                    <ul class="progress-list"></ul>
                    <div class="progress-details-toggle" onclick="toggleProgressDetails(this)">
                        <span>▶</span> Show raw details
                    </div>
                    <div class="progress-details">
                        <pre></pre>
                    </div>
                `;
                currentAssistantMessage.appendChild(container);
                startSpinner();
            }
            return container;
        }

        function toggleProgressDetails(el) {
            const details = el.nextElementSibling;
            if (details.classList.contains('expanded')) {
                details.classList.remove('expanded');
                el.innerHTML = '<span>▶</span> Show raw details';
            } else {
                details.classList.add('expanded');
                el.innerHTML = '<span>▼</span> Hide raw details';
            }
        }

        // Track raw details for debugging
        let progressRawDetails = [];

        function addProgressItem(name, args) {
            const container = getProgressContainer();
            if (!container) return;

            const list = container.querySelector('.progress-list');
            const message = getProgressMessage(name, args);

            // Mark previous active item as done
            const prevActive = list.querySelector('.progress-item.active');
            if (prevActive) {
                prevActive.classList.remove('active');
                prevActive.classList.add('done');
                prevActive.querySelector('.progress-arrow').textContent = '✓';
            }

            // Add new item
            const item = document.createElement('li');
            item.className = 'progress-item active';
            item.dataset.toolName = name;
            item.innerHTML = `
                <span class="progress-arrow">→</span>
                <span class="progress-text">${escapeHtml(message)}</span>
            `;
            list.appendChild(item);

            // Store raw details
            progressRawDetails.push({
                tool: name,
                args: args,
                result: null
            });
            updateRawDetails(container);

            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function updateProgressItem(name, result, success) {
            const container = currentAssistantMessage?.querySelector('.progress-container');
            if (!container) return;

            const list = container.querySelector('.progress-list');
            const items = list.querySelectorAll('.progress-item');

            // Find and update the matching item
            for (let i = items.length - 1; i >= 0; i--) {
                if (items[i].dataset.toolName === name) {
                    items[i].classList.remove('active');
                    items[i].classList.add(success ? 'done' : 'error');
                    items[i].querySelector('.progress-arrow').textContent = success ? '✓' : '✗';
                    break;
                }
            }

            // Update raw details
            for (let i = progressRawDetails.length - 1; i >= 0; i--) {
                if (progressRawDetails[i].tool === name && progressRawDetails[i].result === null) {
                    progressRawDetails[i].result = result;
                    progressRawDetails[i].success = success;
                    break;
                }
            }
            updateRawDetails(container);

            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function updateRawDetails(container) {
            const detailsPre = container.querySelector('.progress-details pre');
            if (!detailsPre) return;

            const formatted = progressRawDetails.map((d, i) => {
                const args = JSON.stringify(d.args, null, 2);
                const resultStr = d.result !== null
                    ? (typeof d.result === 'string' ? d.result : JSON.stringify(d.result, null, 2))
                    : '(pending...)';
                const truncResult = resultStr.length > 500 ? resultStr.substring(0, 500) + '...' : resultStr;
                return `[${i + 1}] ${d.tool}\\nIN: ${args}\\nOUT: ${truncResult}`;
            }).join('\\n\\n');

            detailsPre.textContent = formatted;
        }

        function finalizeProgress() {
            stopSpinner();

            const container = currentAssistantMessage?.querySelector('.progress-container');
            if (!container) return;

            container.classList.add('progress-done');
            const header = container.querySelector('.progress-header');
            if (header) {
                header.innerHTML = '<span>✓</span> <span>Complete</span>';
            }

            // Mark any remaining active items as done
            const activeItems = container.querySelectorAll('.progress-item.active');
            activeItems.forEach(item => {
                item.classList.remove('active');
                item.classList.add('done');
                item.querySelector('.progress-arrow').textContent = '✓';
            });

            // Reset for next message
            progressRawDetails = [];
        }

        function addToolCallBlock(name, args) {
            // Use new progress display instead of detailed blocks
            addProgressItem(name, args);
        }

        function updateToolResultBlock(name, result, success) {
            // Use new progress display instead of detailed blocks
            updateProgressItem(name, result, success);
        }

        function addThinkingIndicator(step, status) {
            if (!currentAssistantMessage) return;

            const prev = currentAssistantMessage.querySelector('.thinking-indicator');
            if (prev) prev.remove();

            const indicator = document.createElement('div');
            indicator.className = 'thinking-indicator';
            indicator.innerHTML = `<span class="thinking-dot"></span> ${escapeHtml(status || 'Thinking...')}`;
            currentAssistantMessage.appendChild(indicator);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function sendExample(text) {
            messageInput.value = text;
            sendMessage();
        }

        async function sendMessage() {
            const message = messageInput.value.trim();
            if (!message || isProcessing) return;

            isProcessing = true;
            sendBtn.disabled = true;
            statusDot.classList.add('active');
            messageInput.value = '';

            addMessage('user', message);

            currentAssistantMessage = addMessage('assistant', '');
            currentAssistantMessage.classList.add('streaming');

            let fullText = '';
            let tokenUsage = {input: 0, output: 0};

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message})
                });

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;

                    const text = decoder.decode(value);
                    const lines = text.split('\\n');

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;

                        try {
                            const event = JSON.parse(line.slice(6));

                            switch (event.type) {
                                case 'thinking':
                                    addThinkingIndicator(event.step, event.status);
                                    break;

                                case 'tool_call':
                                    // Remove thinking indicator
                                    const thinkInd = currentAssistantMessage.querySelector('.thinking-indicator');
                                    if (thinkInd) thinkInd.remove();
                                    addToolCallBlock(event.name, event.arguments);
                                    break;

                                case 'tool_result':
                                    updateToolResultBlock(event.name, event.result, event.success !== false);
                                    break;

                                case 'todo_update':
                                    updateTodos(event.todos);
                                    break;

                                case 'response':
                                    // Remove thinking indicator
                                    const ti = currentAssistantMessage.querySelector('.thinking-indicator');
                                    if (ti) ti.remove();
                                    fullText += event.content;
                                    let textContainer = currentAssistantMessage.querySelector('.message-text');
                                    if (!textContainer) {
                                        textContainer = document.createElement('div');
                                        textContainer.className = 'message-text';
                                        currentAssistantMessage.appendChild(textContainer);
                                    }
                                    textContainer.innerHTML = marked.parse(fullText);
                                    chatContainer.scrollTop = chatContainer.scrollHeight;
                                    break;

                                case 'warning':
                                    console.warn('Warning:', event.message);
                                    break;

                                case 'token_usage':
                                    tokenUsage.input += event.input_tokens || 0;
                                    tokenUsage.output += event.output_tokens || 0;
                                    break;

                                case 'error':
                                    if (!fullText) {
                                        fullText = `Error: ${event.message}`;
                                        let errTextContainer = currentAssistantMessage.querySelector('.message-text');
                                        if (!errTextContainer) {
                                            errTextContainer = document.createElement('div');
                                            errTextContainer.className = 'message-text';
                                            currentAssistantMessage.appendChild(errTextContainer);
                                        }
                                        errTextContainer.innerHTML = marked.parse(fullText);
                                    }
                                    break;

                                case 'done':
                                    finalizeProgress();
                                    break;
                            }
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            } catch (e) {
                let catchTextContainer = currentAssistantMessage.querySelector('.message-text');
                if (!catchTextContainer) {
                    catchTextContainer = document.createElement('div');
                    catchTextContainer.className = 'message-text';
                    currentAssistantMessage.appendChild(catchTextContainer);
                }
                catchTextContainer.innerHTML = `<span style="color: var(--error)">Error: ${e.message}</span>`;
            }

            currentAssistantMessage.classList.remove('streaming');

            // Display token usage if we have any
            if (tokenUsage.input > 0 || tokenUsage.output > 0) {
                const total = tokenUsage.input + tokenUsage.output;
                const cost = ((tokenUsage.input * 0.003 + tokenUsage.output * 0.015) / 1000).toFixed(4);
                const tokenFooter = document.createElement('div');
                tokenFooter.className = 'token-footer';
                tokenFooter.innerHTML = `<span class="token-icon">📊</span> ${tokenUsage.input.toLocaleString()} in / ${tokenUsage.output.toLocaleString()} out · ~$${cost}`;
                currentAssistantMessage.appendChild(tokenFooter);
            }

            isProcessing = false;
            sendBtn.disabled = false;
            statusDot.classList.remove('active');
        }

        async function clearChat() {
            await fetch('/clear', {method: 'POST'});
            chatContainer.innerHTML = '';
            todoPanel.style.display = 'none';
        }

        messageInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 150) + 'px';
        });

        messageInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    </script>
</body>
</html>
'''


# Initialize agent at module load (before Flask spawns threads)
# This ensures MCP connects in the main thread where asyncio works properly
def _init_agent():
    global _agent
    if _agent is None:
        config_path = Path(__file__).parent.parent / "config" / "base_config.yaml"
        _agent = Agent.from_config(str(config_path))
        print(f"Agent initialized with MCP: {_agent.mcp is not None}")

_init_agent()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IFS Cloud ERP Agent UI")
    parser.add_argument("--port", type=int, default=5000, help="Port to run on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")

    args = parser.parse_args()

    print(f"Starting IFS Cloud ERP Agent UI at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=True, threaded=True)
