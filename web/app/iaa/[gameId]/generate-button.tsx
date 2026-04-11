"use client";

import { useState } from "react";

/**
 * Client-side "立即生成" button for the IAA detail page.
 *
 * We POST JSON to /api/iaa/analyze and show a status message inline instead
 * of relying on the API's 303 redirect — that redirect constructs an absolute
 * URL from req.url, which is the internal Docker hostname behind Caddy, so
 * the browser cannot follow it and the page appears unresponsive.
 */
export function GenerateReportButton({ gameId }: { gameId: number }) {
  const [status, setStatus] = useState<
    "idle" | "queueing" | "queued" | "recent" | "error"
  >("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleClick() {
    setStatus("queueing");
    setErrorMsg(null);
    try {
      const res = await fetch("/api/iaa/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gameId }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setErrorMsg(data.error || `HTTP ${res.status}`);
        setStatus("error");
        return;
      }

      const data = await res.json().catch(() => ({}));
      if (data.status === "recent") {
        setStatus("recent");
        // Refresh to show the existing report
        setTimeout(() => {
          window.location.reload();
        }, 800);
        return;
      }

      setStatus("queued");
    } catch (err) {
      console.error(err);
      setErrorMsg("网络错误，请重试");
      setStatus("error");
    }
  }

  if (status === "queued") {
    return (
      <div className="space-y-2">
        <p className="text-sm text-green-700 font-medium">
          ✓ 已加入后台队列
        </p>
        <p className="text-xs text-gray-500">
          Worker 每 5 分钟轮询一次内部任务。生成完成后刷新本页即可看到结果。
          Opus 模型生成一份战报需要 30 秒到 2 分钟。
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="text-xs text-blue-600 hover:text-blue-700 underline"
        >
          立即刷新
        </button>
      </div>
    );
  }

  if (status === "recent") {
    return (
      <p className="text-sm text-blue-600">
        已有最新报告，正在跳转...
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={status === "queueing"}
        className="text-sm bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white rounded px-4 py-2 font-medium"
      >
        {status === "queueing" ? "正在加入队列..." : "立即生成"}
      </button>
      {status === "error" && (
        <p className="text-xs text-red-600">{errorMsg}</p>
      )}
    </div>
  );
}
