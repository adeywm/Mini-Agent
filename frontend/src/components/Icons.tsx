import { Wrench } from "lucide-react";
import { FaAws, FaGithub } from "react-icons/fa6";

/** Brand logos from Font Awesome Free (CC BY 4.0, via react-icons); Lucide for the rest. */
export function ProviderIcon({ kind, size = 14 }: { kind?: "aws" | "github"; size?: number }) {
  if (kind === "github") return <FaGithub size={size} aria-label="GitHub" />;
  if (kind === "aws") return <FaAws size={size + 2} aria-label="AWS" />;
  return <Wrench size={size} aria-hidden />;
}
