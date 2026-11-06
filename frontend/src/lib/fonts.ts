/**
 * Font files to fetch alongside the stylesheet instead of after it, as <link rel="preload"> entries for a route's
 * head. A preload is only for text on the first screen: every file listed here is downloaded on every visit to that
 * route, so a file the screen does not show would only slow it down.
 */
import frauncesItalic from "@fontsource-variable/fraunces/files/fraunces-latin-opsz-italic.woff2?url";
import fraunces from "@fontsource-variable/fraunces/files/fraunces-latin-opsz-normal.woff2?url";
import geistMono from "@fontsource-variable/geist-mono/files/geist-mono-latin-wght-normal.woff2?url";
import geistExtended from "@fontsource-variable/geist/files/geist-latin-ext-wght-normal.woff2?url";
import geist from "@fontsource-variable/geist/files/geist-latin-wght-normal.woff2?url";

/** Fonts are always fetched in anonymous mode, so a preload without crossOrigin would download the file twice. */
const preload = (href: string) => ({
  rel: "preload",
  href,
  as: "font",
  type: "font/woff2",
  crossOrigin: "anonymous" as const,
});

/** Every page: the header and body text, the serif heading and the small labels. */
export const SHELL_FONTS = [geist, fraunces, geistMono].map(preload);

/** Pages with the request box, whose example text shows ₹. */
export const RUPEE_FONTS = [geistExtended].map(preload);

/** The home page heading ends in italic: "with evidence." */
export const HERO_FONTS = [frauncesItalic].map(preload);
