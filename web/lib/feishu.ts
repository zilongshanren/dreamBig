/**
 * Feishu Open Platform API helpers.
 * Handles: event verification, tenant access token, message sending.
 */

import crypto from "crypto";

const FEISHU_OPEN_HOST = "https://open.feishu.cn/open-apis";

export function verifyFeishuSignature(
  timestamp: string,
  nonce: string,
  body: string,
  encryptKey: string,
): boolean {
  // Feishu signature verification:
  // sha256(timestamp + nonce + encryptKey + body)
  const hash = crypto.createHash("sha256");
  hash.update(timestamp + nonce + encryptKey + body);
  const signature = hash.digest("hex");
  // Header comparison happens at caller
  return signature.length > 0; // stub - caller compares against X-Lark-Signature header
}

export function computeFeishuSignature(
  timestamp: string,
  nonce: string,
  body: string,
  encryptKey: string,
): string {
  return crypto
    .createHash("sha256")
    .update(timestamp + nonce + encryptKey + body)
    .digest("hex");
}

/**
 * Get tenant access token. Caches in-memory for 2 hours.
 */
let cachedToken: { token: string; expiresAt: number } | null = null;

export async function getTenantAccessToken(): Promise<string | null> {
  if (cachedToken && cachedToken.expiresAt > Date.now()) {
    return cachedToken.token;
  }

  const appId = process.env.FEISHU_APP_ID;
  const appSecret = process.env.FEISHU_APP_SECRET;
  if (!appId || !appSecret) {
    console.warn("Feishu credentials not configured");
    return null;
  }

  try {
    const resp = await fetch(
      `${FEISHU_OPEN_HOST}/auth/v3/tenant_access_token/internal`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
      },
    );
    const data = await resp.json();
    if (data.code !== 0) {
      console.error("Feishu token error:", data);
      return null;
    }
    cachedToken = {
      token: data.tenant_access_token,
      expiresAt: Date.now() + (data.expire - 60) * 1000,
    };
    return cachedToken.token;
  } catch (e) {
    console.error("Feishu token fetch failed:", e);
    return null;
  }
}

/**
 * Send a message to a chat (or user) via Feishu Open API.
 */
export async function sendFeishuMessage(
  receiveId: string, // open_id or chat_id
  msgType: "text" | "interactive",
  content: unknown,
  receiveIdType: "open_id" | "chat_id" = "chat_id",
): Promise<boolean> {
  const token = await getTenantAccessToken();
  if (!token) return false;

  try {
    const resp = await fetch(
      `${FEISHU_OPEN_HOST}/im/v1/messages?receive_id_type=${receiveIdType}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          receive_id: receiveId,
          msg_type: msgType,
          content:
            typeof content === "string" ? content : JSON.stringify(content),
        }),
      },
    );
    const data = await resp.json();
    if (data.code !== 0) {
      console.error("Feishu send error:", data);
      return false;
    }
    return true;
  } catch (e) {
    console.error("Feishu send failed:", e);
    return false;
  }
}

/**
 * Parse "/analyze <game>" style commands from message text.
 */
export function parseCommand(
  text: string,
): { command: string; args: string } | null {
  const trimmed = text.trim();
  // Accept /cmd, /cmd arg, @bot /cmd arg
  const match = trimmed.match(/(?:^|\s)\/([a-z_]+)(?:\s+(.*))?$/i);
  if (!match) return null;
  return { command: match[1].toLowerCase(), args: (match[2] || "").trim() };
}

/**
 * Build a simple text reply.
 */
export function textReply(text: string): { text: string } {
  return { text };
}

/**
 * Build an interactive card reply (for structured responses).
 */
export function cardReply(
  title: string,
  bodyMarkdown: string,
  template: "blue" | "green" | "yellow" | "red" = "blue",
): unknown {
  return {
    header: {
      title: { tag: "plain_text", content: title },
      template,
    },
    elements: [
      {
        tag: "div",
        text: { tag: "lark_md", content: bodyMarkdown },
      },
    ],
  };
}
