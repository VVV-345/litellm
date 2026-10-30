export const MODULE_ERROR_EVENT = "litellm:module-load-error";
export const CHUNK_RELOAD_LIMIT = 2;
const CHUNK_RELOAD_KEY = "litellm-ui-chunk-reloads";

export function chunkReloadCount(): number {
  if (typeof sessionStorage === "undefined") return 0;
  const count = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY));
  return Number.isInteger(count) && count > 0 ? count : 0;
}

export function canChunkReload(): boolean {
  return chunkReloadCount() < CHUNK_RELOAD_LIMIT;
}

export function takeChunkReload(): boolean {
  if (!canChunkReload() || typeof sessionStorage === "undefined") return false;
  sessionStorage.setItem(CHUNK_RELOAD_KEY, String(chunkReloadCount() + 1));
  return true;
}

export function isModuleLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return /ChunkLoadError|Loading (?:CSS )?chunk .+failed|Failed to (?:fetch dynamically imported module|load chunk)|Importing a module script failed|error loading dynamically imported module/i.test(
    message,
  );
}

export function reportModuleLoadError(error: unknown) {
  if (isModuleLoadError(error)) window.dispatchEvent(new Event(MODULE_ERROR_EVENT));
}
