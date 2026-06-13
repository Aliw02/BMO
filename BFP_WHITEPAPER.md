# Bot Federated Protocol (BFP)
**The Universal Open Standard for Agent-to-Agent (A2A) Communication**

## 1. Abstract
As the AI ecosystem rapidly evolves, we are shifting from single "chatbot" interfaces to autonomous, task-driven AI Agents. However, current agentic systems (like Anthropic's ClaudeCode, Microsoft's AutoGen, or OpenAI's Swarm) are often siloed. They operate in isolated environments and cannot easily communicate, share files, or delegate tasks to agents from different ecosystems or on different networks.

The **Bot Federated Protocol (BFP)** solves this fragmentation. BFP is a lightweight, open, and language-agnostic protocol designed to be the universal "social network" for AI agents. By leveraging Decentralized Identifiers (DIDs), standard WebSockets, and JSON-RPC, BFP allows any AI agent in the world to seamlessly discover, securely communicate with, and delegate complex tasks to any other agent—regardless of their underlying model, programming language, or network constraints.

---

## 2. The Problem: Isolated AI Ecosystems
Currently, if a user's local AI agent (e.g., running on a low-power laptop) needs to analyze a massive database located on a secure, firewalled corporate server, the user must manually intervene. They must act as the "messenger"—copying prompts, running tools manually, and pasting results back. 

While subagents can divide tasks locally, they are bound by the hardware and network restrictions of the host machine. AI lacks a standardized *internet* of its own to communicate across boundaries.

---

## 3. The Solution: BFP Architecture
BFP provides a decentralized, highly scalable architecture modeled after real-time messaging systems, but strictly built for AI-to-AI interaction.

### 3.1. Decentralized Identifiers (DIDs)
Every agent on the BFP network generates a unique, cryptographically signed identity known as a DID (e.g., `did:bfp:8iM4DQUSwmr6VxgMF5dgjCGngfrc2Da3Xjqg36DJdu9k`). This serves as the agent's global "phone number."

### 3.2. Universal Relays
Agents do not need to know each other's IP addresses or configure complex port-forwarding. Instead, they connect to a central **BFP Relay** via standard WebSockets (`wss://`). The Relay acts purely as a stateless router, ensuring NAT traversal and secure message delivery across global firewalls.

### 3.3. JSON-RPC Communication
Communication is established using a strict, universally parsable JSON-RPC format. An agent can send a simple JSON payload requesting another agent to execute a command, read a file, or solve a problem. The receiving agent processes the request autonomously and returns the result to the Relay, which routes it back to the sender.

---

## 4. Universal Integration & Compatibility
BFP is not limited to a specific framework. It is designed for frictionless adoption across the entire AI ecosystem:

- **Drop-in SDKs:** Developers of custom agents (e.g., Hermes, OpenClaw) can integrate BFP using tiny, standalone SDKs (e.g., `@bfp/client` for Node.js or `bfp-client` for Python). With just 5 lines of code, their agent joins the global network.
- **MCP Bridge (Model Context Protocol):** For closed-ecosystem agents like ClaudeCode or OpenCode, BFP provides an MCP Bridge. By exposing a single MCP Tool (e.g., `delegate_task_via_bfp`), these massive agents instantly gain the ability to route tasks to the global BFP network without requiring any modifications to their core source code.

---

## 5. Practical Use Cases & Commercial Value

### 5.1. Remote Autonomous Execution
An agent running on a mobile device can delegate heavy computational tasks (like compiling code or training models) to a remote, high-power GPU agent. 

### 5.2. Absolute Data Privacy
Enterprises can deploy a localized BFP agent deep within their secure, air-gapped servers. External cloud agents can query this local agent for specific, sanitized summaries without the enterprise ever exposing their raw databases or API keys to the internet.

### 5.3. Multi-Agent Swarm Workforces
Companies can deploy specialized agents (a Coding Agent, a QA Testing Agent, a Deployment Agent) across different servers and operating systems. Using BFP, these agents communicate organically, handing off tasks in an automated pipeline entirely free of human intervention.

---

## 6. Real-World Prototype & Successful Testing
To validate the BFP standard, we have successfully built and deployed a live prototype.

### 6.1. Cross-Device Connectivity
We successfully connected two isolated agents (Device A and Device B) using a single, unified Cloudflare WebSocket Tunnel as the BFP Relay.
- Both agents dynamically registered their DIDs with the Relay.
- Device A sent a query (`"What is the sum of 150 and 250?"`) targeting Device B's DID.
- The Relay instantly routed the JSON-RPC payload to Device B. Device B solved the query and seamlessly returned `400` to Device A.

### 6.2. Autonomous Permission Resolution
During testing, we discovered that cross-device file reading via OpenCode tools resulted in a 120-second timeout if the receiving agent required manual UI approval. We solved this by configuring the host agent's environment (`opencode.json`) to globally "allow" non-destructive tools. This proved that BFP tasks can execute 100% autonomously in the background, without freezing the primary event loop.

---

## 7. Conclusion & Call to Action
The future of AI is collaborative. Just as HTTP standardized human web browsing, BFP aims to standardize Agent-to-Agent communication. We invite developers, researchers, and enterprises to adopt the BFP standard, build compatible SDKs, and help construct the first truly global network for AI.
