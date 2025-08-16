import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import axios from "axios";
import { Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { withPaymentInterceptor } from "x402-axios";
import { config } from "dotenv";

config();

const privateKey = process.env.PRIVATE_KEY as Hex;
const baseURL = process.env.RESOURCE_SERVER_URL as string; // e.g., https://YOUR_PROJECT_ID.uc.r.appspot.com
const endpointPath = process.env.ENDPOINT_PATH as string;  // e.g., /x402/openai or /x402/{provider_name}

if (!privateKey || !baseURL || !endpointPath) {
  throw new Error("Missing environment variables: PRIVATE_KEY, RESOURCE_SERVER_URL, ENDPOINT_PATH");
}

const account = privateKeyToAccount(privateKey);
const client = withPaymentInterceptor(axios.create({ baseURL }), account);

const server = new McpServer({
  name: "x402 MCP",
  version: "1.0.0",
});

server.tool(
  "paid-endpoint",
  "Calls the paid resource using x402 (update description to match your endpoint)",
  {},
  async () => {
    // If you set ENDPOINT_PATH to `/x402/{provider_name}` at deploy time,
    // replace `{provider_name}` here or pass it via env var for exact provider.
    const res = await client.get(endpointPath);
    return {
      content: [{ type: "text", text: JSON.stringify(res.data) }],
    };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);

