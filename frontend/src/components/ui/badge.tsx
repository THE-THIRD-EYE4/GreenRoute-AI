import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-11 font-medium leading-none",
  {
    variants: {
      tone: {
        neutral: "bg-paper text-ink border border-rule",
        moss: "bg-moss-12 text-moss",
        amber: "bg-amber-12 text-amber",
        signal: "bg-signal-12 text-signal",
        steel: "bg-steel-10 text-steel",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export { Badge, badgeVariants };
