from fastmcp import FastMCP

arithmetic_mcp = FastMCP(
    "Arithmetic MCP",
    instructions="Built-in arithmetic tools available to every authenticated chat.",
)


@arithmetic_mcp.tool(name="double", description="Multiply one numeric argument by two.")
def double(value: float) -> float:
    return value * 2


@arithmetic_mcp.tool(name="divide", description="Divide numerator by denominator.")
def divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ValueError("denominator must not be zero")
    return numerator / denominator
