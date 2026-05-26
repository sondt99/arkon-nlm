"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AddToWikiDialog } from "./add-to-wiki-dialog";

type Conversation = {
  id: string;
  title: string;
  scope_type: string;
  scope_id: string | null;
  created_at: string;
  updated_at: string;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Array<{ slug: string; title: string }> | null;
  created_at: string;
};

// ---------------------------------------------------------------------------
// Markdown renderer — uses react-markdown + remark-gfm
// ---------------------------------------------------------------------------
function ChatMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => (
          <h1 className="text-base font-bold mt-3 mb-1.5 text-foreground">{children}</h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-sm font-bold mt-3 mb-1 text-foreground">{children}</h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-sm font-semibold mt-2 mb-0.5 text-foreground">{children}</h3>
        ),
        p: ({ children }) => (
          <p className="text-sm leading-relaxed mb-2 last:mb-0">{children}</p>
        ),
        ul: ({ children }) => (
          <ul className="text-sm leading-relaxed mb-2 ml-4 list-disc space-y-0.5">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="text-sm leading-relaxed mb-2 ml-4 list-decimal space-y-0.5">{children}</ol>
        ),
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline underline-offset-2 hover:text-primary/80"
          >
            {children}
          </a>
        ),
        code: ({ className, children, ...props }) => {
          const isBlock = !!className;
          if (isBlock) {
            return (
              <div className="relative group/code my-2">
                <pre className="bg-muted/60 border border-border rounded-lg px-4 py-3 overflow-x-auto">
                  <code className="text-xs font-mono text-foreground whitespace-pre">
                    {children}
                  </code>
                </pre>
              </div>
            );
          }
          return (
            <code
              className="bg-muted border border-border/50 px-1.5 py-0.5 rounded text-xs font-mono text-foreground"
              {...props}
            >
              {children}
            </code>
          );
        },
        pre: ({ children }) => <>{children}</>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-primary/40 pl-3 my-2 text-muted-foreground italic text-sm">
            {children}
          </blockquote>
        ),
        hr: () => <hr className="my-3 border-border" />,
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="text-xs w-full border-collapse border border-border rounded">
              {children}
            </table>
          </div>
        ),
        thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
        th: ({ children }) => (
          <th className="border border-border px-2 py-1.5 text-left font-semibold text-foreground">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border border-border px-2 py-1.5 text-muted-foreground">{children}</td>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------
function MessageBubble({
  msg,
  isEditing,
  editContent,
  onEditStart,
  onEditChange,
  onEditSave,
  onEditCancel,
}: {
  msg: Message;
  isEditing?: boolean;
  editContent?: string;
  onEditStart?: () => void;
  onEditChange?: (val: string) => void;
  onEditSave?: () => void;
  onEditCancel?: () => void;
}) {
  const isUser = msg.role === "user";
  const editRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (isEditing && editRef.current) {
      editRef.current.focus();
      const len = editRef.current.value.length;
      editRef.current.setSelectionRange(len, len);
    }
  }, [isEditing]);

  return (
    <div className={cn("flex gap-3 max-w-3xl", isUser ? "ml-auto flex-row-reverse" : "")}>
      {/* Avatar */}
      <div
        className={cn(
          "w-7 h-7 shrink-0 rounded-full flex items-center justify-center text-xs font-semibold mt-1",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground border border-border"
        )}
      >
        {isUser ? (
          <span className="material-symbols-outlined text-sm">person</span>
        ) : (
          <span className="material-symbols-outlined text-sm">smart_toy</span>
        )}
      </div>

      <div className={cn("flex flex-col gap-1 min-w-0", isUser ? "items-end" : "")}>
        {isEditing ? (
          /* ── Inline edit mode ── */
          <div className="flex flex-col gap-2 w-full max-w-prose">
            <textarea
              ref={editRef}
              value={editContent}
              onChange={(e) => {
                onEditChange?.(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 300)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onEditSave?.(); }
                if (e.key === "Escape") onEditCancel?.();
              }}
              rows={3}
              className="w-full resize-none rounded-2xl rounded-tr-sm px-4 py-3 text-sm bg-primary/5 border border-primary/40 outline-none focus:border-primary/70 text-foreground leading-relaxed"
              style={{ maxHeight: 300 }}
            />
            <div className="flex gap-2 justify-end text-xs">
              <button
                onClick={onEditCancel}
                className="px-3 py-1.5 rounded-lg border border-border text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onEditSave}
                disabled={!editContent?.trim()}
                className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground font-medium hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 13 }}>refresh</span>
                Save & Regenerate
              </button>
            </div>
          </div>
        ) : (
          /* ── Normal display ── */
          <div className={cn("group/msg relative", isUser ? "flex flex-col items-end" : "")}>
            {/* Edit icon — only for user messages */}
            {isUser && onEditStart && (
              <button
                onClick={onEditStart}
                className="absolute -left-6 top-2.5 opacity-0 group-hover/msg:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                title="Edit message"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>edit</span>
              </button>
            )}
            <div
              className={cn(
                "rounded-2xl px-4 py-3 max-w-prose",
                isUser
                  ? "bg-primary text-primary-foreground rounded-tr-sm"
                  : "bg-card border border-border rounded-tl-sm"
              )}
            >
              {isUser ? (
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
              ) : (
                <div className="min-w-0">
                  <ChatMarkdown content={msg.content} />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Sources */}
        {!isEditing && !isUser && msg.sources && msg.sources.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {msg.sources.map((s) => (
              <a
                key={s.slug}
                href={`/wiki/${s.slug}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary/5 border border-primary/20 text-xs text-primary hover:bg-primary/10 transition-colors"
              >
                <span className="material-symbols-outlined text-xs" style={{ fontSize: 11 }}>
                  auto_stories
                </span>
                {s.title}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function ChatPage() {
  const [conversations, setConversations] = React.useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [loadingConvs, setLoadingConvs] = React.useState(true);
  const [loadingMsgs, setLoadingMsgs] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const [input, setInput] = React.useState("");
  const [deletingId, setDeletingId] = React.useState<string | null>(null);
  const [editingConvId, setEditingConvId] = React.useState<string | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const [editingMsgId, setEditingMsgId] = React.useState<string | null>(null);
  const [editingMsgContent, setEditingMsgContent] = React.useState("");
  const [wikiDialogOpen, setWikiDialogOpen] = React.useState(false);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const editInputRef = React.useRef<HTMLInputElement>(null);

  const loadConversations = React.useCallback(() => {
    api<Conversation[]>("/api/chat/conversations")
      .then((data) => setConversations(Array.isArray(data) ? data : []))
      .catch(() => setConversations([]))
      .finally(() => setLoadingConvs(false));
  }, []);

  React.useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const loadMessages = React.useCallback((convId: string) => {
    setLoadingMsgs(true);
    api<Message[]>(`/api/chat/conversations/${convId}/messages`)
      .then((data) => setMessages(Array.isArray(data) ? data : []))
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false));
  }, []);

  React.useEffect(() => {
    if (activeConvId) loadMessages(activeConvId);
    else setMessages([]);
  }, [activeConvId, loadMessages]);

  // Scroll to bottom when messages change
  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const handleNewConversation = async () => {
    try {
      const conv = await api<Conversation>("/api/chat/conversations", {
        method: "POST",
        body: { title: "New conversation" },
      });
      setConversations((prev) => [conv, ...prev]);
      setActiveConvId(conv.id);
      setMessages([]);
    } catch {}
  };

  const handleDelete = async (id: string) => {
    if (deletingId !== id) {
      setDeletingId(id);
      return;
    }
    try {
      await api(`/api/chat/conversations/${id}`, { method: "DELETE" });
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConvId === id) {
        setActiveConvId(null);
        setMessages([]);
      }
    } catch {}
    setDeletingId(null);
  };

  const handleStartEdit = (conv: Conversation) => {
    setDeletingId(null);
    setEditingConvId(conv.id);
    setEditingTitle(conv.title);
  };

  const handleSaveTitle = async (id: string) => {
    const title = editingTitle.trim();
    setEditingConvId(null);
    if (!title) return;
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await api(`/api/chat/conversations/${id}`, { method: "PATCH", body: { title } });
    } catch {
      loadConversations(); // revert on error
    }
  };

  // Auto-focus edit input when entering edit mode
  React.useEffect(() => {
    if (editingConvId) {
      requestAnimationFrame(() => {
        editInputRef.current?.focus();
        editInputRef.current?.select();
      });
    }
  }, [editingConvId]);

  const handleEditMsgSave = async () => {
    const text = editingMsgContent.trim();
    if (!text || sending || !activeConvId) return;

    const msgId = editingMsgId!;
    const msgIndex = messages.findIndex((m) => m.id === msgId);
    setEditingMsgId(null);
    setSending(true);

    // Optimistic: update the edited message, remove everything after it
    setMessages((prev) => [
      ...prev.slice(0, msgIndex),
      { ...prev[msgIndex], content: text },
    ]);

    try {
      const result = await api<{ user_message: Message; assistant_message: Message }>(
        `/api/chat/conversations/${activeConvId}/messages/${msgId}/edit`,
        { method: "PATCH", body: { content: text }, timeoutMs: 120_000 }
      );
      setMessages((prev) => [
        ...prev.slice(0, msgIndex),
        result.user_message,
        result.assistant_message,
      ]);
    } catch {
      // Rollback: reload from server
      loadMessages(activeConvId);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  };

  // Dismiss delete arm on outside click
  React.useEffect(() => {
    if (!deletingId) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(`[data-conv-id="${deletingId}"]`)) {
        setDeletingId(null);
      }
    };
    document.addEventListener("click", handler, true);
    return () => document.removeEventListener("click", handler, true);
  }, [deletingId]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;

    let convId = activeConvId;
    if (!convId) {
      try {
        const conv = await api<Conversation>("/api/chat/conversations", {
          method: "POST",
          body: { title: "New conversation" },
        });
        convId = conv.id;
        setConversations((prev) => [conv, ...prev]);
        setActiveConvId(conv.id);
      } catch {
        return;
      }
    }

    setInput("");
    setSending(true);

    // Optimistic user message
    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      role: "user",
      content: text,
      sources: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      const result = await api<{ user_message: Message; assistant_message: Message }>(
        `/api/chat/conversations/${convId}/messages`,
        { method: "POST", body: { content: text }, timeoutMs: 120_000 }
      );

      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tempUserMsg.id),
        result.user_message,
        result.assistant_message,
      ]);

      // Update conversation title in sidebar
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId
            ? { ...c, title: text.slice(0, 80), updated_at: new Date().toISOString() }
            : c
        )
      );
    } catch {
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tempUserMsg.id),
        tempUserMsg,
        {
          id: `err-${Date.now()}`,
          role: "assistant",
          content: "Sorry, something went wrong. Please try again.",
          sources: null,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const activeConv = conversations.find((c) => c.id === activeConvId);

  return (
    <div className="flex flex-col flex-1 min-h-0 -mx-6 md:-mx-8 lg:-mx-10 -mb-6 md:-mb-8 lg:-mb-10 border-t border-border">
      <div className="flex flex-1 min-h-0">
        {/* ── Conversation sidebar ── */}
        <div className="w-64 shrink-0 border-r border-border bg-card/30 flex flex-col overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex-1">
              Conversations
            </span>
            <button
              onClick={handleNewConversation}
              className="text-muted-foreground hover:text-foreground transition-colors"
              title="New conversation"
            >
              <span className="material-symbols-outlined text-base">edit_square</span>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto py-2">
            {loadingConvs ? (
              <div className="px-3 space-y-2 mt-1">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-8 rounded-md bg-muted animate-pulse" />
                ))}
              </div>
            ) : conversations.length === 0 ? (
              <div className="px-4 py-6 text-center">
                <span className="material-symbols-outlined text-3xl text-muted-foreground/30 block mb-2">
                  chat_bubble_outline
                </span>
                <p className="text-xs text-muted-foreground">No conversations yet</p>
                <button
                  onClick={handleNewConversation}
                  className="mt-3 text-xs text-primary hover:underline"
                >
                  Start one
                </button>
              </div>
            ) : (
              <div className="px-1">
                {conversations.map((conv) => {
                  const isActive = conv.id === activeConvId;
                  const isArmed = deletingId === conv.id;
                  const isEditing = editingConvId === conv.id;
                  return (
                    <div
                      key={conv.id}
                      data-conv-id={conv.id}
                      className={cn(
                        "group flex items-center gap-1 rounded-lg mx-1 mb-0.5 transition-all",
                        isActive ? "bg-primary/10" : "hover:bg-accent/50"
                      )}
                    >
                      {isEditing ? (
                        /* ── Inline title editor ── */
                        <input
                          ref={editInputRef}
                          value={editingTitle}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") { e.preventDefault(); handleSaveTitle(conv.id); }
                            if (e.key === "Escape") setEditingConvId(null);
                          }}
                          onBlur={() => handleSaveTitle(conv.id)}
                          maxLength={500}
                          className="flex-1 mx-2 my-1 px-2 py-1 text-xs bg-background border border-primary/50 rounded-md outline-none text-foreground min-w-0"
                        />
                      ) : (
                        <button
                          onClick={() => setActiveConvId(conv.id)}
                          onDoubleClick={() => handleStartEdit(conv)}
                          className={cn(
                            "flex-1 flex items-center gap-2 px-2 py-2 text-xs min-w-0 text-left",
                            isActive ? "text-primary font-medium" : "text-muted-foreground hover:text-foreground"
                          )}
                        >
                          <span className="material-symbols-outlined shrink-0" style={{ fontSize: 14 }}>
                            {isActive ? "chat_bubble" : "chat_bubble_outline"}
                          </span>
                          <span className="truncate">{conv.title}</span>
                        </button>
                      )}

                      {!isEditing && (
                        isArmed ? (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDelete(conv.id); }}
                            className="shrink-0 mr-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-destructive text-destructive-foreground hover:bg-destructive/90 animate-pulse"
                          >
                            Confirm
                          </button>
                        ) : (
                          <div className="shrink-0 flex items-center mr-1 opacity-0 group-hover:opacity-100 transition-all">
                            <button
                              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleStartEdit(conv); }}
                              className="text-muted-foreground hover:text-foreground transition-colors"
                              title="Rename"
                            >
                              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>edit</span>
                            </button>
                            <button
                              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(conv.id); }}
                              className="text-muted-foreground hover:text-destructive transition-colors"
                              title="Delete"
                            >
                              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>delete</span>
                            </button>
                          </div>
                        )
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ── Chat area ── */}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Header */}
          <div className="flex items-center gap-3 px-6 py-3 border-b border-border shrink-0">
            <span className="material-symbols-outlined text-primary" style={{ fontSize: 20 }}>
              smart_toy
            </span>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-sm font-semibold truncate">
                {activeConv ? activeConv.title : "Victor"}
              </span>
              <span className="text-xs text-muted-foreground">
                Ask Victor anything about the knowledge base
              </span>
            </div>
            {/* Add to Wiki button — only when a conversation with messages exists */}
            {activeConvId && messages.length > 0 && (
              <button
                onClick={() => setWikiDialogOpen(true)}
                className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-muted-foreground hover:border-primary/40 hover:text-primary hover:bg-primary/5 transition-colors"
                title="Save this conversation to the Knowledge Wiki"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>auto_stories</span>
                Add to Wiki
              </button>
            )}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-6 py-6 min-h-0">
            {!activeConvId ? (
              <div className="h-full flex flex-col items-center justify-center gap-4 text-center">
                <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
                  <span className="material-symbols-outlined text-primary text-3xl">smart_toy</span>
                </div>
                <div>
                  <h2 className="text-lg font-semibold">Ask Victor anything</h2>
                  <p className="text-sm text-muted-foreground mt-1 max-w-md">
                    Victor answers questions using your organization&apos;s knowledge base wiki.
                    Start a new conversation or select an existing one.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 justify-center mt-2">
                  {[
                    "What entities are documented in the wiki?",
                    "Summarize the key concepts",
                    "What are our main knowledge sources?",
                  ].map((q) => (
                    <button
                      key={q}
                      onClick={async () => {
                        await handleNewConversation();
                        setInput(q);
                      }}
                      className="px-3 py-1.5 rounded-full border border-border text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : loadingMsgs ? (
              <div className="flex items-center justify-center h-32">
                <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">
                  progress_activity
                </span>
              </div>
            ) : messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center gap-2 text-center">
                <span className="material-symbols-outlined text-3xl text-muted-foreground/30">
                  chat_bubble_outline
                </span>
                <p className="text-sm text-muted-foreground">No messages yet. Say something!</p>
              </div>
            ) : (
              <div className="flex flex-col gap-6">
                {messages.map((msg) => (
                  <MessageBubble
                    key={msg.id}
                    msg={msg}
                    isEditing={editingMsgId === msg.id}
                    editContent={editingMsgContent}
                    onEditStart={
                      msg.role === "user"
                        ? () => {
                            setEditingMsgId(msg.id);
                            setEditingMsgContent(msg.content);
                          }
                        : undefined
                    }
                    onEditChange={setEditingMsgContent}
                    onEditSave={handleEditMsgSave}
                    onEditCancel={() => setEditingMsgId(null)}
                  />
                ))}
                {sending && (
                  <div className="flex gap-3 max-w-3xl">
                    <div className="w-7 h-7 shrink-0 rounded-full flex items-center justify-center bg-muted border border-border">
                      <span className="material-symbols-outlined text-sm">smart_toy</span>
                    </div>
                    <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-card border border-border">
                      <div className="flex gap-1 items-center h-5">
                        <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce" style={{ animationDelay: "0ms" }} />
                        <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce" style={{ animationDelay: "150ms" }} />
                        <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce" style={{ animationDelay: "300ms" }} />
                      </div>
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          {/* Input */}
          <div className="shrink-0 border-t border-border px-6 py-4">
            <div className="flex items-end gap-3 bg-background border border-border rounded-2xl px-4 py-3 focus-within:border-primary/50 transition-colors">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  // Auto-resize
                  e.target.style.height = "auto";
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
                }}
                onKeyDown={handleKeyDown}
                placeholder="Ask a question about the knowledge base… (Enter to send, Shift+Enter for newline)"
                rows={1}
                className="flex-1 text-sm bg-transparent outline-none resize-none text-foreground placeholder:text-muted-foreground/60 leading-relaxed"
                style={{ maxHeight: 160 }}
                disabled={sending}
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || sending}
                className="shrink-0 w-8 h-8 rounded-xl bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                {sending ? (
                  <span className="material-symbols-outlined text-base animate-spin">progress_activity</span>
                ) : (
                  <span className="material-symbols-outlined text-base">arrow_upward</span>
                )}
              </button>
            </div>
            <p className="text-[11px] text-muted-foreground/50 mt-2 text-center">
              Victor&apos;s answers are grounded in your organization&apos;s wiki. Always verify important information.
            </p>
          </div>
        </div>
      </div>

      {/* Add to Wiki dialog */}
      {wikiDialogOpen && activeConvId && (
        <AddToWikiDialog
          conversationId={activeConvId}
          defaultTitle={activeConv?.title ?? "Chat Synthesis"}
          onClose={() => setWikiDialogOpen(false)}
        />
      )}
    </div>
  );
}
