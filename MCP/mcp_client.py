"""
MCP 入门第2步：客户端调用 MCP Server
通过 stdio 启动 server 进程并调用它的工具。

跑法（在另一个终端）：python demo/02_mcp_client.py
"""

import asyncio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession


async def main():
    # 1. 启动 server 进程并建立连接
    server_params = StdioServerParameters(
        command="python",
        args=["MCP/mcp_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 2. 握手
            await session.initialize()
            print("已连接 MCP Server\n")

            # 3. 看有哪些工具
            tools = await session.list_tools()
            print(f"可用工具: {[t.name for t in tools.tools]}")

            # 4. 调用工具！
            result = await session.call_tool("get_weather", {"latitude": "39.9087", "longitude": "116.4713"})
            print(f"调用结果: {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
