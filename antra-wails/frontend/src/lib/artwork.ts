const MIN_ARTWORK_SIZE = 32;
const MAX_ARTWORK_SIZE = 1024;

export function normalizeArtworkSize(size: number): number {
  if (!Number.isFinite(size) || size <= 0) return 0;
  return Math.min(MAX_ARTWORK_SIZE, Math.max(MIN_ARTWORK_SIZE, Math.round(size)));
}

export function sizedArtworkUrl(source: string | null | undefined, size: number): string {
  const url = String(source || '');
  const normalizedSize = normalizeArtworkSize(size);
  if (!url || !normalizedSize) return url;

  const withTemplateSize = url
    .replace(/\{w\}/gi, String(normalizedSize))
    .replace(/\{h\}/gi, String(normalizedSize));

  return withTemplateSize.replace(
    /\/\d{2,4}x\d{2,4}((?:bb|cc|mv)?)((?:\.[a-z0-9]+)(?:\?.*)?)$/i,
    `/${normalizedSize}x${normalizedSize}$1$2`,
  );
}
