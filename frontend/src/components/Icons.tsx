import { Cloud, Github, Wrench } from "lucide-react";

export function ProviderIcon({ kind, size = 14 }: { kind?: "aws" | "github"; size?: number }) {
  if (kind === "github") return <Github size={size} aria-label="GitHub" />;
  if (kind === "aws") return <Cloud size={size} aria-label="AWS" />;
  return <Wrench size={size} aria-hidden />;
}
