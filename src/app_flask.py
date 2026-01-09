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
        event_queue.put({"type": "error", "message": str(e)})
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

        function addToolCallBlock(name, args) {
            if (!currentAssistantMessage) return;

            const block = document.createElement('div');
            block.className = 'tool-call-block';
            block.dataset.toolName = name;

            const desc = getToolDescription(name);
            const argsStr = formatArgsForDisplay(args);

            block.innerHTML = `
                <div class="tool-header">
                    <span class="tool-name">${escapeHtml(name)}</span>
                    <span class="tool-desc">${escapeHtml(desc)}</span>
                </div>
                <div class="tool-section in-section">
                    <div class="tool-section-label">IN</div>
                    <pre>${escapeHtml(argsStr) || '(no arguments)'}</pre>
                </div>
                <div class="tool-section out-section">
                    <div class="tool-section-label">OUT</div>
                    <pre class="tool-result">Running...</pre>
                </div>
            `;
            currentAssistantMessage.appendChild(block);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function updateToolResultBlock(name, result, success) {
            if (!currentAssistantMessage) return;

            const blocks = currentAssistantMessage.querySelectorAll('.tool-call-block');
            for (let i = blocks.length - 1; i >= 0; i--) {
                if (blocks[i].dataset.toolName === name) {
                    blocks[i].classList.add(success ? 'done' : 'error');

                    const resultPre = blocks[i].querySelector('.tool-result');
                    if (resultPre) {
                        const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
                        const truncated = resultStr.length > 1500 ? resultStr.substring(0, 1500) + '\\n...(truncated)' : resultStr;
                        resultPre.textContent = truncated;
                    }
                    break;
                }
            }
            chatContainer.scrollTop = chatContainer.scrollHeight;
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
