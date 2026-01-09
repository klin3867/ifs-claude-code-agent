"""
MCP (Model Context Protocol) client for DeepAgent.
Connects to MCP servers via SSE transport and executes tools.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    """Async MCP client using httpx with SSE support."""

    def __init__(self, url: str, timeout: float = 120.0):
        self.url = url.rstrip("/").replace("/sse", "")
        self.timeout = timeout
        self._tools_cache: Optional[List[Dict]] = None

    def _resolve_endpoint(self, endpoint: str) -> str:
        """Handle relative endpoint paths from SSE."""
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"{self.url}{endpoint}"

    async def _make_request(self, method: str, params: Optional[Dict] = None) -> Dict:
        """
        Make a JSON-RPC request with full MCP handshake.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Connect to SSE endpoint
            sse_url = f"{self.url}/sse"
            
            async with client.stream("GET", sse_url, headers={"Accept": "text/event-stream"}) as response:
                endpoint = None
                initialized = False
                msg_id = 1
                result = None

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()

                        if event_type == "endpoint":
                            # Got the endpoint - start handshake
                            endpoint = self._resolve_endpoint(data)

                            # Send initialize request
                            await client.post(
                                endpoint,
                                json={
                                    "jsonrpc": "2.0",
                                    "method": "initialize",
                                    "params": {
                                        "protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "clientInfo": {"name": "deepagent", "version": "1.0"}
                                    },
                                    "id": msg_id,
                                },
                            )
                            msg_id += 1

                        elif event_type == "message":
                            try:
                                msg_data = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            # Check for initialization response
                            if not initialized and "result" in msg_data and "capabilities" in msg_data.get("result", {}):
                                initialized = True

                                # Send initialized notification (no id = notification)
                                await client.post(
                                    endpoint,
                                    json={
                                        "jsonrpc": "2.0",
                                        "method": "notifications/initialized",
                                    },
                                )
                                await asyncio.sleep(0.05)  # Brief pause

                                # Now send the actual request
                                request_body = {
                                    "jsonrpc": "2.0",
                                    "method": method,
                                    "id": msg_id,
                                }
                                if params:
                                    request_body["params"] = params
                                await client.post(endpoint, json=request_body)
                                msg_id += 1

                            # Got our response
                            elif initialized and "result" in msg_data and msg_data.get("id") == msg_id - 1:
                                result = msg_data.get("result", {})
                                return result

                            # Error response
                            elif "error" in msg_data:
                                error = msg_data["error"]
                                raise Exception(f"MCP error {error.get('code')}: {error.get('message')}")

                return result or {}

    async def list_tools(self) -> List[Dict]:
        """Get available tools from the MCP server."""
        if self._tools_cache:
            return self._tools_cache

        result = await self._make_request("tools/list")
        self._tools_cache = result.get("tools", [])
        return self._tools_cache

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the MCP server."""
        result = await self._make_request(
            "tools/call",
            {"name": name, "arguments": arguments}
        )
        # MCP returns content array, extract text
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list) and len(content) > 0:
                first_item = content[0]
                if isinstance(first_item, dict) and "text" in first_item:
                    return json.loads(first_item["text"]) if first_item["text"].startswith("{") else {"result": first_item["text"]}
        return result


def mcp_to_openai_function(mcp_tools: List[Dict], compact: bool = False) -> List[Dict]:
    """Convert MCP tools to OpenAI function calling format.
    
    Args:
        mcp_tools: List of MCP tool definitions
        compact: If True, truncate descriptions and simplify schemas to reduce token count
    """
    result = []
    for tool in mcp_tools:
        desc = tool.get("description", "")
        schema = tool.get("inputSchema", {"type": "object", "properties": {}})
        
        if compact:
            # Truncate description to first 150 chars
            if len(desc) > 150:
                desc = desc[:147] + "..."
            # Simplify schema - keep only required properties and their types
            if "properties" in schema:
                simplified_props = {}
                for prop_name, prop_def in schema.get("properties", {}).items():
                    # Keep just type and description (truncated)
                    simplified = {"type": prop_def.get("type", "string")}
                    if "description" in prop_def:
                        prop_desc = prop_def["description"]
                        if len(prop_desc) > 80:
                            prop_desc = prop_desc[:77] + "..."
                        simplified["description"] = prop_desc
                    simplified_props[prop_name] = simplified
                schema = {
                    "type": "object",
                    "properties": simplified_props,
                    "required": schema.get("required", [])
                }
        
        result.append({
            "name": tool["name"],
            "description": desc,
            "parameters": schema
        })
    return result


class MCPToolCaller:
    """Tool caller that routes calls to MCP servers."""

    def __init__(self, planning_url: str = None, customer_url: str = None, compact: bool = True):
        self.planning_client = MCPClient(planning_url) if planning_url else None
        self.customer_client = MCPClient(customer_url) if customer_url else None
        self._tools: List[Dict] = []
        self._tool_to_server: Dict[str, str] = {}
        self._compact = compact

    async def initialize(self) -> List[Dict]:
        """Load tools from all MCP servers."""
        all_tools = []
        
        if self.planning_client:
            try:
                planning_tools = await self.planning_client.list_tools()
                for tool in planning_tools:
                    self._tool_to_server[tool["name"]] = "planning"
                all_tools.extend(planning_tools)
                logger.info(f"Loaded {len(planning_tools)} tools from Planning MCP server")
            except Exception as e:
                logger.error(f"Failed to load Planning MCP tools: {e}")

        if self.customer_client:
            try:
                customer_tools = await self.customer_client.list_tools()
                for tool in customer_tools:
                    self._tool_to_server[tool["name"]] = "customer"
                all_tools.extend(customer_tools)
                logger.info(f"Loaded {len(customer_tools)} tools from Customer MCP server")
            except Exception as e:
                logger.error(f"Failed to load Customer MCP tools: {e}")

        self._tools = all_tools
        return mcp_to_openai_function(all_tools, compact=self._compact)

    def get_tool_schema(self, tool_name: str) -> Dict[str, Any]:
        """
        Get the full schema for a specific tool (lazy-load pattern).
        
        This returns the complete parameter schema from the cached MCP tools,
        allowing the LLM to learn exact parameter names before calling.
        
        Args:
            tool_name: Name of the tool to get schema for
            
        Returns:
            Full tool schema with name, description, and inputSchema
        """
        for tool in self._tools:
            if tool.get("name") == tool_name:
                # Return a clean schema for LLM consumption
                return {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    "server": self._tool_to_server.get(tool_name, "unknown")
                }
        
        # Tool not found - provide helpful error with available tools
        available = [t["name"] for t in self._tools[:20]]  # First 20 for brevity
        return {
            "error": f"Tool '{tool_name}' not found",
            "available_tools_sample": available,
            "total_tools": len(self._tools)
        }

    def get_all_tool_names(self) -> List[str]:
        """Get list of all available tool names."""
        return [t["name"] for t in self._tools]

    def get_tools_by_names(self, names: List[str]) -> List[Dict]:
        """
        Get full OpenAI-format schemas for specific tool names.

        Used by native agent to expand available tools after search_tools.

        Args:
            names: List of tool names to get schemas for

        Returns:
            List of OpenAI function-format tool definitions
        """
        result = []
        for name in names:
            if name in self._tool_to_server:
                schema = self.get_tool_schema(name)
                if schema and "error" not in schema:
                    result.append({
                        "type": "function",
                        "function": {
                            "name": schema["name"],
                            "description": schema.get("description", ""),
                            "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
                        }
                    })
        return result

    async def call_tool(self, tool_call: Dict) -> Any:
        """Execute a tool call via the appropriate MCP server."""
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")
        arguments = func.get("arguments", {})

        server = self._tool_to_server.get(tool_name)
        if not server:
            return {"error": f"Unknown tool: {tool_name}"}

        client = self.planning_client if server == "planning" else self.customer_client
        if not client:
            return {"error": f"MCP server '{server}' not configured"}

        try:
            result = await client.call_tool(tool_name, arguments)

            # Defensive retry: some inventory queries have been observed to intermittently
            # return an empty locations list even when stock exists. If we get an empty
            # successful response, retry once before returning.
            if tool_name == "get_inventory_stock" and isinstance(result, dict):
                if result.get("ok") is True:
                    data = result.get("data") or {}
                    locations = data.get("locations")
                    if isinstance(locations, list) and len(locations) == 0:
                        await asyncio.sleep(0.2)
                        result_retry = await client.call_tool(tool_name, arguments)
                        if isinstance(result_retry, dict):
                            data_retry = result_retry.get("data") or {}
                            locations_retry = data_retry.get("locations")
                            if isinstance(locations_retry, list) and len(locations_retry) > 0:
                                return result_retry

            # Truncate large results to prevent rate limit errors
            result = self._truncate_result(result, tool_name)
            return result
        except Exception as e:
            return {"error": str(e)}

    def _truncate_result(self, result: Any, tool_name: str, max_chars: int = 3000) -> Any:
        """
        Truncate large results to prevent context bloat.

        Like Claude Code's output truncation - keeps summaries, limits detail rows.
        """
        if not isinstance(result, dict):
            return result

        result_str = json.dumps(result)
        if len(result_str) <= max_chars:
            return result

        # For inventory queries, keep totals but limit location rows
        if "data" in result:
            data = result["data"]

            # Truncate locations array (inventory queries)
            if "locations" in data and isinstance(data["locations"], list):
                original_count = len(data["locations"])
                if original_count > 5:
                    data["locations"] = data["locations"][:5]
                    data["_truncated"] = f"Showing 5 of {original_count} locations. Use warehouse filter to narrow."

            # Truncate stock_records array (warehouse searches)
            if "stock_records" in data and isinstance(data["stock_records"], list):
                original_count = len(data["stock_records"])
                if original_count > 10:
                    data["stock_records"] = data["stock_records"][:10]
                    data["_truncated"] = f"Showing 10 of {original_count} records. Use filters to narrow."

            # Truncate lines array (order queries)
            if "lines" in data and isinstance(data["lines"], list):
                original_count = len(data["lines"])
                if original_count > 10:
                    data["lines"] = data["lines"][:10]
                    data["_truncated"] = f"Showing 10 of {original_count} lines."

            # Truncate value array (generic OData results)
            if "value" in data and isinstance(data["value"], list):
                original_count = len(data["value"])
                if original_count > 10:
                    data["value"] = data["value"][:10]
                    data["_truncated"] = f"Showing 10 of {original_count} results."

        return result


# Convenience function to get MCP tools in OpenAI format
async def get_mcp_tools(planning_url: str = None, customer_url: str = None) -> List[Dict]:
    """Get all MCP tools in OpenAI function format."""
    caller = MCPToolCaller(planning_url, customer_url)
    return await caller.initialize()
