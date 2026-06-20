"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";


export function Header() {
  const { user, logout } = useAuth();
  const router = useRouter();

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <header className="sticky top-0 z-30 bg-background/95 backdrop-blur-sm border-b border-border h-14 flex items-center justify-between px-6">
        <div className="flex items-center gap-2.5">
          <Image src="/arkon-icon-v2.png" alt="Arkon" width={28} height={28} className="rounded-md" />
          <div className="hidden sm:block">
            <p className="font-heading text-base font-semibold leading-none text-foreground">Arkon <span className="font-mono text-[9px] text-primary">v2</span></p>
            <p className="font-mono text-[8px] uppercase tracking-[0.18em] text-primary">secure node</p>
          </div>
        </div>

        {/* Right */}
        <div className="flex items-center gap-3">
          <DropdownMenu>
            <DropdownMenuTrigger className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-secondary transition-colors cursor-pointer">
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold">
                {user?.name?.charAt(0).toUpperCase() || "?"}
              </div>
              <span className="text-sm font-medium hidden sm:inline">{user?.name}</span>
              <span className="material-symbols-outlined text-muted-foreground text-base">
                expand_more
              </span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={() => router.push("/profile")}>
                <span className="material-symbols-outlined mr-2 text-base">person</span>
                Profile
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                <span className="material-symbols-outlined mr-2 text-base">logout</span>
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
    </header>
  );
}
