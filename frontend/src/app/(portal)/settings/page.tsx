"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/shared/page-header";
import { ProviderConfigCard } from "@/components/settings/provider-config-card";
import { EmbeddingSettingsCard } from "@/components/settings/embedding-settings-card";

export default function SettingsPage() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user && user.role !== "admin") router.replace("/");
  }, [user, router]);

  if (user && user.role !== "admin") return null;

  return (
    <>
      <PageHeader
        title="Settings"
        description="Configure AI providers for embedding, LLM, vision processing, and chatbot."
      />

      <div className="flex flex-col gap-6">
        <EmbeddingSettingsCard />

        <ProviderConfigCard
          title="LLM Provider"
          description="Used for wiki compilation, analysis and summarization"
          icon="psychology"
          capability="llm"
          testEndpoint="/api/settings/test-llm"
        />

        <ProviderConfigCard
          title="Vision Provider"
          description="Optional — used for image analysis in documents"
          icon="visibility"
          capability="vision"
          testEndpoint="/api/settings/test-vision"
        />

        <ProviderConfigCard
          title="Chatbot Provider"
          description="Optional — dedicated model for the RAG knowledge assistant"
          icon="smart_toy"
          capability="chatbot"
          testEndpoint="/api/settings/test-chatbot"
          fallbackNote="Optional — falls back to LLM Provider"
        />
      </div>
    </>
  );
}
