#!/usr/bin/env python3
"""Build the README screenshot tour with Pillow from the supplied tmp/ images.

Run from the repository root:
    backend/.venv/bin/python scripts/build-readme-gif.py

The source screenshots remain untouched. Timings are slideshow timings, not
measurements of application response time.
"""

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp"
OUTPUT = ROOT / "docs" / "assets"
SIZE = (1600, 910)

# Include every Chat screenshot, including answer-scroll continuations.
# Other sections retain representative frames rather than empty-state repeats.
# Tuple values are screenshot suffix and display duration in milliseconds.
SCENES = (
    (0, 3000),  # Chat landing
    (2, 3000),  # Materials
    (3, 3500),  # Meeting files
    (4, 4000),  # Original and parsed PDF
    (5, 4000),  # Video and transcript
    (6, 4000),  # Slides and parsed text
    (10, 3000),  # Meeting selection
    (11, 4500),  # Cited answer
    (12, 4500),  # Cited answer continuation
    (13, 4000),  # Citation preview
    (14, 4000),  # Meeting summary
    (15, 4500),  # Video-based question and answer
    (16, 4500),  # Video answer continuation and citation
    (17, 4000),  # Timestamped video evidence
    (18, 4500),  # Follow-up answer
    (19, 4500),  # GPT post-training answer
    (20, 4500),  # Post-training answer continuation
    (21, 4500),  # Post-training answer conclusion and sources
    (22, 3500),  # History
    (23, 3500),  # Entities
    (24, 4000),  # Memories
    (25, 4500),  # Additional chat question and answer
    (26, 4500),  # Answer continuation
    (27, 4500),  # Further chat question and answer
    (28, 4500),  # Answer continuation
    (29, 4500),  # Final chat question and answer
    (30, 4500),  # Answer continuation and sources
    (9, 3500),  # Settings
    (31, 3000),  # Generate skill selection
    (32, 3500),  # Generation inputs
    (33, 5000),  # Generated proposal
)

# Both screenshot sizes capture the same app viewport, with different margins.
# Remove desktop shadow, browser tabs and address bar; retain the entire app.
VIEWPORTS = {
    (3076, 1982): (68, 226, 3008, 1898),
    (3164, 2070): (112, 250, 3052, 1922),
}


def main() -> None:
    frames = []
    for suffix, _duration in SCENES:
        name = f"Screenshot {suffix}.png" if suffix else "Screenshot.png"
        with Image.open(SOURCE / name) as source:
            viewport = source.crop(VIEWPORTS[source.size]).convert("RGB")
            frames.append(ImageOps.pad(viewport, SIZE, method=Image.Resampling.LANCZOS))

    # Per-frame palettes retain detail in video portraits and PDF figures.
    # No dithering keeps small interface text and pale backgrounds clean.
    indexed = [
        frame.quantize(
            colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
        for frame in frames
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "meeting-agent-tour.gif"
    indexed[0].save(
        target,
        save_all=True,
        append_images=indexed[1:],
        duration=[duration for _, duration in SCENES],
        loop=0,
        disposal=1,
        optimize=True,
    )
    # An accessible still image also allows readers to inspect without motion.
    still_index = next(index for index, (suffix, _) in enumerate(SCENES) if suffix == 11)
    frames[still_index].save(OUTPUT / "meeting-agent-chat.png", optimize=True)

    with Image.open(target) as animation:
        assert animation.n_frames == len(SCENES)
        assert animation.size == SIZE
        assert animation.info["loop"] == 0
        duration = 0
        for index in range(animation.n_frames):
            animation.seek(index)
            animation.load()
            duration += animation.info["duration"]
        assert duration == sum(duration for _, duration in SCENES)
    print(
        f"{target.relative_to(ROOT)}: {len(SCENES)} frames, {duration / 1000:g}s, "
        f"{target.stat().st_size / 1024 / 1024:.2f} MiB, {SIZE[0]}x{SIZE[1]}"
    )


if __name__ == "__main__":
    main()
