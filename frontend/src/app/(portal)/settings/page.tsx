"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/shared/page-header";
import { Accordion, AccordionItem, AccordionTrigger, AccordionPanel } from "@/components/ui/accordion";
import { ProviderConfigCard } from "@/components/settings/provider-config-card";
import { EmbeddingSettingsCard } from "@/components/settings/embedding-settings-card";
import { ChatSettingsCard } from "@/components/settings/chat-settings-card";
import { ExportApiSettingsCard } from "@/components/settings/export-api-settings-card";

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

      <Accordion defaultValue={["embedding"]} className="flex flex-col gap-4">
        <AccordionItem value="embedding">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">data_array</span>}
            label="Embedding Model"
            description="Converts documents and queries into vectors for semantic search"
          />
          <AccordionPanel>
            <EmbeddingSettingsCard />
          </AccordionPanel>
        </AccordionItem>

        <AccordionItem value="llm">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">psychology</span>}
            iconClassName="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            label="LLM Provider"
            description="Used for wiki compilation, analysis and summarization"
          />
          <AccordionPanel>
            <ProviderConfigCard capability="llm" testEndpoint="/api/settings/test-llm" />
          </AccordionPanel>
        </AccordionItem>

        <AccordionItem value="vision">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">visibility</span>}
            iconClassName="bg-violet-500/10 text-violet-600 dark:text-violet-400"
            label="Vision Provider"
            description="Optional — used for image analysis in documents"
          />
          <AccordionPanel>
            <ProviderConfigCard capability="vision" testEndpoint="/api/settings/test-vision" />
          </AccordionPanel>
        </AccordionItem>

        <AccordionItem value="chatbot">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">smart_toy</span>}
            iconClassName="bg-cyan-500/10 text-cyan-600 dark:text-cyan-400"
            label={
              <span className="flex items-center gap-2">
                Chatbot Provider
                <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">
                  Optional — falls back to LLM Provider
                </span>
              </span>
            }
            description="Optional — dedicated model for the RAG knowledge assistant"
          />
          <AccordionPanel>
            <ProviderConfigCard capability="chatbot" testEndpoint="/api/settings/test-chatbot" />
          </AccordionPanel>
        </AccordionItem>

        <AccordionItem value="chat-behavior">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">manage_search</span>}
            iconClassName="bg-amber-500/10 text-amber-600 dark:text-amber-400"
            label="Chat Settings"
            description="Configure chatbot behaviour"
          />
          <AccordionPanel>
            <ChatSettingsCard />
          </AccordionPanel>
        </AccordionItem>

        <AccordionItem value="export-api">
          <AccordionTrigger
            icon={<span className="material-symbols-outlined">webhook</span>}
            iconClassName="bg-pink-500/10 text-pink-600 dark:text-pink-400"
            label="Export API"
            description="REST access for external tools (n8n, Zapier, scripts)"
          />
          <AccordionPanel>
            <ExportApiSettingsCard />
          </AccordionPanel>
        </AccordionItem>
      </Accordion>
    </>
  );
}
