"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

interface WorkspaceOption {
  id: string;
  name: string;
  isDefault: boolean;
  role: string;
}

export function WorkspaceSwitcher({
  workspaces,
  currentId,
}: {
  workspaces: WorkspaceOption[];
  currentId: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  const current = workspaces.find((w) => w.id === currentId);

  if (workspaces.length === 0) {
    return (
      <div className="px-3 py-2 text-xs text-gray-400 border-b border-gray-700">
        无工作区
      </div>
    );
  }

  async function selectWorkspace(id: string) {
    if (id === currentId) {
      setOpen(false);
      return;
    }
    setOpen(false);
    startTransition(async () => {
      const res = await fetch("/api/workspaces/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspaceId: id }),
      });
      if (res.ok) {
        router.refresh();
      } else {
        alert("切换失败：无访问权限");
      }
    });
  }

  return (
    <div className="border-b border-gray-700 relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={pending}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-800 transition-colors"
      >
        <div className="min-w-0">
          <p className="text-xs text-gray-400 uppercase tracking-wide">
            工作区
          </p>
          <p className="text-sm font-medium text-white truncate">
            {pending ? "切换中…" : current?.name ?? "选择"}
          </p>
        </div>
        <svg
          className={`w-4 h-4 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-50 bg-gray-800 border-b border-gray-700 shadow-lg">
          {workspaces.map((w) => (
            <button
              key={w.id}
              type="button"
              onClick={() => selectWorkspace(w.id)}
              className={`w-full text-left px-4 py-2 text-sm flex items-center justify-between hover:bg-gray-700 ${
                w.id === currentId ? "bg-gray-700 text-white" : "text-gray-300"
              }`}
            >
              <span className="truncate">{w.name}</span>
              <span className="text-xs text-gray-500 ml-2">{w.role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
