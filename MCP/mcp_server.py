"""
MCP 入门第1步：最简单的 MCP Server
暴露一个 get_weather 工具，客户端可以远程调用它。

跑法：python demo/01_mcp_server.py（会一直运行，Ctrl+C 退出）
"""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import httpx

# 1. 创建 server
server = Server("weather-server")


# 2. 声明工具：必须返回 list[Tool]
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_weather",
            description="查询指定经纬度的天气",
            inputSchema={
                "type": "object",
                "properties": {
                    "latitude": {"type": "string", "description": "经度，如 39.9087"},
                    "longitude": {"type": "string", "description": "纬度，如 116.4713"}
                },
                "required": ["latitude", "longitude"],
            },
        )
    ]

# 3. 实现工具逻辑：返回 list[TextContent]
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_weather":
        latitude = arguments["latitude"]
        longitude = arguments["longitude"]

        url = f"https://wttr.in/{latitude},{longitude}?format=j1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
        res_data = resp.json()
        current = res_data["current_condition"][0]
        weather_data = {
            "weather": current["weatherDesc"][0]["value"].strip(),
            "temperature": int(current["temp_C"]),
        }
        return [TextContent(type="text", text=json.dumps(weather_data))]
    raise ValueError(f"Unknown tool: {name}")


# 4. 启动 server
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
