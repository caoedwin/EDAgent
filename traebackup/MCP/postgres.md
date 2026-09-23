{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://agent:${PG_PASSWORD}@localhost:5432/ai_agent"
      ]
    }
  }
}