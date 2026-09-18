import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatINR(value: number): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
}

export function formatKg(value: number, decimals = 1): string {
  return `${value.toFixed(decimals)} kg`;
}

export function formatDays(value: number, decimals = 1): string {
  return `${value.toFixed(decimals)}d`;
}

export function formatPct(value: number, decimals = 0): string {
  return `${(value * 100).toFixed(decimals)}%`;
}
