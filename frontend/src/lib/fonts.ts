/**
 * Font files to fetch alongside the stylesheet instead of after it, as <link rel="preload"> entries for a route's
 * head. A preload is only for text on the first screen: every file listed here is downloaded on every visit to that
 * route, so a file the screen does not show would only slow it down. These are the small files from
 * scripts/subset_fonts.py, never the packaged ones: on a slow connection a big preload holds up the page's own code.
 */
import frauncesItalic from "@/assets/fonts/fraunces-hero-italic.woff2?url";
import fraunces from "@/assets/fonts/fraunces-core.woff2?url";
import geistMono from "@/assets/fonts/geist-mono-core.woff2?url";
import geistRupee from "@/assets/fonts/geist-rupee.woff2?url";
import geist from "@/assets/fonts/geist-core.woff2?url";

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
export const RUPEE_FONTS = [geistRupee].map(preload);

/** The home page heading ends in italic: "with evidence." */
export const HERO_FONTS = [frauncesItalic].map(preload);
