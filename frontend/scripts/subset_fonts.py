"""Make the small font files that the first screen downloads (F-16).

The packaged fonts hold every weight and every Latin letter, 219 KB on the home page, and on a slow phone those bytes
compete with the page's own code. Almost all our text needs a fraction of that: Fraunces is only ever drawn at weight
400, the italic is only "with evidence.", and the rupee sign sits alone in a file of accented letters. The files this
writes hold just that, with the same outlines. src/fonts.css keeps the packaged files as a fallback, so a rare letter
or a heavier weight still gets the real font.

Regenerate after updating a font package or changing the home page's italic text. Run from frontend/ after npm ci:

    pip install fonttools==4.68.0 brotli==1.2.1
    python scripts/subset_fonts.py

The output is committed, so a normal build needs neither Python nor these packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

PACKAGES = Path("node_modules/@fontsource-variable")
OUTPUT = Path("src/assets/fonts")

RUPEE = {0x20B9}
# Keep in step with CORE_RANGE in src/fonts.css. Basic Latin, then the punctuation our text and shop names use.
CORE = set(range(0x20, 0x7F)) | {
    0xA0,  # no-break space
    0xA9,  # ©
    0xB7,  # ·
    0xBD,  # ½
    0x2013,  # –
    0x2014,  # —
    0x2018,  # ‘
    0x2019,  # ’
    0x201C,  # “
    0x201D,  # ”
    0x2022,  # •
    0x2026,  # …
}
HERO_ITALIC = set(map(ord, "with evidence."))


@dataclass(frozen=True)
class Job:
    output: str
    source: str
    characters: set[int]
    weights: int | tuple[int, int]


JOBS = (
    Job("fraunces-core.woff2", "fraunces/files/fraunces-latin-opsz-normal.woff2", CORE, 400),
    Job("fraunces-rupee.woff2", "fraunces/files/fraunces-latin-ext-opsz-normal.woff2", RUPEE, 400),
    Job("fraunces-hero-italic.woff2", "fraunces/files/fraunces-latin-opsz-italic.woff2", HERO_ITALIC, 400),
    Job("geist-core.woff2", "geist/files/geist-latin-wght-normal.woff2", CORE, (400, 600)),
    Job("geist-rupee.woff2", "geist/files/geist-latin-ext-wght-normal.woff2", RUPEE, (400, 600)),
    Job("geist-mono-core.woff2", "geist-mono/files/geist-mono-latin-wght-normal.woff2", CORE, (400, 500)),
)


def make(job: Job) -> int:
    """Cut one file down and return its size. The optical size axis stays, so headings keep their shape at any size."""
    font = TTFont(PACKAGES / job.source, recalcTimestamp=False)
    options = subset.Options()
    options.notdef_outline = True
    options.name_IDs = ["*"]  # keeps the copyright and licence inside the file, as the OFL asks
    options.layout_features = [*options.layout_features, "tnum"]  # prices use tabular figures
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(job.characters))
    subsetter.subset(font)
    font = instancer.instantiateVariableFont(font, {"wght": job.weights})
    font.flavor = "woff2"
    path = OUTPUT / job.output
    font.save(path)
    return path.stat().st_size


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for job in JOBS:
        print(f"{job.output}: {make(job)} bytes")


if __name__ == "__main__":
    main()
