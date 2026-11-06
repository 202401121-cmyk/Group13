/**
 * A web search for a product and the criterion ShopSense couldn't check, so the shopper can look it up. Only
 * those two names go in the address, never anything the shopper typed (no request or answer in a URL).
 */
export function researchLink(product: string, criterion?: string | null): string {
  const text = [product, criterion].filter(Boolean).join(" ");
  return `https://www.google.com/search?q=${encodeURIComponent(text)}`;
}
