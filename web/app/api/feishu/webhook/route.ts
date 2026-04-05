import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { parseCommand, computeFeishuSignature } from "@/lib/feishu";

export const runtime = "nodejs"; // ensure crypto available

const KNOWN_COMMANDS = ["analyze", "iaa", "similar", "trending", "help"];

/**
 * POST /api/feishu/webhook
 *
 * Receives all Feishu events. Handles both:
 *  - URL verification (returns challenge)
 *  - Message events (parses command, inserts FeishuBotCommand row,
 *    queues a worker job via scrape_jobs)
 *
 * The worker loop picks up pending FeishuBotCommand rows, looks up the
 * requested data, calls the Feishu API to send a reply, and updates
 * status to success/failed.
 */
export async function POST(req: NextRequest) {
  const bodyText = await req.text();

  // Parse body
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(bodyText);
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  // URL verification
  if (body.type === "url_verification" || body.challenge) {
    return NextResponse.json({ challenge: body.challenge });
  }

  // Signature verification (if configured)
  const encryptKey = process.env.FEISHU_VERIFICATION_TOKEN;
  const timestamp = req.headers.get("X-Lark-Request-Timestamp");
  const nonce = req.headers.get("X-Lark-Request-Nonce");
  const signature = req.headers.get("X-Lark-Signature");

  if (encryptKey && timestamp && nonce && signature) {
    const expected = computeFeishuSignature(
      timestamp,
      nonce,
      bodyText,
      encryptKey,
    );
    if (expected !== signature) {
      console.warn("Feishu signature mismatch");
      return NextResponse.json(
        { error: "invalid_signature" },
        { status: 401 },
      );
    }
  }

  // Handle message event (Feishu event v2 wraps in event)
  const event =
    (body.event as Record<string, unknown>) ||
    (body as Record<string, unknown>);
  const header = body.header as Record<string, unknown> | undefined;
  const eventType =
    (header?.event_type as string | undefined) ||
    ((body.event as Record<string, unknown>)?.type as string | undefined) ||
    ((body.event as Record<string, unknown>)?.event_type as
      | string
      | undefined);

  if (
    eventType !== "im.message.receive_v1" &&
    eventType !== "message"
  ) {
    // Non-message event, just ack
    return NextResponse.json({ ok: true });
  }

  try {
    const message = (event.message as Record<string, unknown>) || {};
    const messageId =
      (message.message_id as string | undefined) ||
      (event.message_id as string | undefined);
    const chatId =
      (message.chat_id as string | undefined) ||
      (event.chat_id as string | undefined);
    const sender = (event.sender as Record<string, unknown>) || {};
    const senderId = sender.sender_id as Record<string, unknown> | undefined;
    const userOpenId =
      (senderId?.open_id as string | undefined) ||
      (sender.open_id as string | undefined);

    if (!messageId) {
      return NextResponse.json({ ok: true, skipped: "no_message_id" });
    }

    // Parse content (varies by msg_type)
    let text = "";
    const msgType =
      (message.message_type as string | undefined) ||
      (message.msg_type as string | undefined);
    if (msgType === "text") {
      try {
        const parsed =
          typeof message.content === "string"
            ? JSON.parse(message.content)
            : message.content;
        text = (parsed?.text as string) || "";
      } catch {
        text = "";
      }
    }

    // Strip @mentions
    text = text.replace(/@_user_\d+/g, "").replace(/@\S+/g, "").trim();

    const parsed = parseCommand(text);
    if (!parsed || !KNOWN_COMMANDS.includes(parsed.command)) {
      // Unrecognized command — we still ack silently
      return NextResponse.json({ ok: true });
    }

    // Insert FeishuBotCommand (idempotent on message_id)
    const existing = await prisma.feishuBotCommand.findUnique({
      where: { messageId },
    });
    if (existing) {
      return NextResponse.json({ ok: true, duplicate: true });
    }

    await prisma.feishuBotCommand.create({
      data: {
        messageId,
        userOpenId: userOpenId ?? null,
        chatId: chatId ?? null,
        command: parsed.command,
        args: parsed.args,
        status: "pending",
      },
    });

    // Queue worker job via scrape_jobs
    await prisma.scrapeJob.create({
      data: {
        platform: "internal",
        jobType: "feishu_command",
        status: "pending",
        errorMessage: JSON.stringify({ messageId }),
      },
    });

    return NextResponse.json({ ok: true, queued: true });
  } catch (e) {
    console.error("Feishu webhook error:", e);
    return NextResponse.json({ error: "internal" }, { status: 500 });
  }
}
