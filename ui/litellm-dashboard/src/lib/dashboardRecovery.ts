export const MODULE_ERROR_EVENT = "litellm:module-load-error";

export function isModuleLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return /ChunkLoadError|Loading (?:CSS )?chunk .+failed|Failed to (?:fetch dynamically imported module|load chunk)|Importing a module script failed|error loading dynamically imported module/i.test(
    message,
  );
}

export function reportModuleLoadError(error: unknown) {
  if (isModuleLoadError(error)) window.dispatchEvent(new Event(MODULE_ERROR_EVENT));
}
