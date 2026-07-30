const messages = {
  "app.title": "Application",
  "app.subtitle": "FastAPI backend, Next.js frontend.",
  "health.loading": "Checking the API…",
  "health.error": "The API could not be reached.",
  "health.ok": "API is healthy",
  "health.version": "Version",
  "health.environment": "Environment",
} as const;

export type MessageKey = keyof typeof messages;

/** Every user-visible string goes through here — never hardcode text in a component. */
export function t(key: MessageKey): string {
  return messages[key];
}
